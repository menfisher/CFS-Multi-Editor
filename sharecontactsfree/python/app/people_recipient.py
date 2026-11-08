"""People API helpers for recipient contact import."""

from __future__ import annotations

import base64
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from typing import Any

logger = logging.getLogger(__name__)

import httpx
from google.oauth2.credentials import Credentials
from googleapiclient.errors import HttpError
from googleapiclient.http import BatchHttpRequest

from .config import (
    RECIPIENT_IMPORT_PHOTO_FETCH_WORKERS,
    RECIPIENT_IMPORT_PHOTO_HTTP_BATCH_SIZE,
)
from .people_api import is_rate_limit_error, people_service

ALLOWED_CREATE_KEYS = (
    "addresses",
    "biographies",
    "birthdays",
    "emailAddresses",
    "memberships",
    "names",
    "nicknames",
    "organizations",
    "phoneNumbers",
    "relations",
    "urls",
    "userDefined",
)

_GOOGLE_ADDRESS_STRUCTURED_KEYS = (
    "streetAddress",
    "extendedAddress",
    "poBox",
    "city",
    "region",
    "postalCode",
    "country",
    "countryCode",
)


def _normalize_shared_formatted_address(value: str) -> str:
    text = str(value or "").replace("\r", "\n").strip()
    if not text or "\n" not in text:
        return text
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if len(lines) == 2 and re.search(r",\s*[A-Z]{2}\s+\d{5}", lines[1]):
        return f"{lines[0]}, {lines[1]}"
    return "\n".join(lines)


def _prepare_shared_address_item(item: dict[str, Any]) -> dict[str, Any]:
    """Keep Google structured address fields so recipients get the same split as the owner."""
    next_item = {k: v for k, v in item.items() if k != "metadata"}
    next_item.pop("formattedType", None)

    structured: dict[str, str] = {}
    for key in _GOOGLE_ADDRESS_STRUCTURED_KEYS:
        value = str(next_item.get(key) or "").strip()
        if value:
            structured[key] = value
        next_item.pop(key, None)

    formatted = str(next_item.get("formattedValue") or "").strip()
    if structured:
        street1 = structured.get("streetAddress", "")
        street2 = structured.get("extendedAddress", "")
        city = structured.get("city", "")
        region = structured.get("region", "")
        postal = structured.get("postalCode", "")
        if city and region and postal:
            city_line = f"{city}, {region} {postal}"
        elif city and region:
            city_line = f"{city}, {region}"
        elif city and postal:
            city_line = f"{city} {postal}"
        else:
            city_line = ", ".join(part for part in (city, region, postal) if part)
        rebuilt = "\n".join(part for part in (street1, street2, city_line) if part)
        if rebuilt:
            formatted = rebuilt
    formatted = _normalize_shared_formatted_address(formatted)

    # Prefer owner structured fields; never strip them down to formatted-only.
    # Google often mis-parses formattedValue alone (e.g. "#119" → street "119 ...").
    next_item.update(structured)
    if formatted:
        next_item["formattedValue"] = formatted
    else:
        next_item.pop("formattedValue", None)
    return next_item


def build_shared_group_name(group_name: str) -> str:
    base = str(group_name or "").strip()
    if re.search(r"\(shared\)$", base, re.I):
        return base
    return f"{base} (shared)"


def group_names_equivalent(left: str, right: str) -> bool:
    def variants(name: str) -> set[str]:
        cleaned = str(name or "").strip()
        if not cleaned:
            return set()
        names = {cleaned.casefold()}
        base = re.sub(r"\s*\(shared\)\s*$", "", cleaned, flags=re.I).strip()
        if base:
            names.add(base.casefold())
            names.add(f"{base} (Shared)".casefold())
            names.add(f"{base} (shared)".casefold())
        return names

    return bool(variants(left) & variants(right))


def normalize_person_resource_name(resource_name: str) -> str:
    if not resource_name:
        return resource_name
    return resource_name if resource_name.startswith("people/") else f"people/{resource_name}"


