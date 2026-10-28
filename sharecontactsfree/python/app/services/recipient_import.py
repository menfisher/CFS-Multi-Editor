"""Port of createSharedGroups from gsWebApp.gs — import shared contacts into recipient Google account."""

from __future__ import annotations

import logging
import threading
import time
from copy import deepcopy
from typing import Any, Callable

from google.oauth2.credentials import Credentials

from .. import db
from ..google_auth import clear_recipient_connection, is_oauth_refresh_error, owner_credentials
from ..people_api import is_rate_limit_error
from ..config import (
    RECIPIENT_IMPORT_BATCH_SIZE,
    RECIPIENT_IMPORT_CREATE_BATCH_SIZE,
    RECIPIENT_IMPORT_INCLUDE_PHOTOS,
    RECIPIENT_IMPORT_LARGE_GROUP_THRESHOLD,
    RECIPIENT_IMPORT_MAX_OPS_PER_LOAD,
    RECIPIENT_IMPORT_MAX_RETRIES,
    RECIPIENT_IMPORT_PHOTO_BATCH_SIZE,
    RECIPIENT_IMPORT_PROGRESS_BATCH_SIZE,
    RECIPIENT_IMPORT_TIME_BUDGET_MS,
)
from ..people_recipient import (
    batch_create_contacts_in_group,
    batch_upload_contact_photos,
    build_shared_group_name,
    create_contact_group,
    create_contact_in_group,
    find_group_by_name,
    get_group_details,
    get_people_contacts_batch,
    get_people_photos_batch,
    group_exists,
    group_member_count,
    has_contact_photo,
    has_custom_contact_photo,
    normalize_person_resource_name,
    prefetch_photo_bytes,
    ensure_contact_in_group,
)

logger = logging.getLogger(__name__)

_user_locks: dict[str, threading.Lock] = {}
_lock_guard = threading.Lock()


def _lock_for(email: str) -> threading.Lock:
    with _lock_guard:
        if email not in _user_locks:
            _user_locks[email] = threading.Lock()
        return _user_locks[email]


