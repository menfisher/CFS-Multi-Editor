from __future__ import annotations

import time
from typing import Any

import google_auth_httplib2
import httplib2
import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .config import PEOPLE_API_HTTP_TIMEOUT

GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


def is_rate_limit_error(exc: Exception) -> bool:
    """True when an exception is a Google rate-limit / quota-exceeded error (HTTP 429)."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status == 429:
        return True
    text = str(exc).lower()
    return (
        "quota exceeded" in text
        or "rate_limit_exceeded" in text
        or "resource_exhausted" in text
        or "rate limit" in text
        or "ratelimitexceeded" in text
    )


def people_service(creds: Credentials):
    # Wrap the OAuth credentials in an httplib2 transport that has a hard socket
    # timeout. The default googleapiclient transport has NO timeout, so a single
    # stalled Google connection (e.g. a hung updateContactPhoto during the photo
    # phase) would block the background push worker thread indefinitely and wedge
    # the whole import queue. With a timeout the call raises instead, and the
    # worker requeues/retries the job.
    authed_http = google_auth_httplib2.AuthorizedHttp(
        creds, http=httplib2.Http(timeout=PEOPLE_API_HTTP_TIMEOUT)
    )
    return build(
        "people",
        "v1",
        http=authed_http,
        cache_discovery=False,
        static_discovery=True,
    )


def _profile_from_userinfo(creds: Credentials) -> dict[str, Any]:
    if creds.expired:
        if not creds.refresh_token:
            raise RuntimeError("Google sign-in expired. Please sign in again.")
        try:
            creds.refresh(GoogleAuthRequest())
        except Exception as exc:
            raise RuntimeError("Google sign-in expired. Please sign in again.") from exc
    token = str(creds.token or "").strip()
    if not token:
        raise RuntimeError("Google access token is missing.")
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {token}"},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Could not read Google profile ({resp.status_code}).")
    info = resp.json()
    email = str(info.get("email") or "").strip()
    user_id = str(info.get("id") or "").strip()
    return {
        "resourceName": f"people/{user_id}" if user_id else "",
        "displayName": email,
        "url": str(info.get("picture") or ""),
        "email": email,
    }


def get_profile(creds: Credentials) -> dict[str, Any]:
    try:
        service = people_service(creds)
        person = (
            service.people()
            .get(resourceName="people/me", personFields="names,photos,emailAddresses")
            .execute()
        )
    except HttpError as exc:
        # 401/403 = auth problem; 429 = People API read quota exhausted. In every one of
        # these cases the OAuth2 userinfo endpoint (a separate service with its own quota)
        # can still return the signed-in profile, so the page loads without burning — or
        # being blocked by — People API "critical read" quota.
        if exc.resp.status not in {401, 403, 429}:
            raise
        return _profile_from_userinfo(creds)
    names = person.get("names") or [{}]
    photos = person.get("photos") or [{}]
    emails = person.get("emailAddresses") or [{}]
    email = str(emails[0].get("value") or "").strip()
    return {
        "resourceName": person.get("resourceName", ""),
        "displayName": email or names[0].get("displayName", ""),
        "url": photos[0].get("url", ""),
        "email": email,
    }


def list_contact_groups(creds: Credentials) -> list[dict[str, Any]]:
    service = people_service(creds)
    groups: list[dict[str, Any]] = []
    page_token = None
    while True:
        req = service.contactGroups().list(pageSize=100)
        if page_token:
            req = service.contactGroups().list(pageSize=100, pageToken=page_token)
        data = req.execute()
        for g in data.get("contactGroups") or []:
            name = g.get("name") or ""
            if isinstance(name, dict):
                name = name.get("name", "")
            group_name = str(name)
            if group_name.endswith(" (shared)"):
                continue
            if g.get("groupType") != "USER_CONTACT_GROUP":
                continue
            groups.append(
                {
                    "resourceName": g.get("resourceName", ""),
                    "groupType": g.get("groupType", ""),
                    "name": group_name,
                    "memberCount": g.get("memberCount", 0),
                    "members": [],
                    "shared": [],
                }
            )
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    groups.sort(key=lambda x: (x.get("name") or "").lower())
    return groups


def get_group_member_ids(creds: Credentials, resource_name: str, max_members: int = 2000) -> list[str]:
    service = people_service(creds)
    data = (
        service.contactGroups()
        .get(resourceName=resource_name, maxMembers=max_members)
        .execute()
    )
    return list(data.get("memberResourceNames") or [])


def get_people_batch(creds: Credentials, resource_names: list[str]) -> dict[str, dict[str, Any]]:
    if not resource_names:
        return {}
    service = people_service(creds)
    fields = (
        "names,emailAddresses,phoneNumbers,addresses,organizations,"
        "biographies,birthdays,nicknames,photos,relations,urls,userDefined,"
        "memberships"
    )
    result: dict[str, dict[str, Any]] = {}
    chunk_size = 50
    for i in range(0, len(resource_names), chunk_size):
        chunk = resource_names[i : i + chunk_size]
        # A dropped chunk here silently shrinks the owner's contact list, and
        # callers treat anything absent from that list as no longer shared.
        for attempt in range(4):
            try:
                data = (
                    service.people()
                    .getBatchGet(
                        resourceNames=chunk,
                        personFields=fields,
                    )
                    .execute()
                )
                break
            except HttpError:
                if attempt == 3:
                    raise
                time.sleep(1.5 * (attempt + 1))
        for person in data.get("responses") or []:
            p = person.get("person")
            if p and p.get("resourceName"):
                result[p["resourceName"]] = p
    return result