def sanitize_contact_for_create(data: dict[str, Any] | None) -> dict[str, Any]:
    if not data:
        return {}
    clean = {k: deepcopy(data[k]) for k in ALLOWED_CREATE_KEYS if data.get(k)}

    def strip_items(items: list[Any] | None) -> list[Any]:
        stripped: list[Any] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            next_item = {k: v for k, v in item.items() if k != "metadata"}
            stripped.append(next_item)
        return stripped

    for key in list(clean.keys()):
        if isinstance(clean.get(key), list):
            clean[key] = strip_items(clean[key])

    for item in clean.get("names") or []:
        item.pop("displayName", None)
        item.pop("displayNameLastFirst", None)
        item.pop("unstructuredName", None)
    for item in clean.get("phoneNumbers") or []:
        item.pop("canonicalForm", None)
    for item in clean.get("organizations") or []:
        title = str(item.get("title") or "").strip()
        department = str(item.get("department") or "").strip()
        if title and department and title.casefold() == department.casefold():
            item.pop("department", None)
    clean["addresses"] = [
        _prepare_shared_address_item(item)
        for item in clean.get("addresses") or []
        if isinstance(item, dict)
    ]

    birthdays = clean.get("birthdays") or []
    seen: set[str] = set()
    filtered = []
    for item in birthdays:
        date = (item or {}).get("date") or {}
        key = f"{date.get('year', '')}-{date.get('month', '')}-{date.get('day', '')}"
        if not date or key in seen:
            continue
        seen.add(key)
        filtered.append(item)
    if birthdays:
        clean["birthdays"] = filtered

    return clean


def list_shared_contact_groups(creds: Credentials) -> list[dict[str, Any]]:
    """Google contact groups created by share import (name ends with '(shared)')."""
    service = people_service(creds)
    groups: list[dict[str, Any]] = []
    page_token = None
    while True:
        kwargs: dict[str, Any] = {"pageSize": 200}
        if page_token:
            kwargs["pageToken"] = page_token
        data = service.contactGroups().list(**kwargs).execute()
        for group in data.get("contactGroups") or []:
            name = str(group.get("name") or "").strip()
            if not name or not re.search(r"\(shared\)$", name, re.I):
                continue
            resource_name = str(group.get("resourceName") or "").strip()
            if not resource_name:
                continue
            member_count = int(group.get("memberCount") or 0)
            base_name = re.sub(r"\s*\(shared\)$", "", name, flags=re.I).strip() or name
            groups.append(
                {
                    "googleGroupId": resource_name,
                    "displayName": name,
                    "baseName": base_name,
                    "memberCount": member_count,
                }
            )
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return groups


def find_group_by_name(creds: Credentials, group_name: str) -> str | None:
    service = people_service(creds)
    page_token = None
    while True:
        kwargs: dict[str, Any] = {"pageSize": 200}
        if page_token:
            kwargs["pageToken"] = page_token
        data = service.contactGroups().list(**kwargs).execute()
        for g in data.get("contactGroups") or []:
            if g.get("name") == group_name:
                return g.get("resourceName")
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return None


def create_contact_group(creds: Credentials, group_name: str) -> str | None:
    service = people_service(creds)
    name = build_shared_group_name(group_name)
    body = {"contactGroup": {"name": name}}
    try:
        data = service.contactGroups().create(body=body).execute()
        return data.get("resourceName")
    except HttpError as exc:
        if exc.resp.status == 409 or "ALREADY_EXISTS" in str(exc):
            return find_group_by_name(creds, name)
        if is_rate_limit_error(exc):
            existing = find_group_by_name(creds, name)
            if existing:
                return existing
        raise


def update_contact_group_name(
    creds: Credentials, resource_name: str, group_name: str
) -> str | None:
    group_id = str(resource_name or "").strip()
    next_name = build_shared_group_name(group_name)
    if not group_id or not next_name:
        return None
    details = get_group_details(creds, group_id, 1)
    current_name = str(details.get("formattedName") or details.get("name") or "").strip()
    if group_names_equivalent(current_name, next_name) and current_name == next_name:
        return group_id
    body = {
        "contactGroup": {"name": next_name},
        "updateGroupFields": "name",
    }
    etag = str(details.get("etag") or "").strip()
    if etag:
        body["contactGroup"]["etag"] = etag
    service = people_service(creds)
    data = service.contactGroups().update(
        resourceName=group_id,
        body=body,
    ).execute()
    return str(data.get("resourceName") or group_id)