def _contact_group_resource(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("contactGroups/"):
        return text
    return ""


def _clear_recipient_import_state(group: dict[str, Any]) -> None:
    group["created"] = ""
    group["importCursor"] = 0
    group["actualCount"] = 0
    group["skippedCount"] = 0
    group["photoDoneCount"] = 0
    for key in (
        "retryIndexes",
        "retryAttempts",
        "skippedIndexes",
        "photoPendingIndexes",
        "photoRetryIndexes",
        "photoRetryAttempts",
        "photoTargets",
        "photoTargetCount",
        "photoNeedsRecipientScan",
        "importPhase",
        "repairCheckedActual",
    ):
        group.pop(key, None)


def _drop_stale_mappings_for_group(group: dict[str, Any]) -> None:
    """Forget recipient person ids that belonged to a deleted/empty group."""
    share_id = str(group.get("shareId") or "")
    owner = str(group.get("owner") or "")
    resource_name = str(group.get("resourceName") or "")
    recipient_email = str(group.get("recipientEmail") or "")
    if not share_id or not owner or not recipient_email:
        return
    db.delete_sync_mapping_rows(share_id, owner, resource_name, recipient_email)
    group["importCursor"] = 0
    group["importPhase"] = "contacts"


def _forget_mappings_missing_from_group(
    creds: Credentials, group: dict[str, Any], group_id: str
) -> int:
    """Drop mappings whose recipient contact is not in the current group."""
    share_id = str(group.get("shareId") or "")
    owner = str(group.get("owner") or "")
    resource_name = str(group.get("resourceName") or "")
    recipient_email = str(group.get("recipientEmail") or "")
    if not share_id or not owner or not recipient_email or not group_id:
        return 0
    try:
        details = get_group_details(creds, group_id, 5000)
    except Exception:
        return 0
    members = {
        rn
        for rn in (
            normalize_person_resource_name(m)
            for m in (details.get("memberResourceNames") or [])
        )
        if rn
    }
    by_owner, _ = db.load_sync_mappings(
        share_id, owner, resource_name, recipient_email
    )
    gone = [
        owner_person_id
        for owner_person_id, mapping in by_owner.items()
        if normalize_person_resource_name(
            str(mapping.get("recipient_person_id") or "")
        )
        not in members
    ]
    if not gone:
        return 0
    db.delete_sync_mapping_rows(
        share_id, owner, resource_name, recipient_email, gone
    )
    return len(gone)


def _ensure_recipient_group(
    creds: Credentials, group: dict[str, Any], name: str
) -> str | None:
    group_id = _contact_group_resource(group.get("created"))
    exists = group_exists(creds, group_id) if group_id else False
    if group_id and exists is False:
        # The label the recipient had was deleted. Leftover mappings make the
        # importer skip contact creates and "upload photos" to people that 404.
        _drop_stale_mappings_for_group(group)
        _clear_recipient_import_state(group)
        group_id = ""

    if not group_id:
        shared_name = build_shared_group_name(name)
        group_id = find_group_by_name(creds, shared_name)
        if not group_id:
            group_id = create_contact_group(creds, name)
        if not group_id:
            return None
        group["created"] = group_id
        if int(group.get("importCursor") or 0) > 0 and group_exists(creds, group_id) is False:
            group["importCursor"] = 0
    return group_id


def _sync_mapping_count(group: dict[str, Any]) -> int:
    share_id = str(group.get("shareId") or "")
    owner = str(group.get("owner") or "")
    resource_name = str(group.get("resourceName") or "")
    recipient_email = str(group.get("recipientEmail") or "")
    if not share_id or not owner or not recipient_email:
        return 0
    by_owner, _ = db.load_sync_mappings(
        share_id, owner, resource_name, recipient_email
    )
    return len(by_owner)


def _contacts_import_complete(
    group: dict[str, Any],
    members_len: int,
    start: int,
    retry_indexes: list[int],
) -> bool:
    if retry_indexes:
        return False
    if members_len <= 0:
        return True
    if start >= members_len:
        return True
    return _sync_mapping_count(group) >= members_len


def _contacts_are_imported(group: dict[str, Any]) -> bool:
    """True when contact creates are finished. Mid-import Queued is not finished."""
    member_count = int(group.get("memberCount") or 0)
    actual = int(group.get("actualCount") or 0)
    # A replaced/empty recipient label must not be treated as done just because
    # we still have mappings (or a Photos status) from the deleted group.
    if member_count > 0 and actual < member_count:
        return False
    import_phase = str(group.get("importPhase") or "")
    if import_phase in {"photos", "done"}:
        return True
    status = str(group.get("status") or "")
    if status.startswith(("Shared", "Photos", "Importing photos")):
        return True
    cursor = int(group.get("importCursor") or 0)
    return member_count > 0 and cursor >= member_count


def _reconcile_import_progress(
    creds: Credentials,
    group: dict[str, Any],
    group_id: str,
    members_data_len: int,
) -> int:
    """Resume contact import without rewinding a lagging Google membership count."""
    actual = group_member_count(creds, group_id)
    mapped = _sync_mapping_count(group)
    if actual is not None and mapped > actual:
        # Leftover mappings from a deleted group (or 404s) make mapped jump
        # ahead of real membership, so the importer skips the rest.
        _forget_mappings_missing_from_group(creds, group, group_id)
        mapped = _sync_mapping_count(group)
        if actual == 0 and mapped > 0:
            _drop_stale_mappings_for_group(group)
            mapped = 0
    start = max(0, int(group.get("importCursor") or 0), mapped)
    if actual is not None:
        group["actualCount"] = actual
        start = max(start, actual)
    if members_data_len > 0:
        start = min(start, members_data_len)
    group["importCursor"] = start
    return start


def _dedupe_shared_groups(shared_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    index_by_key: dict[str, int] = {}
    for group in shared_groups:
        if not group:
            continue
        key = f"{group.get('owner') or ''}|{group.get('resourceName') or ''}"
        if key not in index_by_key:
            index_by_key[key] = len(deduped)
            deduped.append(deepcopy(group))
            continue
        idx = index_by_key[key]
        current = deduped[idx]
        current.update(group)
        current["created"] = _contact_group_resource(
            group.get("created") or current.get("created")
        )
        current["importCursor"] = group.get("importCursor") or current.get("importCursor") or 0
    return deduped


def _filter_result(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for group in groups:
        if not group or not group.get("shareId"):
            continue
        if group.get("staleRemove"):
            continue
        if group.get("status") == "Missing data" and not group.get("created"):
            continue
        out.append(group)
    return out


def _queue_unmapped_member_retries(
    group: dict[str, Any],
    members_data: list[dict[str, Any]],
    retry_indexes: list[int],
) -> int:
    """Queue already-passed contacts that never received a recipient sync mapping.

    Only indexes below importCursor are queued (genuine gaps the sequential importer
    already moved past). Indexes at/above the cursor are left for the fast
    batchCreateContacts path — queuing them here would force slow one-at-a-time creates,
    which is what made reset/relink re-imports crawl.
    """
    share_id = str(group.get("shareId") or "")
    owner = str(group.get("owner") or "")
    resource_name = str(group.get("resourceName") or "")
    recipient_email = str(group.get("recipientEmail") or "")
    if not share_id or not owner or not recipient_email:
        return 0

    cursor = int(group.get("importCursor") or 0)
    if cursor <= 0:
        return 0

    by_owner, _ = db.load_sync_mappings(
        share_id, owner, resource_name, recipient_email
    )
    added = 0
    for idx, member in enumerate(members_data):
        if idx >= cursor:
            break
        owner_person_id = str(member.get("resourceName") or "")
        if not owner_person_id.startswith("people/"):
            continue
        if owner_person_id in by_owner:
            continue
        if idx not in retry_indexes:
            retry_indexes.append(idx)
            added += 1
    if added:
        group["retryIndexes"] = list(dict.fromkeys(retry_indexes))
    return added


def _hydrate_photo_targets(
    group: dict[str, Any],
    members_data: list[dict[str, Any]],
    photo_targets: dict[str, str],
) -> dict[str, str]:
    """Rebuild index→recipient person map from sync mappings (not stored in app_data)."""
    share_id = str(group.get("shareId") or "")
    owner = str(group.get("owner") or "")
    resource_name = str(group.get("resourceName") or "")
    recipient_email = str(group.get("recipientEmail") or "")
    if not share_id or not owner or not recipient_email:
        return dict(photo_targets)

    by_owner, _ = db.load_sync_mappings(
        share_id, owner, resource_name, recipient_email
    )
    hydrated: dict[str, str] = {}
    for idx, member in enumerate(members_data):
        owner_person_id = str(member.get("resourceName") or "")
        mapping = by_owner.get(owner_person_id) or {}
        recipient_person_id = str(mapping.get("recipient_person_id") or "").strip()
        if not recipient_person_id.startswith("people/") or not has_contact_photo(member):
            continue
        # Photos already on this person (this share or a sibling such as AlMsLa)
        # must not be queued again when a meeting group is shared.
        prev_photo = str(mapping.get("last_photo_hash") or "")
        if prev_photo and prev_photo != "no-photo":
            continue
        hydrated[str(idx)] = recipient_person_id
    return hydrated


def _dedupe_int_list(values: list[int]) -> list[int]:
    return list(dict.fromkeys(values))


def _normalize_photo_queues(
    photo_pending: list[int],
    photo_retry: list[int],
    photo_targets: dict[str, str],
    members_data: list[dict[str, Any]],
) -> None:
    """Drop duplicate/stale indices so done count and pending queue stay aligned."""
    valid: set[int] = set()
    for idx_str in photo_targets:
        try:
            idx = int(idx_str)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(members_data) and has_contact_photo(members_data[idx]):
            valid.add(idx)
    photo_pending[:] = _dedupe_int_list([i for i in photo_pending if i in valid])
    photo_retry[:] = _dedupe_int_list([i for i in photo_retry if i in valid])


def _photos_import_complete(
    photo_done: int,
    photo_target_total: int,
    photo_pending: list[int],
    photo_retry: list[int],
) -> bool:
    """True when every photo target is uploaded. Do not drop leftover queues."""
    if photo_pending or photo_retry:
        return False
    if photo_target_total <= 0:
        return True
    return photo_done >= photo_target_total


def _ensure_photo_work_queue(
    members_data: list[dict[str, Any]],
    photo_targets: dict[str, str],
    photo_pending: list[int],
    photo_retry: list[int],
    photo_done: int,
    photo_target_total: int,
) -> bool:
    """Refill the photo queue when it was lost but uploads remain.

    Uploads still skip pictures the recipient already has. An empty queue after
    the first 50-photo batch is what stalled imports at Photos (50/…).
    """
    _normalize_photo_queues(
        photo_pending, photo_retry, photo_targets, members_data
    )
    if photo_pending or photo_retry:
        return False
    hydrate_count = len(photo_targets)
    if hydrate_count <= 0 or photo_done >= hydrate_count:
        return False
    blocked = set(photo_retry)
    for idx_str, _rid in photo_targets.items():
        try:
            idx = int(idx_str)
        except (TypeError, ValueError):
            continue
        if idx in blocked:
            continue
        member = members_data[idx] if 0 <= idx < len(members_data) else None
        if member and has_contact_photo(member) and idx not in photo_pending:
            photo_pending.append(idx)
    return not (photo_pending or photo_retry)


def group_needs_photo_backfill(group: dict[str, Any]) -> bool:
    """True when staged contacts still have photos that were never finished."""
    if not RECIPIENT_IMPORT_INCLUDE_PHOTOS:
        return False
    share_id = str(group.get("shareId") or "")
    members_data = db.read_shared_contacts(share_id) if share_id else []
    photo_targets = _hydrate_photo_targets(group, members_data, {})
    photo_target_total = len(photo_targets)
    if photo_target_total <= 0:
        return False
    photo_pending = list(group.get("photoPendingIndexes") or [])
    photo_retry = list(group.get("photoRetryIndexes") or [])
    _normalize_photo_queues(photo_pending, photo_retry, photo_targets, members_data)
    if photo_pending or photo_retry:
        return True
    if int(group.get("photoDoneCount") or 0) < photo_target_total:
        return True
    # The counter can hit the target after skipping Google's default-user
    # silhouette. Empty last_photo_hash means we never actually uploaded.
    owner = str(group.get("owner") or "")
    resource_name = str(group.get("resourceName") or "")
    recipient_email = str(group.get("recipientEmail") or "")
    if share_id and owner and recipient_email:
        by_owner, _ = db.load_sync_mappings(
            share_id, owner, resource_name, recipient_email
        )
        for idx_str, _rid in photo_targets.items():
            try:
                idx = int(idx_str)
            except (TypeError, ValueError):
                continue
            member = members_data[idx] if 0 <= idx < len(members_data) else None
            owner_person_id = str((member or {}).get("resourceName") or "")
            hash_value = str(
                (by_owner.get(owner_person_id) or {}).get("last_photo_hash") or ""
            )
            if hash_value == "":
                return True
    return False


def _missing_recipient_photo_indexes(
    creds: Credentials,
    photo_targets: dict[str, str],
) -> tuple[list[int], int]:
    """Return (missing indexes, already-have count) from live recipient Google photos."""
    rids = list(dict.fromkeys(photo_targets.values()))
    people = get_people_photos_batch(creds, rids)
    missing: list[int] = []
    already = 0
    for idx_str, rid in photo_targets.items():
        try:
            idx = int(idx_str)
        except (TypeError, ValueError):
            continue
        key = normalize_person_resource_name(rid) or str(rid)
        person = people.get(key) or people.get(str(rid))
        if has_custom_contact_photo(person):
            already += 1
            continue
        missing.append(idx)
    return missing, already


def apply_missing_photo_backfill(
    group: dict[str, Any],
    members_data: list[dict[str, Any]] | None = None,
    *,
    force: bool = False,
    creds: Credentials | None = None,
    live_only: bool = False,
) -> bool:
    """Re-queue leftover photos after Shared. Returns True when photo work remains.

    Rebuild the pending list from mapped contacts that still have photos.
    The upload pass skips pictures the recipient already has, so this does
    not re-upload. A full Google scan runs only when that list is empty.
    live_only skips the stale photoDone/photoTarget refill and trusts Google.
    """
    if not RECIPIENT_IMPORT_INCLUDE_PHOTOS:
        return False
    if not _contacts_are_imported(group):
        return False
    share_id = str(group.get("shareId") or "")
    if members_data is None:
        members_data = db.read_shared_contacts(share_id) if share_id else []
    photo_targets = _hydrate_photo_targets(
        group, members_data, dict(group.get("photoTargets") or {})
    )
    photo_pending = list(group.get("photoPendingIndexes") or [])
    photo_retry = list(group.get("photoRetryIndexes") or [])
    photo_retry_attempts = dict(group.get("photoRetryAttempts") or {})
    photo_done = int(group.get("photoDoneCount") or 0)
    photo_target_total = len(photo_targets)
    looked_up = False
    if force:
        group["photoNeedsRecipientScan"] = True
    queue_stuck = False
    if not live_only:
        queue_stuck = _ensure_photo_work_queue(
            members_data,
            photo_targets,
            photo_pending,
            photo_retry,
            photo_done,
            photo_target_total,
        )
    if photo_pending or photo_retry:
        group.pop("photoNeedsRecipientScan", None)
    elif queue_stuck:
        group["photoNeedsRecipientScan"] = True
    if (
        (force or live_only)
        and creds is not None
        and photo_target_total > 0
        and not photo_pending
        and not photo_retry
    ):
        try:
            missing, already = _missing_recipient_photo_indexes(
                creds, photo_targets
            )
            photo_pending = missing
            photo_retry = []
            photo_retry_attempts = {}
            photo_done = already
            group["photoTargetCount"] = photo_target_total
            group.pop("photoNeedsRecipientScan", None)
            looked_up = True
        except Exception:
            looked_up = False
    needs_scan = bool(group.get("photoNeedsRecipientScan"))
    if (
        not needs_scan
        and _photos_import_complete(
            photo_done, photo_target_total, photo_pending, photo_retry
        )
    ):
        return False
    photo_total = _apply_photo_progress_fields(
        group,
        photo_pending=photo_pending,
        photo_retry=photo_retry,
        photo_retry_attempts=photo_retry_attempts,
        photo_targets=photo_targets,
        photo_done=photo_done,
        replace_done=looked_up,
    )
    group.pop("photoTargets", None)
    display_total = photo_total or (photo_done + len(photo_pending) + len(photo_retry))
    group["importPhase"] = "photos"
    group["status"] = f"Photos ({photo_done}/{display_total})"
    group["pushStatus"] = group["status"]
    return True


def _save_sync_mapping_after_create(
    group: dict[str, Any],
    member_data: dict[str, Any] | None,
    recipient_person_id: str | None,
) -> None:
    if not member_data or not recipient_person_id:
        return
    owner_person_id = str(member_data.get("resourceName") or "")
    if not owner_person_id.startswith("people/"):
        return
    share_id = str(group.get("shareId") or "")
    owner = str(group.get("owner") or "")
    resource_name = str(group.get("resourceName") or "")
    recipient_email = str(group.get("recipientEmail") or "")
    if not share_id or not owner or not resource_name or not recipient_email:
        return
    from .contact_hash import compute_contact_hash, compute_photo_hash

    photo_hash = compute_photo_hash(member_data)
    db.upsert_sync_mapping(
        share_id=share_id,
        owner=owner,
        group_resource_name=resource_name,
        recipient_email=recipient_email,
        owner_person_id=owner_person_id,
        recipient_person_id=str(recipient_person_id),
        last_hash=compute_contact_hash(member_data),
        # Keep photo hash empty until the photo pass succeeds so Retry/Sync
        # can still backfill pictures that never uploaded.
        last_photo_hash="" if photo_hash != "no-photo" else photo_hash,
        owner_etag=str(member_data.get("etag") or ""),
    )


def _existing_recipient_mapping(
    group: dict[str, Any], owner_person_id: str
) -> dict[str, Any] | None:
    """Find the recipient contact already created for this owner person in another shared group."""
    owner = str(group.get("owner") or "")
    recipient_email = str(group.get("recipientEmail") or "")
    share_id = str(group.get("shareId") or "")
    if not owner or not recipient_email or not str(owner_person_id).startswith("people/"):
        return None
    for sibling in db.find_sibling_mappings(owner, recipient_email, owner_person_id):
        if share_id and str(sibling.get("share_id") or "") == share_id:
            continue
        rid = normalize_person_resource_name(str(sibling.get("recipient_person_id") or ""))
        if rid:
            return sibling
    return None


def _existing_recipient_person_id(group: dict[str, Any], owner_person_id: str) -> str:
    """Reuse the recipient contact already created for this owner person in another shared group."""
    sibling = _existing_recipient_mapping(group, owner_person_id)
    if not sibling:
        return ""
    return (
        normalize_person_resource_name(str(sibling.get("recipient_person_id") or ""))
        or ""
    )


def _save_sync_mapping_after_reuse(
    group: dict[str, Any],
    member_data: dict[str, Any] | None,
    recipient_person_id: str,
    sibling: dict[str, Any] | None = None,
) -> None:
    """Record this share's mapping for a contact that already exists on the recipient."""
    if not member_data or not recipient_person_id:
        return
    owner_person_id = str(member_data.get("resourceName") or "")
    if not owner_person_id.startswith("people/"):
        return
    share_id = str(group.get("shareId") or "")
    owner = str(group.get("owner") or "")
    resource_name = str(group.get("resourceName") or "")
    recipient_email = str(group.get("recipientEmail") or "")
    if not share_id or not owner or not resource_name or not recipient_email:
        return
    from .contact_hash import compute_contact_hash, compute_photo_hash

    sibling = sibling or {}
    last_hash = str(sibling.get("last_hash") or "") or compute_contact_hash(member_data)
    last_photo_hash = str(sibling.get("last_photo_hash") or "") or compute_photo_hash(
        member_data
    )
    db.upsert_sync_mapping(
        share_id=share_id,
        owner=owner,
        group_resource_name=resource_name,
        recipient_email=recipient_email,
        owner_person_id=owner_person_id,
        recipient_person_id=str(recipient_person_id),
        last_hash=last_hash,
        last_photo_hash=last_photo_hash,
        owner_etag=str(sibling.get("owner_etag") or member_data.get("etag") or ""),
    )


def _attach_existing_recipient_to_group(
    creds: Credentials,
    group: dict[str, Any],
    group_id: str,
    member: dict[str, Any],
) -> str:
    """Add an existing AlMsLa (or other sibling) contact to this meeting group.

    Returns the recipient person id on success, or "" if none exists / add failed.
    """
    owner_person_id = str(member.get("resourceName") or "")
    sibling = _existing_recipient_mapping(group, owner_person_id)
    if not sibling:
        return ""
    rid = normalize_person_resource_name(str(sibling.get("recipient_person_id") or ""))
    if not rid:
        return ""
    if not ensure_contact_in_group(creds, rid, group_id):
        return ""
    _save_sync_mapping_after_reuse(group, member, rid, sibling)
    return rid


def _contact_match_keys(person: dict[str, Any] | None) -> set[str]:
    """Identity keys for matching a staged contact to one already in the group."""
    keys: set[str] = set()
    if not person:
        return keys
    for item in person.get("emailAddresses") or []:
        value = str((item or {}).get("value") or "").strip().lower()
        if value:
            keys.add(f"email:{value}")
    for item in person.get("phoneNumbers") or []:
        digits = "".join(ch for ch in str((item or {}).get("value") or "") if ch.isdigit())
        if len(digits) >= 7:
            keys.add(f"phone:{digits[-10:]}")
    for item in person.get("names") or []:
        name = str((item or {}).get("displayName") or "").strip().lower()
        if not name:
            given = str((item or {}).get("givenName") or "").strip()
            family = str((item or {}).get("familyName") or "").strip()
            name = f"{given} {family}".strip().lower()
        if name:
            keys.add(f"name:{name}")
    return keys


def adopt_untracked_group_members(
    creds: Credentials,
    group: dict[str, Any],
    group_id: str,
    members_data: list[dict[str, Any]],
) -> int:
    """Claim contacts already in the group that we have no mapping for.

    A create can succeed at Google and then be lost before we record it (request
    killed, batch error after a partial write). Re-creating those is what filled
    the recipient's group with duplicates. Adopting them instead makes the
    import safe to retry.
    """
    share_id = str(group.get("shareId") or "")
    owner = str(group.get("owner") or "")
    resource_name = str(group.get("resourceName") or "")
    recipient_email = str(group.get("recipientEmail") or "")
    if not share_id or not owner or not recipient_email or not group_id:
        return 0

    by_owner, _ = db.load_sync_mappings(
        share_id, owner, resource_name, recipient_email
    )
    try:
        details = get_group_details(creds, group_id, 5000)
    except Exception:
        return 0
    members_in_group = {
        rn
        for rn in (
            normalize_person_resource_name(m)
            for m in (details.get("memberResourceNames") or [])
        )
        if rn
    }
    if not members_in_group:
        return 0

    tracked = {
        normalize_person_resource_name(str(m.get("recipient_person_id") or ""))
        for m in by_owner.values()
    }
    tracked.discard("")

    # Needs a contact: never mapped, or mapped to something no longer in the
    # group. A mapping pointing at a deleted contact is why some people showed
    # up only as an untracked copy; re-point it rather than lose them.
    unmapped: list[tuple[int, dict[str, Any]]] = []
    for idx, member in enumerate(members_data):
        owner_person_id = str(member.get("resourceName") or "")
        if not owner_person_id.startswith("people/"):
            continue
        mapping = by_owner.get(owner_person_id)
        rid = normalize_person_resource_name(
            str((mapping or {}).get("recipient_person_id") or "")
        )
        if not mapping or not rid or rid not in members_in_group:
            unmapped.append((idx, member))
    if not unmapped:
        return 0

    candidates = [rn for rn in members_in_group if rn not in tracked]
    if not candidates:
        return 0

    try:
        people = get_people_contacts_batch(creds, candidates)
    except Exception:
        return 0

    available: dict[str, str] = {}
    for rid, person in people.items():
        for key in _contact_match_keys(person):
            available.setdefault(key, rid)

    adopted = 0
    claimed: set[str] = set()
    unmatched: list[dict[str, Any]] = []
    for _idx, member in unmapped:
        match = ""
        for key in _contact_match_keys(member):
            rid = available.get(key)
            if rid and rid not in claimed:
                match = rid
                break
        if not match:
            unmatched.append(member)
            continue
        claimed.add(match)
        _save_sync_mapping_after_create(group, member, match)
        adopted += 1

    # No copy survived for these. If the contact we recorded is really gone,
    # drop the record so the normal retry path re-creates the contact; a record
    # pointing at a deleted contact otherwise blocks the re-import forever.
    stale_rids = {}
    for member in unmatched:
        owner_person_id = str(member.get("resourceName") or "")
        rid = normalize_person_resource_name(
            str((by_owner.get(owner_person_id) or {}).get("recipient_person_id") or "")
        )
        if rid:
            stale_rids[rid] = owner_person_id
    cleared = 0
    if stale_rids:
        try:
            still_there = get_people_contacts_batch(
                creds, sorted(stale_rids), strict=True
            )
        except Exception:
            still_there = None
        if still_there is not None:
            gone = [
                owner_person_id
                for rid, owner_person_id in stale_rids.items()
                if rid not in still_there
            ]
            if gone:
                db.delete_sync_mapping_rows(
                    share_id, owner, resource_name, recipient_email, gone
                )
                cleared = len(gone)

    if adopted or cleared:
        logger.info(
            "Repaired %s: claimed %s existing contacts, cleared %s records for "
            "contacts deleted in the recipient account",
            group_id,
            adopted,
            cleared,
        )
    return adopted


def _process_sync_mappings_before_batch(
    creds: Credentials,
    group: dict[str, Any],
    group_id: str,
    members_data: list[dict[str, Any]],
    start: int,
    ops_budget: int,
    *,
    photo_pending: list[int],
    photo_targets: dict[str, str],
    over_budget: Callable[[], bool],
) -> tuple[int, int]:
    """Skip or update mapped contacts before batch create."""
    share_id = str(group.get("shareId") or "")
    owner = str(group.get("owner") or "")
    resource_name = str(group.get("resourceName") or "")
    recipient_email = str(group.get("recipientEmail") or "")
    if not share_id or not owner or not recipient_email:
        return start, 0

    by_owner_id, _ = db.load_sync_mappings(
        share_id, owner, resource_name, recipient_email
    )

    from .contact_hash import compute_contact_hash, compute_photo_hash
    from ..people_recipient import (
        delete_contact_photo,
        ensure_contact_in_group,
        get_person_etags_batch,
        normalize_person_resource_name,
        set_contact_photo,
        update_contact,
    )

    owner_creds = owner_credentials(owner)
    owner_token = (owner_creds.token or "") if owner_creds else None

    ops_used = 0
    cursor = start
    while cursor < len(members_data) and ops_used < ops_budget:
        if over_budget():
            break
        member = members_data[cursor]
        owner_person_id = str(member.get("resourceName") or "")
        mapping = by_owner_id.get(owner_person_id)
        reused_sibling = False
        if not mapping or not str((mapping or {}).get("recipient_person_id") or ""):
            sibling = _existing_recipient_mapping(group, owner_person_id)
            if sibling:
                mapping = sibling
                reused_sibling = True
        recipient_person_id = str((mapping or {}).get("recipient_person_id") or "")
        if not mapping or not recipient_person_id:
            break

        contact_hash = compute_contact_hash(member)
        photo_hash = compute_photo_hash(member)
        owner_etag = str(member.get("etag") or "")
        prev_hash = str(mapping.get("last_hash") or "")
        prev_photo = str(mapping.get("last_photo_hash") or "")
        prev_etag = str(mapping.get("owner_etag") or "")

        rid = normalize_person_resource_name(recipient_person_id)
        if not rid:
            break

        if reused_sibling:
            if not ensure_contact_in_group(creds, rid, group_id):
                break
            _save_sync_mapping_after_reuse(group, member, rid, mapping)
            cursor += 1
            ops_used += 1
            continue

        if contact_hash == prev_hash and photo_hash == prev_photo and owner_etag == prev_etag:
            if photo_hash != "no-photo" and str(cursor) not in photo_targets:
                photo_pending.append(cursor)
                photo_targets[str(cursor)] = rid
            cursor += 1
            ops_used += 1
            continue

        etags = get_person_etags_batch(creds, [rid])
        etag = etags.get(rid)
        if not etag:
            break

        if contact_hash != prev_hash or owner_etag != prev_etag:
            if not update_contact(creds, rid, member, etag):
                break
        if photo_hash != prev_photo:
            if photo_hash == "no-photo":
                delete_contact_photo(creds, rid)
            else:
                set_contact_photo(creds, rid, member, owner_token=owner_token)

        db.upsert_sync_mapping(
            share_id=share_id,
            owner=owner,
            group_resource_name=resource_name,
            recipient_email=recipient_email,
            owner_person_id=owner_person_id,
            recipient_person_id=rid,
            last_hash=contact_hash,
            last_photo_hash=photo_hash,
            owner_etag=owner_etag,
        )
        ensure_contact_in_group(creds, rid, group_id)
        if photo_hash != "no-photo" and str(cursor) not in photo_targets:
            photo_pending.append(cursor)
            photo_targets[str(cursor)] = rid

        cursor += 1
        ops_used += 1

    return cursor, ops_used


def _record_photo_upload_hash(group: dict[str, Any], member_data: dict[str, Any] | None) -> None:
    if not member_data:
        return
    owner_person_id = str(member_data.get("resourceName") or "")
    share_id = str(group.get("shareId") or "")
    owner = str(group.get("owner") or "")
    resource_name = str(group.get("resourceName") or "")
    recipient_email = str(group.get("recipientEmail") or "")
    if not owner_person_id.startswith("people/") or not share_id or not owner or not recipient_email:
        return
    from .contact_hash import compute_contact_hash, compute_photo_hash

    by_owner, _ = db.load_sync_mappings(share_id, owner, resource_name, recipient_email)
    mapping = by_owner.get(owner_person_id) or {}
    recipient_person_id = str(mapping.get("recipient_person_id") or "")
    if not recipient_person_id.startswith("people/"):
        return
    db.upsert_sync_mapping(
        share_id=share_id,
        owner=owner,
        group_resource_name=resource_name,
        recipient_email=recipient_email,
        owner_person_id=owner_person_id,
        recipient_person_id=recipient_person_id,
        last_hash=str(mapping.get("last_hash") or compute_contact_hash(member_data)),
        last_photo_hash=compute_photo_hash(member_data),
        owner_etag=str(mapping.get("owner_etag") or member_data.get("etag") or ""),
    )


def _record_create_result(
    index: int,
    resource_id: str | None,
    member_data: dict[str, Any] | None,
    *,
    retry_indexes: list[int],
    retry_attempts: dict[str, int],
    skipped_indexes: list[int],
    photo_pending: list[int],
    photo_targets: dict[str, str],
) -> bool:
    """Apply one contact create outcome. Returns True if the create failed."""
    if resource_id:
        retry_attempts.pop(str(index), None)
        skipped_indexes[:] = [x for x in skipped_indexes if x != index]
        if (
            RECIPIENT_IMPORT_INCLUDE_PHOTOS
            and member_data
            and has_contact_photo(member_data)
        ):
            photo_targets[str(index)] = resource_id
            if index not in photo_pending:
                photo_pending.append(index)
        return False

    key = str(index)
    nxt = retry_attempts.get(key, 0) + 1
    if nxt >= RECIPIENT_IMPORT_MAX_RETRIES:
        retry_attempts.pop(key, None)
        if index not in skipped_indexes:
            skipped_indexes.append(index)
    else:
        retry_attempts[key] = nxt
        retry_indexes.append(index)
    return True


def _notify_contact_import_progress(
    group_snapshot: dict[str, Any],
    *,
    cursor: int,
    members_total: int,
    retry_indexes: list[int],
    skipped_indexes: list[int],
    photo_pending: list[int],
    photo_targets: dict[str, str],
    on_group_updated: Callable[[dict[str, Any]], None] | None,
    failed: int = 0,
) -> None:
    if not on_group_updated:
        return
    group_snapshot["importCursor"] = cursor
    group_snapshot["retryIndexes"] = list(dict.fromkeys(retry_indexes))
    group_snapshot["skippedIndexes"] = list(dict.fromkeys(skipped_indexes))
    group_snapshot["skippedCount"] = len(skipped_indexes)
    group_snapshot["photoPendingIndexes"] = list(dict.fromkeys(photo_pending))
    group_snapshot["photoTargets"] = dict(photo_targets)
    photo_done = int(group_snapshot.get("photoDoneCount") or 0)
    photo_retry_list = list(group_snapshot.get("photoRetryIndexes") or [])
    photo_remaining = len(photo_pending) + len(photo_retry_list)
    photo_target_total = _photo_target_count(group_snapshot, photo_targets)
    group_snapshot["photoTargetCount"] = photo_target_total
    group_snapshot["photoDoneCount"] = photo_done
    retry_label = f", retry {len(retry_indexes)}" if retry_indexes else ""
    fail_label = f", {failed} failed this pass" if failed else ""
    if cursor >= members_total and photo_remaining > 0:
        photo_total = photo_target_total or (photo_done + photo_remaining)
        group_snapshot["importPhase"] = "photos"
        group_snapshot["status"] = f"Photos ({photo_done}/{photo_total})"
    else:
        group_snapshot["importPhase"] = "contacts"
        group_snapshot["status"] = (
            f"Importing ({cursor}/{members_total}{retry_label}{fail_label})"
        )
    on_group_updated(deepcopy(group_snapshot))


def _contact_import_chunk_size(members_total: int, ops_budget: int) -> int:
    """Small groups use progress-sized chunks; large groups use full API batch size."""
    api_cap = min(
        RECIPIENT_IMPORT_CREATE_BATCH_SIZE,
        RECIPIENT_IMPORT_BATCH_SIZE,
        200,
    )
    if members_total <= RECIPIENT_IMPORT_LARGE_GROUP_THRESHOLD:
        api_cap = min(api_cap, RECIPIENT_IMPORT_PROGRESS_BATCH_SIZE)
    return max(1, min(api_cap, ops_budget))


def _photo_import_chunk_size(photo_remaining: int, ops_budget: int) -> int:
    """Keep photo batches large through the tail of a job.

    Contact creates shrink on small groups so the UI counter ticks. Photos
    already update after every batch, so shrinking the last 30 to 5 only
    added extra Google round trips.
    """
    return max(
        1,
        min(
            RECIPIENT_IMPORT_PHOTO_BATCH_SIZE,
            max(1, int(ops_budget)),
            max(1, int(photo_remaining)),
        ),
    )


def _import_contacts_batch(
    creds: Credentials,
    members_data: list[dict[str, Any]],
    group_id: str,
    start: int,
    ops_budget: int,
    *,
    retry_indexes: list[int],
    retry_attempts: dict[str, int],
    skipped_indexes: list[int],
    photo_pending: list[int],
    photo_targets: dict[str, str],
    over_budget,
    on_group_updated: Callable[[dict[str, Any]], None] | None = None,
    group_snapshot: dict[str, Any] | None = None,
) -> tuple[int, int, int]:
    """Import a slice of contacts using batchCreateContacts. Returns (new_start, attempts_used, failed)."""
    attempts_used = 0
    failed = 0
    cursor = start
    members_total = len(members_data)

    while cursor < members_total and attempts_used < ops_budget:
        if over_budget():
            break
        chunk_size = min(
            _contact_import_chunk_size(members_total, ops_budget - attempts_used),
            members_total - cursor,
        )
        if chunk_size <= 0:
            break

        indexed_members = [
            (idx, members_data[idx]) for idx in range(cursor, cursor + chunk_size)
        ]
        to_create: list[tuple[int, dict[str, Any]]] = []
        chunk_failed = 0
        for idx, member in indexed_members:
            existing = ""
            if group_snapshot is not None:
                existing = _attach_existing_recipient_to_group(
                    creds, group_snapshot, group_id, member
                )
            if existing:
                retry_attempts.pop(str(idx), None)
                skipped_indexes[:] = [x for x in skipped_indexes if x != idx]
                attempts_used += 1
                continue
            to_create.append((idx, member))
        results = (
            batch_create_contacts_in_group(creds, to_create, group_id)
            if to_create
            else {}
        )
        for idx, member in to_create:
            resource_id = results.get(idx)
            if _record_create_result(
                idx,
                resource_id,
                member,
                retry_indexes=retry_indexes,
                retry_attempts=retry_attempts,
                skipped_indexes=skipped_indexes,
                photo_pending=photo_pending,
                photo_targets=photo_targets,
            ):
                chunk_failed += 1
                failed += 1
            elif resource_id and group_snapshot is not None:
                _save_sync_mapping_after_create(group_snapshot, member, resource_id)
            attempts_used += 1

        cursor += chunk_size
        if on_group_updated and group_snapshot is not None:
            _notify_contact_import_progress(
                group_snapshot,
                cursor=cursor,
                members_total=len(members_data),
                retry_indexes=retry_indexes,
                skipped_indexes=skipped_indexes,
                photo_pending=photo_pending,
                photo_targets=photo_targets,
                on_group_updated=on_group_updated,
                failed=chunk_failed,
            )

    return cursor, attempts_used, failed


def resolve_import_time_budget_ms(time_budget_ms: int | None) -> int:
    """Clamp one import pass to the caller's slice.

    A poll asks for a few seconds of work, so a pass must not run the full
    default budget — that is what pushed requests past Cloud Run's 300s limit.
    """
    if time_budget_ms is None:
        return RECIPIENT_IMPORT_TIME_BUDGET_MS
    return max(1000, min(int(time_budget_ms), RECIPIENT_IMPORT_TIME_BUDGET_MS))


def _known_contact_total(group: dict[str, Any], members_len: int = 0) -> int:
    """Use the largest known contact total so a stale memberCount cannot freeze photos."""
    return max(
        int(group.get("memberCount") or 0),
        int(group.get("actualCount") or 0),
        int(group.get("importCursor") or 0),
        int(members_len or 0),
    )


def _raise_member_count_to_known_total(
    group: dict[str, Any], members_len: int = 0
) -> int:
    known = _known_contact_total(group, members_len)
    if known > int(group.get("memberCount") or 0):
        group["memberCount"] = known
    return int(group.get("memberCount") or 0)


def _photo_count_cap(group: dict[str, Any], value: int) -> int:
    """Photo counts cannot exceed the number of contacts in the group."""
    ceiling = _known_contact_total(group)
    pending = len(group.get("photoPendingIndexes") or []) + len(
        group.get("photoRetryIndexes") or []
    )
    if pending:
        ceiling = max(
            ceiling,
            int(group.get("photoDoneCount") or 0) + pending,
            int(value or 0) + pending,
        )
    if ceiling > 0:
        return min(max(0, int(value)), ceiling)
    return max(0, int(value))


def _photo_target_count(
    group: dict[str, Any],
    photo_targets: dict[str, str],
    *,
    replace: bool = False,
) -> int:
    count = len(photo_targets)
    prev = int(group.get("photoTargetCount") or 0)
    if count > 0:
        value = count if replace else max(prev, count)
        group["photoTargetCount"] = _photo_count_cap(group, value)
        return int(group["photoTargetCount"])
    return _photo_count_cap(group, prev)


def _reconcile_photo_done(
    group: dict[str, Any],
    photo_targets: dict[str, str],
    photo_pending: list[int],
    photo_retry: list[int],
    stored_done: int,
) -> int:
    """Done = photos finished, not 'uploads attempted including re-uploads'."""
    hydrate = len(photo_targets)
    remaining = len(photo_pending) + len(photo_retry)
    stored = max(0, int(stored_done or 0))
    # Empty queue while waiting for a Google missing-photo scan does not mean
    # every photo is done — do not fill the counter up to the target.
    if remaining == 0 and group.get("photoNeedsRecipientScan"):
        return _photo_count_cap(group, stored)
    derived = max(0, hydrate - remaining) if hydrate else 0
    return _photo_count_cap(group, max(stored, derived))


def _apply_photo_progress_fields(
    group: dict[str, Any],
    *,
    photo_pending: list[int],
    photo_retry: list[int],
    photo_retry_attempts: dict[str, int],
    photo_targets: dict[str, str],
    photo_done: int,
    replace_done: bool = False,
) -> int:
    group["photoPendingIndexes"] = list(dict.fromkeys(photo_pending))
    group["photoRetryIndexes"] = list(dict.fromkeys(photo_retry))
    group["photoRetryAttempts"] = photo_retry_attempts
    group["photoTargets"] = photo_targets
    prev_done = int(group.get("photoDoneCount") or 0)
    if replace_done:
        photo_done = _photo_count_cap(group, max(0, int(photo_done)))
    else:
        photo_done = _reconcile_photo_done(
            group,
            photo_targets,
            photo_pending,
            photo_retry,
            max(prev_done, max(0, int(photo_done))),
        )
    photo_target = _photo_target_count(
        group, photo_targets, replace=replace_done
    )
    if photo_done > photo_target:
        photo_target = photo_done
        group["photoTargetCount"] = _photo_count_cap(group, photo_target)
        photo_done = min(photo_done, photo_target)
    group["photoDoneCount"] = photo_done
    return photo_target


def _queue_photo_failure(
    src: int,
    *,
    photo_retry: list[int],
    photo_retry_attempts: dict[str, int],
) -> None:
    key = str(src)
    nxt = photo_retry_attempts.get(key, 0) + 1
    if nxt >= RECIPIENT_IMPORT_MAX_RETRIES:
        photo_retry_attempts.pop(key, None)
    else:
        photo_retry_attempts[key] = nxt
        photo_retry.append(src)


def _invalidate_stale_photo_mapping(
    group: dict[str, Any],
    members_data: list[dict[str, Any]],
    idx: int,
    *,
    photo_pending: list[int],
    photo_retry: list[int],
    photo_retry_attempts: dict[str, int],
    photo_targets: dict[str, str],
    retry_indexes: list[int],
) -> None:
    """Drop dead recipient person id and queue contact re-create before photos."""
    share_id = str(group.get("shareId") or "")
    owner = str(group.get("owner") or "")
    resource_name = str(group.get("resourceName") or "")
    recipient_email = str(group.get("recipientEmail") or "")
    member = members_data[idx] if 0 <= idx < len(members_data) else None
    owner_person_id = str((member or {}).get("resourceName") or "")
    if share_id and owner and resource_name and recipient_email and owner_person_id:
        db.delete_sync_mapping_rows(
            share_id, owner, resource_name, recipient_email, [owner_person_id]
        )
    photo_targets.pop(str(idx), None)
    photo_retry_attempts.pop(str(idx), None)
    if idx in photo_pending:
        photo_pending[:] = [i for i in photo_pending if i != idx]
    if idx in photo_retry:
        photo_retry[:] = [i for i in photo_retry if i != idx]
    if idx not in retry_indexes:
        retry_indexes.append(idx)
    cursor = min(int(group.get("importCursor") or 0), idx)
    group["importCursor"] = cursor
    group["importPhase"] = "contacts"
    total = len(members_data)
    group["status"] = f"Importing ({cursor}/{total})" if total else "Importing"


def _import_photos_pass(
    creds: Credentials,
    members_data: list[dict[str, Any]],
    photo_ops: int,
    *,
    photo_pending: list[int],
    photo_retry: list[int],
    photo_retry_attempts: dict[str, int],
    photo_targets: dict[str, str],
    over_budget,
    group: dict[str, Any] | None = None,
    on_group_updated: Callable[[dict[str, Any]], None] | None = None,
    photo_done: int = 0,
    retry_indexes: list[int] | None = None,
) -> tuple[int, int, int]:
    """Prefetch + batch-upload photos. Returns (used_ops, failures, successes)."""
    if photo_ops <= 0:
        return 0, 0, 0

    retry_indexes = retry_indexes if retry_indexes is not None else []
    work: list[tuple[int, dict[str, Any], str]] = []
    while photo_retry and len(work) < photo_ops:
        if over_budget():
            break
        src = int(photo_retry.pop(0))
        member = members_data[src] if 0 <= src < len(members_data) else None
        rid = photo_targets.get(str(src))
        if not member or not rid:
            photo_retry_attempts.pop(str(src), None)
            continue
        work.append((src, member, rid))

    while photo_pending and len(work) < photo_ops:
        if over_budget():
            break
        src = int(photo_pending.pop(0))
        member = members_data[src] if 0 <= src < len(members_data) else None
        rid = photo_targets.get(str(src))
        if not member or not rid:
            photo_retry_attempts.pop(str(src), None)
            continue
        work.append((src, member, rid))

    if not work:
        return 0, 0, 0

    attempted = len(work)
    # One batched lookup tells us both which contacts still exist and which
    # already have a picture. Asking per contact turned every batch into
    # dozens of sequential round trips, which is why photos crawled in 1 by 1.
    try:
        people = get_people_photos_batch(creds, [rid for _src, _member, rid in work])
    except Exception:
        people = {}
    live: set[int] = set()
    keep: list[tuple[int, dict[str, Any], str]] = []
    for src, member, rid in work:
        key = normalize_person_resource_name(rid) or str(rid)
        person = people.get(key) or people.get(str(rid))
        if person is None and people:
            # Present in the mapping but gone from the recipient account.
            if group is not None:
                _invalidate_stale_photo_mapping(
                    group,
                    members_data,
                    src,
                    photo_pending=photo_pending,
                    photo_retry=photo_retry,
                    photo_retry_attempts=photo_retry_attempts,
                    photo_targets=photo_targets,
                    retry_indexes=retry_indexes,
                )
            continue
        live.add(src)
        if has_custom_contact_photo(person):
            photo_retry_attempts.pop(str(src), None)
            continue
        keep.append((src, member, rid))
    work = keep
    if not work:
        if group is not None and on_group_updated:
            new_photo_done = _reconcile_photo_done(
                group, photo_targets, photo_pending, photo_retry, photo_done
            )
            photo_total = _apply_photo_progress_fields(
                group,
                photo_pending=photo_pending,
                photo_retry=photo_retry,
                photo_retry_attempts=photo_retry_attempts,
                photo_targets=photo_targets,
                photo_done=new_photo_done,
            )
            if not photo_total:
                photo_remaining = len(photo_pending) + len(photo_retry)
                photo_total = new_photo_done + photo_remaining
            group["status"] = f"Photos ({new_photo_done}/{photo_total})"
            group["importPhase"] = "photos"
            on_group_updated(deepcopy(group))
        return attempted, 0, 0

    if over_budget():
        for src, _member, _rid in reversed(work):
            if src not in photo_pending and src not in photo_retry:
                photo_pending.insert(0, src)
        return 0, 0, 0

    token = creds.token or ""
    owner_token: str | None = None
    if group:
        owner_email = str(group.get("owner") or "").strip()
        if owner_email:
            owner_creds = owner_credentials(owner_email)
            if owner_creds and owner_creds.token:
                owner_token = owner_creds.token
    prefetched = prefetch_photo_bytes(
        [(src, member) for src, member, _ in work],
        token,
        owner_token=owner_token,
    )

    uploads: list[tuple[int, str, str]] = []
    missing_bytes: list[int] = []
    for src, _member, rid in work:
        payload = prefetched.get(src)
        if payload:
            uploads.append((src, rid, payload))
        else:
            missing_bytes.append(src)

    outcomes = batch_upload_contact_photos(creds, uploads) if uploads else {}

    failures = 0
    successes = 0
    for src, _member, rid in work:
        if src in missing_bytes or not outcomes.get(src):
            failures += 1
            if src not in live and group is not None:
                _invalidate_stale_photo_mapping(
                    group,
                    members_data,
                    src,
                    photo_pending=photo_pending,
                    photo_retry=photo_retry,
                    photo_retry_attempts=photo_retry_attempts,
                    photo_targets=photo_targets,
                    retry_indexes=retry_indexes,
                )
            else:
                _queue_photo_failure(
                    src,
                    photo_retry=photo_retry,
                    photo_retry_attempts=photo_retry_attempts,
                )
        else:
            photo_retry_attempts.pop(str(src), None)
            successes += 1
            if group is not None:
                _record_photo_upload_hash(group, _member)

    if group is not None and on_group_updated and (work or attempted):
        new_photo_done = _reconcile_photo_done(
            group, photo_targets, photo_pending, photo_retry, photo_done
        )
        photo_total = _apply_photo_progress_fields(
            group,
            photo_pending=photo_pending,
            photo_retry=photo_retry,
            photo_retry_attempts=photo_retry_attempts,
            photo_targets=photo_targets,
            photo_done=new_photo_done,
        )
        if not photo_total:
            photo_remaining = len(photo_pending) + len(photo_retry)
            photo_total = new_photo_done + photo_remaining
        group["status"] = f"Photos ({new_photo_done}/{photo_total})"
        group["importPhase"] = "photos"
        on_group_updated(deepcopy(group))

    return len(work), failures, successes


def create_shared_groups(
    shared_groups: list[dict[str, Any]],
    creds: Credentials,
    *,
    max_ops: int | None = None,
    time_budget_ms: int = RECIPIENT_IMPORT_TIME_BUDGET_MS,
    on_group_updated: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    if not shared_groups:
        return []

    started_at = time.time() * 1000
    deduped = _dedupe_shared_groups(shared_groups)
    remaining_ops = RECIPIENT_IMPORT_MAX_OPS_PER_LOAD if max_ops is None else max_ops

    def over_budget() -> bool:
        return (time.time() * 1000 - started_at) > time_budget_ms

    if remaining_ops <= 0:
        for group in deduped:
            total = int(group.get("memberCount") or 0)
            cursor = int(group.get("importCursor") or 0)
            status = str(group.get("status") or "")
            if status.startswith("Waiting for owner sync"):
                continue
            if not status:
                if total > 0 and cursor < total:
                    group["status"] = f"Queued ({cursor}/{total})"
                elif total > 0 and cursor >= total:
                    group["status"] = "Shared"
                    _sync_push_status_field(group)
        return _filter_result(deduped)

    def notify_group_progress(group: dict[str, Any]) -> None:
        if on_group_updated:
            on_group_updated(deepcopy(group))

    for index, group in enumerate(deduped):
        name = group.get("name") or ""
        share_id = group.get("shareId") or ""
        created = _contact_group_resource(group.get("created"))
        if group.get("created") and not created:
            group["created"] = ""
        import_cursor = group.get("importCursor")

        try:
            members_data = db.read_shared_contacts(share_id)
            if not members_data:
                if not created:
                    created = find_group_by_name(creds, build_shared_group_name(name)) or ""
                    if created:
                        group["created"] = created
                if created and group_exists(creds, created):
                    actual = group_member_count(creds, created)
                    if actual is not None:
                        group["actualCount"] = actual
                        group["importCursor"] = max(
                            int(group.get("importCursor") or 0),
                            actual,
                        )
                    group["status"] = "Shared"
                    _sync_push_status_field(group)
                elif group.get("recovered") or int(group.get("actualCount") or 0) > 0:
                    if created and group_exists(creds, created):
                        actual = group_member_count(creds, created)
                        if actual is not None:
                            group["actualCount"] = actual
                            group["importCursor"] = max(
                                int(group.get("importCursor") or 0),
                                actual,
                            )
                    group["status"] = "Shared (recovered)"
                    _sync_push_status_field(group)
                elif str(group.get("status") or "").startswith("Waiting for owner sync"):
                    continue
                else:
                    # Keep the list row. Dismiss/restore can leave Share without
                    # staged owner payloads even though Google already has the people.
                    member_count = int(group.get("memberCount") or 0)
                    group["status"] = (
                        f"Waiting for owner sync (0/{member_count})"
                        if member_count
                        else "Waiting for owner sync"
                    )
                    group.pop("staleRemove", None)
                continue

            group_id = _ensure_recipient_group(creds, group, name)
            if not group_id:
                group["status"] = "Error"
                continue

            start = _reconcile_import_progress(creds, group, group_id, len(members_data))

            # More contacts in Google than we have records for means earlier
            # creates were lost. Claim them, and repair records pointing at a
            # contact that was deleted, before anything else runs. This has to
            # happen for finished groups too, which is where the damage shows.
            if int(group.get("actualCount") or 0) > _sync_mapping_count(group):
                try:
                    adopt_untracked_group_members(
                        creds, group, group_id, members_data
                    )
                    group["repairCheckedActual"] = int(group.get("actualCount") or 0)
                except Exception:
                    logger.exception(
                        "Failed adopting existing contacts for %s", group_id
                    )
                start = _reconcile_import_progress(
                    creds, group, group_id, len(members_data)
                )

            import_cursor = start
            status_text = str(group.get("status") or "")
            if status_text.startswith("Ready"):
                group["status"] = f"Importing ({start}/{len(members_data)})"
                group["importPhase"] = "contacts"
                notify_group_progress(group)
            retry_indexes: list[int] = list(group.get("retryIndexes") or [])
            retry_attempts: dict[str, int] = dict(group.get("retryAttempts") or {})
            skipped_indexes: list[int] = list(group.get("skippedIndexes") or [])
            _queue_unmapped_member_retries(group, members_data, retry_indexes)
            photo_pending: list[int] = list(group.get("photoPendingIndexes") or [])
            photo_retry: list[int] = list(group.get("photoRetryIndexes") or [])
            photo_retry_attempts: dict[str, int] = dict(group.get("photoRetryAttempts") or {})
            photo_targets: dict[str, str] = _hydrate_photo_targets(
                group,
                members_data,
                dict(group.get("photoTargets") or {}),
            )
            group.pop("photoTargets", None)
            photo_done = int(group.get("photoDoneCount") or 0)
            photo_target_total = _photo_target_count(group, photo_targets)
            if _ensure_photo_work_queue(
                members_data,
                photo_targets,
                photo_pending,
                photo_retry,
                photo_done,
                len(photo_targets),
            ):
                group["photoNeedsRecipientScan"] = True
            photo_done = _photo_count_cap(group, photo_done)
            photo_target_total = _photo_count_cap(group, photo_target_total)
            group["photoDoneCount"] = photo_done
            group["photoTargetCount"] = photo_target_total

            contacts_exhausted = _contacts_import_complete(
                group, len(members_data), start, retry_indexes
            )
            needs_more = not contacts_exhausted
            if contacts_exhausted:
                group["importCursor"] = len(members_data)
            if not needs_more:
                pass
            else:
                if remaining_ops <= 0:
                    retry_label = f", retry {len(retry_indexes)}" if retry_indexes else ""
                    group["status"] = f"Queued ({start}/{len(members_data)}{retry_label})"
                    group["importPhase"] = "contacts"
                    continue

                ops_budget = min(RECIPIENT_IMPORT_BATCH_SIZE, remaining_ops)
                attempts_used = 0
                failed = 0

                if on_group_updated:
                    _notify_contact_import_progress(
                        group,
                        cursor=start,
                        members_total=len(members_data),
                        retry_indexes=retry_indexes,
                        skipped_indexes=skipped_indexes,
                        photo_pending=photo_pending,
                        photo_targets=photo_targets,
                        on_group_updated=on_group_updated,
                    )

                while retry_indexes and attempts_used < ops_budget:
                    if over_budget():
                        break
                    ri = retry_indexes.pop(0)
                    member = members_data[ri] if ri < len(members_data) else None
                    if not member:
                        continue
                    try:
                        existing = _attach_existing_recipient_to_group(
                            creds, group, group_id, member
                        )
                        if existing:
                            retry_attempts.pop(str(ri), None)
                            skipped_indexes[:] = [x for x in skipped_indexes if x != ri]
                            attempts_used += 1
                            if on_group_updated:
                                _notify_contact_import_progress(
                                    group,
                                    cursor=start,
                                    members_total=len(members_data),
                                    retry_indexes=retry_indexes,
                                    skipped_indexes=skipped_indexes,
                                    photo_pending=photo_pending,
                                    photo_targets=photo_targets,
                                    on_group_updated=on_group_updated,
                                    failed=failed,
                                )
                            continue
                        rid = create_contact_in_group(
                            creds, member, group_id, skip_photo=True
                        )
                    except Exception:
                        rid = None
                    retry_failed = _record_create_result(
                        ri,
                        rid,
                        member,
                        retry_indexes=retry_indexes,
                        retry_attempts=retry_attempts,
                        skipped_indexes=skipped_indexes,
                        photo_pending=photo_pending,
                        photo_targets=photo_targets,
                    )
                    if retry_failed:
                        failed += 1
                    elif rid:
                        _save_sync_mapping_after_create(group, member, rid)
                    attempts_used += 1
                    if on_group_updated:
                        _notify_contact_import_progress(
                            group,
                            cursor=start,
                            members_total=len(members_data),
                            retry_indexes=retry_indexes,
                            skipped_indexes=skipped_indexes,
                            photo_pending=photo_pending,
                            photo_targets=photo_targets,
                            on_group_updated=on_group_updated,
                            failed=failed,
                        )

                if start < len(members_data) and attempts_used < ops_budget:
                    mapped_start, mapped_ops = _process_sync_mappings_before_batch(
                        creds,
                        group,
                        group_id,
                        members_data,
                        start,
                        ops_budget - attempts_used,
                        photo_pending=photo_pending,
                        photo_targets=photo_targets,
                        over_budget=over_budget,
                    )
                    start = mapped_start
                    attempts_used += mapped_ops
                    group["importCursor"] = start

                if start < len(members_data) and attempts_used < ops_budget:
                    start, batch_used, batch_failed = _import_contacts_batch(
                        creds,
                        members_data,
                        group_id,
                        start,
                        ops_budget - attempts_used,
                        retry_indexes=retry_indexes,
                        retry_attempts=retry_attempts,
                        skipped_indexes=skipped_indexes,
                        photo_pending=photo_pending,
                        photo_targets=photo_targets,
                        over_budget=over_budget,
                        on_group_updated=on_group_updated,
                        group_snapshot=group,
                    )
                    attempts_used += batch_used
                    failed += batch_failed

                remaining_ops -= attempts_used
                group["importCursor"] = start
                group["retryIndexes"] = list(dict.fromkeys(retry_indexes))
                group["retryAttempts"] = retry_attempts
                group["skippedIndexes"] = list(dict.fromkeys(skipped_indexes))
                group["skippedCount"] = len(group["skippedIndexes"])
                group["photoPendingIndexes"] = list(dict.fromkeys(photo_pending))
                group["photoRetryIndexes"] = list(dict.fromkeys(photo_retry))
                group["photoRetryAttempts"] = photo_retry_attempts
                group["photoTargets"] = photo_targets
                group["photoDoneCount"] = photo_done

                actual = group_member_count(creds, group_id)
                if actual is not None:
                    group["actualCount"] = actual

                if start < len(members_data) or group["retryIndexes"]:
                    retry_label = (
                        f", retry {len(group['retryIndexes'])}"
                        if group["retryIndexes"]
                        else ""
                    )
                    fail_label = f", {failed} failed this pass" if failed else ""
                    group["status"] = (
                        f"Importing ({start}/{len(members_data)}{retry_label}{fail_label})"
                    )
                    group["importPhase"] = "contacts"
                    notify_group_progress(group)
                    continue

            if RECIPIENT_IMPORT_INCLUDE_PHOTOS:
                _raise_member_count_to_known_total(group, len(members_data))
                photo_targets = _hydrate_photo_targets(
                    group, members_data, photo_targets
                )
                photo_target_total = _photo_target_count(group, photo_targets)
                _normalize_photo_queues(
                    photo_pending, photo_retry, photo_targets, members_data
                )
                queue_stuck = _ensure_photo_work_queue(
                    members_data,
                    photo_targets,
                    photo_pending,
                    photo_retry,
                    photo_done,
                    photo_target_total,
                )
                if group.get("photoNeedsRecipientScan") or queue_stuck:
                    apply_missing_photo_backfill(
                        group, members_data, force=True, creds=creds
                    )
                    photo_pending = list(group.get("photoPendingIndexes") or [])
                    photo_retry = list(group.get("photoRetryIndexes") or [])
                    photo_retry_attempts = dict(
                        group.get("photoRetryAttempts") or {}
                    )
                    photo_done = int(group.get("photoDoneCount") or 0)
                    photo_targets = _hydrate_photo_targets(
                        group, members_data, {}
                    )
                    photo_target_total = _photo_target_count(group, photo_targets)
                if (
                    not group.get("photoNeedsRecipientScan")
                    and _photos_import_complete(
                        photo_done,
                        len(photo_targets),
                        photo_pending,
                        photo_retry,
                    )
                ):
                    skipped_count = len(group.get("skippedIndexes") or [])
                    group["status"] = (
                        f"Shared ({skipped_count} skipped)"
                        if skipped_count
                        else "Shared"
                    )
                    group["importPhase"] = "done"
                    _apply_photo_progress_fields(
                        group,
                        photo_pending=photo_pending,
                        photo_retry=photo_retry,
                        photo_retry_attempts=photo_retry_attempts,
                        photo_targets=photo_targets,
                        photo_done=photo_done,
                    )
                    notify_group_progress(group)
                    _sync_push_status_field(group)
                    continue
                photo_remaining = len(photo_pending) + len(photo_retry)
                if (
                    start >= len(members_data)
                    and not retry_indexes
                    and photo_target_total > 0
                    and photo_done < photo_target_total
                ):
                    group["importPhase"] = "photos"
                    group["status"] = f"Photos ({photo_done}/{photo_target_total})"
                    _apply_photo_progress_fields(
                        group,
                        photo_pending=photo_pending,
                        photo_retry=photo_retry,
                        photo_retry_attempts=photo_retry_attempts,
                        photo_targets=photo_targets,
                        photo_done=photo_done,
                    )
                    notify_group_progress(group)

                while True:
                    photo_remaining = len(photo_pending) + len(photo_retry)
                    if photo_remaining <= 0:
                        photo_targets = _hydrate_photo_targets(
                            group, members_data, photo_targets
                        )
                        _ensure_photo_work_queue(
                            members_data,
                            photo_targets,
                            photo_pending,
                            photo_retry,
                            photo_done,
                            len(photo_targets),
                        )
                        photo_remaining = len(photo_pending) + len(photo_retry)
                    if photo_remaining <= 0 and (
                        not photo_targets or photo_done >= len(photo_targets)
                    ):
                        break
                    if photo_remaining <= 0 or remaining_ops <= 0 or over_budget():
                        break

                    photo_ops = _photo_import_chunk_size(
                        photo_remaining, remaining_ops
                    )
                    if photo_remaining > 0 or photo_done > 0:
                        photo_total = photo_target_total or (photo_done + photo_remaining)
                        group["importPhase"] = "photos"
                        group["status"] = f"Photos ({photo_done}/{photo_total})"
                        _apply_photo_progress_fields(
                            group,
                            photo_pending=photo_pending,
                            photo_retry=photo_retry,
                            photo_retry_attempts=photo_retry_attempts,
                            photo_targets=photo_targets,
                            photo_done=photo_done,
                        )
                        notify_group_progress(group)

                    photo_used, photo_failures, photo_successes = _import_photos_pass(
                        creds,
                        members_data,
                        photo_ops,
                        photo_pending=photo_pending,
                        photo_retry=photo_retry,
                        photo_retry_attempts=photo_retry_attempts,
                        photo_targets=photo_targets,
                        over_budget=over_budget,
                        group=group,
                        on_group_updated=on_group_updated,
                        photo_done=photo_done,
                        retry_indexes=retry_indexes,
                    )
                    if retry_indexes:
                        notify_group_progress(group)
                        break
                    if photo_used <= 0:
                        had_queue = bool(photo_pending or photo_retry)
                        if not had_queue:
                            photo_targets = _hydrate_photo_targets(
                                group, members_data, photo_targets
                            )
                            _ensure_photo_work_queue(
                                members_data,
                                photo_targets,
                                photo_pending,
                                photo_retry,
                                photo_done,
                                len(photo_targets),
                            )
                        if not (photo_pending or photo_retry) or had_queue:
                            break
                        continue

                    photo_done = _reconcile_photo_done(
                        group,
                        photo_targets,
                        photo_pending,
                        photo_retry,
                        photo_done,
                    )
                    remaining_ops -= photo_used
                    photo_target_total = _apply_photo_progress_fields(
                        group,
                        photo_pending=photo_pending,
                        photo_retry=photo_retry,
                        photo_retry_attempts=photo_retry_attempts,
                        photo_targets=photo_targets,
                        photo_done=photo_done,
                    )

                    photo_remaining = len(photo_pending) + len(photo_retry)
                    photo_total = photo_target_total or (photo_done + photo_remaining)
                    fail_label = (
                        f", {photo_failures} photo failures this pass"
                        if photo_failures
                        else ""
                    )
                    group["status"] = (
                        f"Photos ({photo_done}/{photo_total}{fail_label})"
                    )
                    group["importPhase"] = "photos"
                    notify_group_progress(group)
                    if photo_remaining <= 0 and (
                        not photo_targets or photo_done >= len(photo_targets)
                    ):
                        break

            skipped_count = len(group.get("skippedIndexes") or [])
            photo_target_total = _photo_target_count(group, photo_targets)
            _normalize_photo_queues(
                photo_pending, photo_retry, photo_targets, members_data
            )
            photos_complete = not RECIPIENT_IMPORT_INCLUDE_PHOTOS or (
                not group.get("photoNeedsRecipientScan")
                and _photos_import_complete(
                    photo_done,
                    len(photo_targets),
                    photo_pending,
                    photo_retry,
                )
            )
            if photos_complete:
                group["status"] = (
                    f"Shared ({skipped_count} skipped)"
                    if skipped_count
                    else "Shared"
                )
                group["importPhase"] = "done"
            else:
                photo_total = photo_target_total or (photo_done + photo_remaining)
                group["importPhase"] = "photos"
                group["status"] = f"Photos ({photo_done}/{photo_total})"
                _apply_photo_progress_fields(
                    group,
                    photo_pending=photo_pending,
                    photo_retry=photo_retry,
                    photo_retry_attempts=photo_retry_attempts,
                    photo_targets=photo_targets,
                    photo_done=photo_done,
                )
                notify_group_progress(group)
            _sync_push_status_field(group)

        except Exception as exc:
            if is_oauth_refresh_error(exc):
                recipient = str(group.get("recipientEmail") or "").strip().lower()
                if recipient:
                    clear_recipient_connection(recipient)
                group["status"] = "Reconnect account to import"
            elif is_rate_limit_error(exc):
                # Do not turn a Google 429 into a fatal Error. Keep progress and pause.
                cursor = int(group.get("importCursor") or 0)
                total = int(group.get("memberCount") or 0)
                status = str(group.get("status") or "")
                if status.startswith("Photos") or str(group.get("importPhase") or "") == "photos":
                    pass
                elif total > 0:
                    group["status"] = f"Importing ({cursor}/{total})"
                    group["importPhase"] = "contacts"
                group["rateLimited"] = True
            else:
                group["status"] = f"Error: {exc}"
            _sync_push_status_field(group)

    return _filter_result(deduped)


def _sync_push_status_field(group: dict[str, Any]) -> None:
    """Keep pushStatus aligned with status so owner UI does not stay on Pushing."""
    status = str(group.get("status") or "")
    if status:
        group["pushStatus"] = status


def _persist_shared_group(
    recipient_email: str,
    owner_key: str,
    resource_name: str,
    group_state: dict[str, Any],
) -> None:
    app_data = db.get_app_data(recipient_email) or {}
    shared_groups: list[dict[str, Any]] = list(app_data.get("sharedGroups") or [])
    target_index = next(
        (
            i
            for i, g in enumerate(shared_groups)
            if g
            and g.get("owner") == owner_key
            and g.get("resourceName") == resource_name
        ),
        -1,
    )
    if target_index < 0:
        return
    existing = shared_groups[target_index] or {}
    persisted = deepcopy(group_state)
    persisted.pop("photoTargets", None)
    try:
        persisted["importCursor"] = max(
            int(existing.get("importCursor") or 0),
            int(persisted.get("importCursor") or 0),
        )
        persisted["photoDoneCount"] = _photo_count_cap(
            persisted,
            max(
                int(existing.get("photoDoneCount") or 0),
                int(persisted.get("photoDoneCount") or 0),
            ),
        )
        persisted["photoTargetCount"] = _photo_count_cap(
            persisted,
            max(
                int(existing.get("photoTargetCount") or 0),
                int(persisted.get("photoTargetCount") or 0),
                int(persisted.get("photoDoneCount") or 0),
            ),
        )
    except (TypeError, ValueError):
        pass
    shared_groups[target_index] = persisted
    app_data["sharedGroups"] = shared_groups
    db.save_app_data(recipient_email, app_data)


def _slim_shared_group_for_poll(group: dict[str, Any]) -> dict[str, Any]:
    """Lightweight group payload for progress polling (avoids huge photoTargets blobs)."""
    photo_pending = list(group.get("photoPendingIndexes") or [])
    photo_retry = list(group.get("photoRetryIndexes") or [])
    return {
        "name": group.get("name"),
        "owner": group.get("owner"),
        "resourceName": group.get("resourceName"),
        "shareId": group.get("shareId"),
        "memberCount": group.get("memberCount"),
        "status": group.get("status"),
        "importCursor": group.get("importCursor"),
        "importPhase": group.get("importPhase"),
        "photoDoneCount": group.get("photoDoneCount"),
        "photoTargetCount": group.get("photoTargetCount"),
        "photoPendingCount": len(photo_pending),
        "photoPendingIndexes": photo_pending,
        "photoRetryIndexes": photo_retry,
        "skippedCount": group.get("skippedCount"),
        "actualCount": group.get("actualCount"),
        "retryIndexes": list(group.get("retryIndexes") or []),
        "created": group.get("created"),
    }


def get_recipient_shared_groups(recipient_email: str) -> dict[str, Any]:
    from .app_data import _filter_dismissed_shared_groups

    app_data = db.get_app_data(recipient_email) or {}
    shared_groups = [
        _slim_shared_group_for_poll(g)
        for g in _filter_dismissed_shared_groups(app_data)
        if g
    ]
    return {"sharedGroups": shared_groups}


def _visible_shared_groups(recipient_email: str) -> list[dict[str, Any]]:
    from .app_data import _filter_dismissed_shared_groups

    app_data = db.get_app_data(recipient_email) or {}
    return _filter_dismissed_shared_groups(app_data)


def process_shared_import_for_group(
    recipient_email: str,
    owner_key: str,
    resource_name: str,
    max_ops: int = 200,
    time_budget_ms: int | None = None,
) -> dict[str, Any]:
    from ..google_auth import recipient_credentials

    creds = recipient_credentials(recipient_email)
    if not creds:
        raise PermissionError("Connect your account first, then retry import.")

    lock = _lock_for(recipient_email)
    if not lock.acquire(blocking=False):
        return {"sharedGroups": _visible_shared_groups(recipient_email), "busy": True}

    try:
        app_data = db.get_app_data(recipient_email) or {}
        shared_groups: list[dict[str, Any]] = list(
            app_data.get("sharedGroups") or []
        )
        owner_key_normalized = db.normalize_email(owner_key)
        target_index = next(
            (
                i
                for i, g in enumerate(shared_groups)
                if g
                and db.normalize_email(str(g.get("owner") or "")) == owner_key_normalized
                and g.get("resourceName") == resource_name
            ),
            -1,
        )
        if target_index < 0:
            from .recipient_recovery import repair_recipient_app_data_from_staged

            if repair_recipient_app_data_from_staged(recipient_email):
                app_data = db.get_app_data(recipient_email) or {}
                shared_groups = list(app_data.get("sharedGroups") or [])
                target_index = next(
                    (
                        i
                        for i, g in enumerate(shared_groups)
                        if g
                        and db.normalize_email(str(g.get("owner") or "")) == owner_key_normalized
                        and g.get("resourceName") == resource_name
                    ),
                    -1,
                )
            if target_index < 0:
                return {"sharedGroups": _visible_shared_groups(recipient_email), "busy": False}

        target = shared_groups[target_index]
        target["recipientEmail"] = recipient_email
        progress_cb = lambda group_state: _persist_shared_group(
            recipient_email, owner_key, resource_name, group_state
        )
        processed = create_shared_groups(
            [deepcopy(target)],
            creds,
            max_ops=max_ops,
            time_budget_ms=resolve_import_time_budget_ms(time_budget_ms),
            on_group_updated=progress_cb,
        )

        rate_limited = False
        if processed:
            shared_groups = [
                g
                for g in shared_groups
                if not (
                    g
                    and g.get("owner") == owner_key
                    and g.get("resourceName") == resource_name
                )
            ]
            insert_at = min(target_index, len(shared_groups))
            cleaned = deepcopy(processed[0])
            rate_limited = bool(cleaned.pop("rateLimited", None))
            cleaned.pop("photoTargets", None)
            shared_groups.insert(insert_at, cleaned)

        app_data["sharedGroups"] = shared_groups
        db.save_app_data(recipient_email, app_data)
        return {
            "sharedGroups": _visible_shared_groups(recipient_email),
            "busy": False,
            "rate_limited": rate_limited,
        }
    finally:
        lock.release()