def get_group_details(creds: Credentials, resource_name: str, max_members: int = 2000) -> dict[str, Any]:
    service = people_service(creds)
    return (
        service.contactGroups()
        .get(resourceName=resource_name, maxMembers=max_members)
        .execute()
    )


def group_exists(creds: Credentials, resource_name: str) -> bool | None:
    """True/False when known. None if the lookup failed (do not treat as missing)."""
    if not resource_name:
        return False
    try:
        get_group_details(creds, resource_name, 1)
        return True
    except HttpError as exc:
        status = int(getattr(exc.resp, "status", 0) or 0)
        if status == 404:
            return False
        return None


def group_member_count(creds: Credentials, resource_name: str) -> int | None:
    if not resource_name:
        return None
    try:
        details = get_group_details(creds, resource_name, 2000)
        return len(details.get("memberResourceNames") or [])
    except HttpError:
        return None


def assignment_shared_group_name(member_data: dict[str, Any] | None) -> str:
    """Field is Job Title. Meeting is Company. Empty Job Title falls back to Department."""
    items = [
        item
        for item in (member_data or {}).get("organizations") or []
        if isinstance(item, dict)
    ]
    items.sort(key=lambda item: 0 if (item.get("metadata") or {}).get("primary") else 1)
    for item in items:
        field_name = str(item.get("title") or "").strip() or str(item.get("department") or "").strip()
        meeting_name = str(item.get("name") or "").strip()
        if field_name and meeting_name:
            return f"{field_name} - {meeting_name} (Shared)"
    return ""


def ensure_assignment_shared_group(
    creds: Credentials,
    member_data: dict[str, Any] | None,
    cache: dict[str, str] | None = None,
) -> str:
    """Create or find the recipient Field+Meeting (Shared) label."""
    name = assignment_shared_group_name(member_data)
    if not name:
        return ""
    if cache is not None and name in cache:
        return cache[name]
    try:
        group_id = find_group_by_name(creds, name) or create_contact_group(creds, name) or ""
    except Exception:
        group_id = ""
    if cache is not None:
        cache[name] = group_id
    return group_id


def ensure_assignment_contact_label(
    creds: Credentials,
    recipient_person_id: str,
    member_data: dict[str, Any] | None,
    cache: dict[str, str] | None = None,
) -> bool:
    extra_group_id = ensure_assignment_shared_group(creds, member_data, cache)
    if not extra_group_id:
        return False
    return ensure_contact_in_group(creds, recipient_person_id, extra_group_id)


def prepare_contact_person(
    member_data: dict[str, Any],
    group_id: str,
    extra_group_ids: list[str] | None = None,
) -> dict[str, Any]:
    payload = sanitize_contact_for_create(member_data)
    memberships = [
        {"contactGroupMembership": {"contactGroupResourceName": group_id}}
    ]
    seen = {str(group_id or "").strip()}
    for extra in extra_group_ids or []:
        extra_id = str(extra or "").strip()
        if extra_id and extra_id not in seen:
            memberships.append(
                {"contactGroupMembership": {"contactGroupResourceName": extra_id}}
            )
            seen.add(extra_id)
    payload["memberships"] = memberships
    return payload


def create_contact_in_group(
    creds: Credentials,
    member_data: dict[str, Any],
    group_id: str,
    *,
    skip_photo: bool = True,
) -> str | None:
    if not member_data:
        return None
    service = people_service(creds)
    extra = ensure_assignment_shared_group(creds, member_data)
    payload = prepare_contact_person(
        member_data, group_id, extra_group_ids=[extra] if extra else None
    )
    data = service.people().createContact(body=payload).execute()
    resource_name = data.get("resourceName")
    if resource_name and not skip_photo:
        set_contact_photo(creds, resource_name, member_data)
    return resource_name


BATCH_CREATE_READ_MASK = (
    "names,emailAddresses,phoneNumbers,memberships,photos"
)


def batch_create_contacts_in_group(
    creds: Credentials,
    indexed_members: list[tuple[int, dict[str, Any]]],
    group_id: str,
) -> dict[int, str | None]:
    """Create up to 200 contacts in one People API call.

    Returns a map of member index -> created resource name (or None on failure).
    """
    if not indexed_members:
        return {}

    if len(indexed_members) == 1:
        idx, member = indexed_members[0]
        try:
            return {idx: create_contact_in_group(creds, member, group_id, skip_photo=True)}
        except Exception:
            return {idx: None}

    service = people_service(creds)
    cache: dict[str, str] = {}
    contacts = []
    for _, member in indexed_members:
        extra = ensure_assignment_shared_group(creds, member, cache)
        contacts.append(
            {
                "contactPerson": prepare_contact_person(
                    member, group_id, extra_group_ids=[extra] if extra else None
                )
            }
        )
    try:
        data = (
            service.people()
            .batchCreateContacts(
                body={"contacts": contacts, "readMask": BATCH_CREATE_READ_MASK}
            )
            .execute()
        )
        created = data.get("createdPeople") or []
        result: dict[int, str | None] = {}
        for i, (idx, _) in enumerate(indexed_members):
            if i < len(created):
                person = (created[i] or {}).get("person") or {}
                result[idx] = person.get("resourceName")
            else:
                result[idx] = None
        return result
    except HttpError as exc:
        if is_rate_limit_error(exc):
            raise
        if len(indexed_members) > 10:
            mid = len(indexed_members) // 2
            left = batch_create_contacts_in_group(
                creds, indexed_members[:mid], group_id
            )
            right = batch_create_contacts_in_group(
                creds, indexed_members[mid:], group_id
            )
            return {**left, **right}

        result = {}
        for idx, member in indexed_members:
            try:
                result[idx] = create_contact_in_group(
                    creds, member, group_id, skip_photo=True
                )
            except Exception:
                result[idx] = None
        return result


def _photo_url_from_entry(photo: Any) -> str:
    if not isinstance(photo, dict):
        return ""
    return str(photo.get("url") or "").strip()


def contact_photo_url(member_data: dict[str, Any] | None) -> str:
    """Prefer the owner's primary custom photo over Google's default avatar."""
    if not member_data:
        return ""
    photos = [photo for photo in (member_data.get("photos") or []) if isinstance(photo, dict)]
    if not photos:
        return ""

    def _is_default(photo: dict[str, Any]) -> bool:
        metadata = photo.get("metadata") if isinstance(photo.get("metadata"), dict) else {}
        return bool(metadata.get("default"))

    def _is_primary(photo: dict[str, Any]) -> bool:
        metadata = photo.get("metadata") if isinstance(photo.get("metadata"), dict) else {}
        return bool(metadata.get("primary"))

    for photo in photos:
        url = _photo_url_from_entry(photo)
        if url and _is_primary(photo) and not _is_default(photo):
            return url
    for photo in photos:
        url = _photo_url_from_entry(photo)
        if url and not _is_default(photo):
            return url
    for photo in photos:
        url = _photo_url_from_entry(photo)
        if url:
            return url
    return ""


def has_contact_photo(member_data: dict[str, Any] | None) -> bool:
    return bool(contact_photo_url(member_data))


def _is_placeholder_photo_url(url: str) -> bool:
    """Google's empty silhouette is not a custom contact photo."""
    text = str(url or "").strip().lower()
    if not text:
        return True
    return "default-user" in text or "/a/default" in text


def has_custom_contact_photo(member_data: dict[str, Any] | None) -> bool:
    """True when the contact has a real picture, not Google's default avatar.

    Contacts UI shows the CONTACT photo. A linked Google PROFILE picture does
    not count — and Google often omits metadata.default on default-user URLs,
    which made bulk import skip hundreds of uploads.
    """
    if not member_data:
        return False
    photos = [photo for photo in (member_data.get("photos") or []) if isinstance(photo, dict)]
    if not photos:
        return False

    def _is_default(photo: dict[str, Any]) -> bool:
        metadata = photo.get("metadata") if isinstance(photo.get("metadata"), dict) else {}
        if metadata.get("default"):
            return True
        return _is_placeholder_photo_url(_photo_url_from_entry(photo))

    def _is_primary(photo: dict[str, Any]) -> bool:
        metadata = photo.get("metadata") if isinstance(photo.get("metadata"), dict) else {}
        return bool(metadata.get("primary"))

    def _source_type(photo: dict[str, Any]) -> str:
        metadata = photo.get("metadata") if isinstance(photo.get("metadata"), dict) else {}
        source = metadata.get("source") if isinstance(metadata.get("source"), dict) else {}
        return str(source.get("type") or "").strip().upper()

    contact_photos = [photo for photo in photos if _source_type(photo) == "CONTACT"]
    to_check = contact_photos or photos
    for photo in to_check:
        url = _photo_url_from_entry(photo)
        if url and _is_primary(photo) and not _is_default(photo):
            return True
    for photo in to_check:
        url = _photo_url_from_entry(photo)
        if url and not _is_default(photo):
            return True
    return False


def get_people_photos_batch(
    creds: Credentials, resource_names: list[str]
) -> dict[str, dict[str, Any]]:
    """Fetch only photos for recipient contacts (Retry missing-photo check)."""
    names = [normalize_person_resource_name(n) for n in resource_names]
    names = [n for n in names if n]
    if not names:
        return {}
    service = people_service(creds)
    result: dict[str, dict[str, Any]] = {}
    chunk_size = 50
    for i in range(0, len(names), chunk_size):
        chunk = names[i : i + chunk_size]
        data = (
            service.people()
            .getBatchGet(resourceNames=chunk, personFields="photos")
            .execute()
        )
        for person in data.get("responses") or []:
            p = person.get("person")
            if p and p.get("resourceName"):
                result[str(p["resourceName"])] = p
    return result


def get_people_contacts_batch(
    creds: Credentials, resource_names: list[str], *, strict: bool = False
) -> dict[str, dict[str, Any]]:
    """Fetch identity fields for recipient contacts (duplicate detection).

    The People API returns transient 502/429s on large reads. Silently dropping
    those chunks makes a contact look unknown when it is merely unread, so
    retry, and with strict=True raise rather than return a partial answer.
    """
    names = [normalize_person_resource_name(n) for n in resource_names]
    names = [n for n in names if n]
    if not names:
        return {}
    service = people_service(creds)
    result: dict[str, dict[str, Any]] = {}
    chunk_size = 50
    for i in range(0, len(names), chunk_size):
        chunk = names[i : i + chunk_size]
        data = None
        for attempt in range(5):
            try:
                data = (
                    service.people()
                    .getBatchGet(
                        resourceNames=chunk,
                        personFields="names,emailAddresses,phoneNumbers",
                    )
                    .execute()
                )
                break
            except HttpError:
                if attempt == 4:
                    if strict:
                        raise
                    data = None
                    break
                time.sleep(1.5 * (attempt + 1))
        if data is None:
            continue
        for person in data.get("responses") or []:
            p = person.get("person")
            if p and p.get("resourceName"):
                result[str(p["resourceName"])] = p
    return result


def build_photo_url_candidates(url: str) -> list[str]:
    """Build Google People photo URL variants (ported from gsWebApp buildPhotoUrlCandidates_)."""
    raw = str(url or "").strip()
    if not raw:
        return []

    out = [raw]
    if "?" in raw:
        if re.search(r"([?&])sz=\d+", raw, re.I):
            out.append(re.sub(r"([?&])sz=\d+", r"\1sz=0", raw, flags=re.I))
            out.append(re.sub(r"([?&])sz=\d+", r"\1sz=2048", raw, flags=re.I))
        else:
            out.append(raw + "&sz=0")
            out.append(raw + "&sz=2048")
    else:
        out.append(raw + "?sz=0")
        out.append(raw + "?sz=2048")

    if re.search(r"=s\d+", raw, re.I):
        out.append(re.sub(r"=s\d+(-[a-z0-9]+)?", "=s0", raw, flags=re.I))
        out.append(re.sub(r"=s\d+(-[a-z0-9]+)?", "=s2048", raw, flags=re.I))
    else:
        out.append(raw + "=s0")
        out.append(raw + "=s2048")

    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in out:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        ordered.append(candidate)

    # Prefer full-resolution variants before the default thumbnail URL.
    def rank(candidate: str) -> tuple[int, int]:
        hi = 0
        if "2048" in candidate or "=s2048" in candidate:
            hi = 0
        elif "sz=0" in candidate or "=s0" in candidate:
            hi = 1
        elif re.search(r"sz=\d+", candidate, re.I) or re.search(r"=s\d+", candidate, re.I):
            hi = 3
        else:
            hi = 2
        return (hi, len(candidate))

    return sorted(ordered, key=rank)


def _is_full_resolution_photo_url(url: str) -> bool:
    return bool(re.search(r"(?:sz=2048|=s2048|sz=0(?!\d)|=s0(?!\d))", url, re.I))


def _fetch_url_bytes(
    url: str, token: str, *, client: httpx.Client | None = None
) -> bytes | None:
    headers = {"Authorization": f"Bearer {token}"}
    try:
        if client is None:
            with httpx.Client(timeout=15.0, follow_redirects=True) as owned:
                resp = owned.get(url, headers=headers)
        else:
            resp = client.get(url, headers=headers)
        if 200 <= resp.status_code < 300 and resp.content:
            return resp.content
    except httpx.HTTPError:
        pass
    return None


def fetch_photo_bytes(
    url: str, token: str, *, client: httpx.Client | None = None
) -> str | None:
    """Fetch photo bytes at the highest resolution available.

    Google stores photos[].url as a small (=s100) thumbnail. Request the
    full-resolution variants first and stop at the first one that works so a
    726-photo import does not download 4–6 URL variants per contact.
    """
    if not url or not token:
        return None

    fallback: bytes | None = None
    for candidate in build_photo_url_candidates(url):
        content = _fetch_url_bytes(candidate, token, client=client)
        if not content:
            continue
        if _is_full_resolution_photo_url(candidate):
            return base64.b64encode(content).decode("ascii")
        if fallback is None or len(content) > len(fallback):
            fallback = content
    if not fallback:
        return None
    return base64.b64encode(fallback).decode("ascii")


def fetch_photo_bytes_for_member(
    member_data: dict[str, Any] | None,
    *,
    owner_token: str | None = None,
    recipient_token: str | None = None,
    client: httpx.Client | None = None,
) -> str | None:
    """Download shared contact photo bytes (owner URLs need the owner OAuth token)."""
    url = contact_photo_url(member_data)
    if not url:
        return None
    for token in (owner_token, recipient_token):
        if not token:
            continue
        payload = fetch_photo_bytes(url, token, client=client)
        if payload:
            return payload
    return None


def prefetch_photo_bytes(
    indexed_members: list[tuple[int, dict[str, Any]]],
    token: str,
    *,
    owner_token: str | None = None,
    max_workers: int | None = None,
) -> dict[int, str | None]:
    """Download photo bytes in parallel for a slice of contacts."""
    if not indexed_members:
        return {}

    workers = max(1, min(max_workers or RECIPIENT_IMPORT_PHOTO_FETCH_WORKERS, len(indexed_members)))
    timeout = httpx.Timeout(15.0, connect=8.0)
    limits = httpx.Limits(
        max_keepalive_connections=workers,
        max_connections=max(workers * 2, 8),
    )

    def _one(item: tuple[int, dict[str, Any]], client: httpx.Client) -> tuple[int, str | None]:
        idx, member = item
        return idx, fetch_photo_bytes_for_member(
            member,
            owner_token=owner_token,
            recipient_token=token,
            client=client,
        )

    with httpx.Client(timeout=timeout, follow_redirects=True, limits=limits) as client:
        if len(indexed_members) == 1:
            idx, payload = _one(indexed_members[0], client)
            return {idx: payload}

        result: dict[int, str | None] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_one, item, client): item[0] for item in indexed_members
            }
            for future in as_completed(futures):
                idx, payload = future.result()
                result[idx] = payload
        return result


def recipient_person_exists(creds: Credentials, resource_name: str) -> bool:
    normalized = normalize_person_resource_name(resource_name)
    if not normalized:
        return False
    service = people_service(creds)
    try:
        service.people().get(resourceName=normalized, personFields="metadata").execute()
        return True
    except HttpError as exc:
        if exc.resp is not None and exc.resp.status == 404:
            return False
        return True


def upload_contact_photo_bytes(
    creds: Credentials,
    resource_name: str,
    photo_bytes_b64: str,
    *,
    verify_exists: bool = True,
) -> bool:
    if not photo_bytes_b64:
        return False
    if verify_exists and not recipient_person_exists(creds, resource_name):
        return False
    service = people_service(creds)
    normalized = normalize_person_resource_name(resource_name)
    body = {"photoBytes": photo_bytes_b64}
    try:
        service.people().updateContactPhoto(resourceName=normalized, body=body).execute()
        return True
    except Exception:
        # Treat timeouts / 404 / quota blips as soft failures so bulk refresh can continue.
        return False


def batch_upload_contact_photos(
    creds: Credentials,
    uploads: list[tuple[int, str, str]],
) -> dict[int, bool]:
    """Upload multiple contact photos via HTTP batch (one round trip per chunk)."""
    if not uploads:
        return {}

    if len(uploads) == 1:
        idx, resource_name, photo_bytes = uploads[0]
        return {
            idx: upload_contact_photo_bytes(
                creds, resource_name, photo_bytes, verify_exists=False
            )
        }

    service = people_service(creds)
    chunk_size = max(1, min(RECIPIENT_IMPORT_PHOTO_HTTP_BATCH_SIZE, 200))
    outcomes: dict[int, bool] = {}

    for offset in range(0, len(uploads), chunk_size):
        chunk = uploads[offset : offset + chunk_size]
        pending: dict[str, tuple[int, str, str]] = {}

        def _callback(request_id: str, _response: Any, exception: Exception | None) -> None:
            idx, resource_name, photo_bytes = pending[request_id]
            if exception is None:
                outcomes[idx] = True
                return
            outcomes[idx] = upload_contact_photo_bytes(
                creds, resource_name, photo_bytes, verify_exists=False
            )

        batch = BatchHttpRequest(callback=_callback)
        for idx, resource_name, photo_bytes in chunk:
            request_id = str(idx)
            pending[request_id] = (idx, resource_name, photo_bytes)
            normalized = normalize_person_resource_name(resource_name)
            batch.add(
                service.people().updateContactPhoto(
                    resourceName=normalized,
                    body={"photoBytes": photo_bytes},
                ),
                request_id=request_id,
            )
        try:
            batch.execute()
        except Exception:
            for idx, resource_name, photo_bytes in chunk:
                if idx not in outcomes:
                    outcomes[idx] = upload_contact_photo_bytes(
                        creds, resource_name, photo_bytes, verify_exists=False
                    )

        for idx, resource_name, photo_bytes in chunk:
            if idx not in outcomes:
                outcomes[idx] = upload_contact_photo_bytes(
                    creds, resource_name, photo_bytes, verify_exists=False
                )

    return outcomes


def set_contact_photo(
    creds: Credentials,
    resource_name: str,
    member_data: dict[str, Any],
    *,
    photo_bytes_b64: str | None = None,
    owner_token: str | None = None,
) -> bool:
    payload = photo_bytes_b64
    if not payload:
        payload = fetch_photo_bytes_for_member(
            member_data,
            owner_token=owner_token,
            recipient_token=creds.token or "",
        )
    if not payload:
        return False
    return upload_contact_photo_bytes(creds, resource_name, payload)


def get_person_etag(creds: Credentials, resource_name: str) -> str | None:
    normalized = normalize_person_resource_name(resource_name)
    if not normalized:
        return None
    service = people_service(creds)
    try:
        data = (
            service.people()
            .get(resourceName=normalized, personFields="metadata")
            .execute()
        )
        return str(data.get("etag") or "") or None
    except HttpError:
        return None


def get_person_etags_batch(creds: Credentials, resource_names: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in resource_names:
        normalized = normalize_person_resource_name(name)
        if not normalized:
            continue
        etag = get_person_etag(creds, normalized)
        if etag:
            out[normalized] = etag
    return out


def update_contact(
    creds: Credentials,
    recipient_person_id: str,
    member_data: dict[str, Any],
    etag: str,
) -> bool:
    from .services.contact_hash import SYNC_WRITABLE_FIELDS, build_update_payload

    normalized = normalize_person_resource_name(recipient_person_id)
    if not normalized or not etag:
        return False
    service = people_service(creds)
    body = build_update_payload(member_data)
    body["resourceName"] = normalized
    body["etag"] = etag
    try:
        service.people().updateContact(
            resourceName=normalized,
            updatePersonFields=",".join(SYNC_WRITABLE_FIELDS),
            body=body,
        ).execute()
        return True
    except HttpError:
        return False


def delete_contact(creds: Credentials, recipient_person_id: str) -> bool:
    normalized = normalize_person_resource_name(recipient_person_id)
    if not normalized:
        return False
    service = people_service(creds)
    try:
        service.people().deleteContact(resourceName=normalized).execute()
        return True
    except HttpError:
        return False


def delete_contact_photo(creds: Credentials, recipient_person_id: str) -> bool:
    normalized = normalize_person_resource_name(recipient_person_id)
    if not normalized:
        return False
    service = people_service(creds)
    try:
        service.people().deleteContactPhoto(resourceName=normalized).execute()
        return True
    except HttpError:
        return False


def ensure_contact_in_group(
    creds: Credentials,
    recipient_person_id: str,
    group_id: str,
) -> bool:
    normalized = normalize_person_resource_name(recipient_person_id)
    if not normalized or not group_id:
        return False
    service = people_service(creds)
    try:
        service.contactGroups().members().modify(
            resourceName=group_id,
            body={"resourceNamesToAdd": [normalized]},
        ).execute()
        return True
    except HttpError:
        return False


def remove_contact_from_group(
    creds: Credentials,
    recipient_person_id: str,
    group_id: str,
) -> bool:
    """Remove a recipient contact from a Google contact group."""
    normalized = normalize_person_resource_name(recipient_person_id)
    if not normalized or not group_id:
        return False
    service = people_service(creds)
    try:
        service.contactGroups().members().modify(
            resourceName=group_id,
            body={"resourceNamesToRemove": [normalized]},
        ).execute()
        return True
    except HttpError:
        return False


_ASSIGNMENT_LABEL_RE = re.compile(r"^.+ - .+ \(shared\)$", re.I)


def list_group_names(creds: Credentials) -> dict[str, str]:
    """Map every contact group resourceName to its name for this account."""
    service = people_service(creds)
    names: dict[str, str] = {}
    page_token = None
    while True:
        kwargs: dict[str, Any] = {"pageSize": 200}
        if page_token:
            kwargs["pageToken"] = page_token
        data = service.contactGroups().list(**kwargs).execute()
        for group in data.get("contactGroups") or []:
            resource_name = str(group.get("resourceName") or "").strip()
            if resource_name:
                names[resource_name] = str(group.get("name") or "").strip()
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return names


def remove_outdated_assignment_labels(
    creds: Credentials,
    recipient_person_id: str,
    current_label_name: str,
    protected_group_ids: set[str],
    group_names: dict[str, str],
) -> int:
    """Remove 'Field - Meeting (Shared)' labels this contact no longer qualifies for.

    Used when the previously stored label is unknown (rows created before the
    last_assignment_label column existed). Reads the recipient contact's real
    memberships, so it also catches e.g. 'Deleted - 2026 (Shared)' after a restore.
    Share import groups are protected and never touched.
    """
    normalized = normalize_person_resource_name(recipient_person_id)
    if not normalized:
        return 0
    current = str(current_label_name or "").strip().casefold()
    service = people_service(creds)
    try:
        person = (
            service.people().get(resourceName=normalized, personFields="memberships").execute()
        )
    except HttpError:
        return 0
    removed = 0
    for membership in person.get("memberships") or []:
        group_id = str(
            (membership.get("contactGroupMembership") or {}).get("contactGroupResourceName")
            or ""
        ).strip()
        if not group_id or group_id in protected_group_ids:
            continue
        name = group_names.get(group_id, "")
        if not name or not _ASSIGNMENT_LABEL_RE.match(name):
            continue
        if name.casefold() == current:
            continue
        if remove_contact_from_group(creds, normalized, group_id):
            removed += 1
    return removed


def remove_stale_assignment_label(
    creds: Credentials,
    recipient_person_id: str,
    old_label_name: str,
    new_label_name: str,
) -> bool:
    """Remove an outdated Field+Meeting (Shared) label from a recipient contact.

    Called after a contact's CFS assignment changes (e.g. moved from
    'Deleted - 2026' back to 'Contacts - LA North'). Looks up the old label
    group by name and removes the contact from it so the stale label does not
    linger in the recipient account.

    Returns True when the label was removed (or was already absent).
    Returns False when the removal failed or the group could not be found.
    Does nothing and returns True when old and new labels are equal.
    """
    old = str(old_label_name or "").strip()
    new = str(new_label_name or "").strip()
    if not old or old == new:
        return True
    try:
        old_group_id = find_group_by_name(creds, old)
        if not old_group_id:
            return True
        return remove_contact_from_group(creds, recipient_person_id, old_group_id)
    except Exception:
        logger.exception(
            "Failed to remove stale assignment label %r from %s",
            old,
            recipient_person_id,
        )
        return False

