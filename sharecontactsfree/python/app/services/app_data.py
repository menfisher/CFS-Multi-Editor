from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _contact_group_resource(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("contactGroups/"):
        return text
    return ""

import logging

from ..config import PUBLIC_WEB_URL, SEND_SHARE_EMAILS
from .. import db
from ..google_auth import owner_credentials, recipient_credentials
from ..people_api import (
    get_group_member_ids,
    get_people_batch,
    get_profile,
    is_rate_limit_error,
    list_contact_groups,
)
from .recipient_import import _clear_recipient_import_state
from .recipient_recovery import maybe_recover_recipient_shared_groups

logger = logging.getLogger(__name__)


def compact_app_data(
    app_data: dict[str, Any], existing: dict[str, Any] | None = None
) -> dict[str, Any]:
    compact_groups = {}
    for resource_name, group in (app_data.get("groups") or {}).items():
        entry: dict[str, Any] = {"shared": group.get("shared") or []}
        if group.get("syncProgress"):
            entry["syncProgress"] = group.get("syncProgress")
        if group.get("status"):
            entry["status"] = group.get("status")
        if str(group.get("shareId") or "").strip():
            entry["shareId"] = str(group.get("shareId")).strip()
        compact_groups[resource_name] = entry
    result = {
        "groups": compact_groups,
        "sharedGroups": app_data.get("sharedGroups") or [],
    }
    settings_name = str(
        (app_data.get("sharedContactsGroupName") if app_data else "")
        or ((existing or {}).get("sharedContactsGroupName") if existing else "")
        or ""
    ).strip()
    settings_resource = str(
        (app_data.get("sharedContactsGroupResourceName") if app_data else "")
        or ((existing or {}).get("sharedContactsGroupResourceName") if existing else "")
        or ""
    ).strip()
    if settings_name:
        result["sharedContactsGroupName"] = settings_name
    if settings_resource:
        result["sharedContactsGroupResourceName"] = settings_resource
    if existing:
        if existing.get("invitedByOwner"):
            result["invitedByOwner"] = existing["invitedByOwner"]
        dismissed = [
            str(key).strip()
            for key in (existing.get("dismissedSharedGroups") or [])
            if str(key).strip()
        ]
        for key in app_data.get("dismissedSharedGroups") or []:
            normalized = str(key).strip()
            if normalized and normalized not in dismissed:
                dismissed.append(normalized)
        if dismissed:
            result["dismissedSharedGroups"] = dismissed
    elif app_data.get("dismissedSharedGroups"):
        result["dismissedSharedGroups"] = list(app_data.get("dismissedSharedGroups") or [])
    return result


def _append_dismissal_keys(
    keys: list[str],
    *,
    owner: str = "",
    resource_name: str = "",
    share_id: str = "",
    created: str = "",
    name: str = "",
) -> list[str]:
    owner = db.normalize_email(owner)
    if owner == "unknown":
        owner = ""
    resource_name = str(resource_name or "").strip()
    share_id = str(share_id or "").strip()
    created = str(created or "").strip()
    name = str(name or "").strip().lower()
    if created:
        keys.append(f"created:{created}")
    if share_id:
        keys.append(f"share-id:{share_id}")
        if owner:
            keys.append(f"share:{owner}|{share_id}")
        else:
            keys.append(f"share:|{share_id}")
    if resource_name:
        if owner:
            keys.append(f"group:{owner}|{resource_name}")
        else:
            keys.append(f"group:|{resource_name}")
    if name:
        keys.append(f"name:{name}")
        if owner:
            keys.append(f"name:{owner}|{name}")
    return list(dict.fromkeys(key for key in keys if key))


def _all_dismissal_keys_for_group(group: dict[str, Any]) -> list[str]:
    return _append_dismissal_keys(
        [],
        owner=str(group.get("owner") or ""),
        resource_name=str(group.get("resourceName") or ""),
        share_id=str(group.get("shareId") or ""),
        created=str(group.get("created") or ""),
        name=str(group.get("name") or ""),
    )


def _shared_group_dismissal_key(group: dict[str, Any]) -> str:
    keys = _all_dismissal_keys_for_group(group)
    return keys[0] if keys else ""


def _record_dismissal_keys(
    recipient_data: dict[str, Any], keys: list[str]
) -> None:
    dismissed = [
        str(item).strip()
        for item in (recipient_data.get("dismissedSharedGroups") or [])
        if str(item).strip()
    ]
    for key in keys:
        normalized = str(key or "").strip()
        if normalized and normalized not in dismissed:
            dismissed.append(normalized)
    recipient_data["dismissedSharedGroups"] = dismissed


def _record_dismissed_shared_group(
    recipient_data: dict[str, Any], group: dict[str, Any]
) -> None:
    _record_dismissal_keys(recipient_data, _all_dismissal_keys_for_group(group))


def _record_dismissal_request(
    recipient_data: dict[str, Any],
    owner_key: str,
    resource_name: str,
    share_id: str = "",
    created: str = "",
    group_name: str = "",
) -> None:
    keys = _append_dismissal_keys(
        [],
        owner=owner_key,
        resource_name=resource_name,
        share_id=share_id,
        created=created,
        name=group_name,
    )
    _record_dismissal_keys(recipient_data, keys)


def _shared_group_matches_dismissal_request(
    group: dict[str, Any],
    owner_key: str,
    resource_name: str,
    share_id: str = "",
    created: str = "",
    group_name: str = "",
) -> bool:
    owner_key_normalized = db.normalize_email(owner_key)
    resource_name = str(resource_name or "").strip()
    share_id = str(share_id or "").strip()
    created = str(created or "").strip()
    group_name_normalized = str(group_name or "").strip().lower()

    group_owner = db.normalize_email(str(group.get("owner") or ""))
    group_resource = str(group.get("resourceName") or "").strip()
    group_share_id = str(group.get("shareId") or "").strip()
    group_created = str(group.get("created") or "").strip()
    group_name_value = str(group.get("name") or "").strip().lower()

    if share_id and group_share_id == share_id:
        return True
    if created and group_created == created:
        return True
    if resource_name and group_resource == resource_name:
        if not owner_key_normalized or group_owner in ("", "unknown", owner_key_normalized):
            return True
    # Name is last-resort only for recovered rows with no share/resource id.
    # Matching by name would hide every "AlMsLa (Shared)" row from this owner.
    if share_id or resource_name or created:
        return False
    if group_name_normalized and group_name_value == group_name_normalized:
        if not owner_key_normalized or group_owner in ("", "unknown", owner_key_normalized):
            return True
    return False


def _split_shared_groups_for_dismissal(
    shared_groups: list[dict[str, Any]],
    owner_key: str,
    resource_name: str,
    share_id: str = "",
    created: str = "",
    group_name: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for group in shared_groups:
        if _shared_group_matches_dismissal_request(
            group, owner_key, resource_name, share_id, created, group_name
        ):
            removed.append(group)
        else:
            kept.append(group)
    return kept, removed


def _filter_dismissed_shared_groups(
    recipient_data: dict[str, Any],
    shared_groups: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    dismissed = {
        str(key).strip()
        for key in (recipient_data.get("dismissedSharedGroups") or [])
        if str(key).strip()
    }
    groups = list(
        shared_groups
        if shared_groups is not None
        else recipient_data.get("sharedGroups") or []
    )
    if not dismissed:
        return groups
    return [group for group in groups if not _group_is_hidden_by_dismissal(group, dismissed)]


def _dismissal_identity_keys(group: dict[str, Any]) -> list[str]:
    return _append_dismissal_keys(
        [],
        owner=str(group.get("owner") or ""),
        resource_name=str(group.get("resourceName") or ""),
        share_id=str(group.get("shareId") or ""),
        created=str(group.get("created") or ""),
        name="",
    )


def _group_is_hidden_by_dismissal(group: dict[str, Any], dismissed: set[str]) -> bool:
    if dismissed.intersection(_dismissal_identity_keys(group)):
        return True
    has_identity = bool(
        str(group.get("shareId") or "").strip()
        or str(group.get("resourceName") or "").strip()
        or str(group.get("created") or "").strip()
    )
    if has_identity:
        return False
    name_keys = _append_dismissal_keys(
        [],
        owner=str(group.get("owner") or ""),
        name=str(group.get("name") or ""),
    )
    return bool(dismissed.intersection(name_keys))


def retire_vanished_owner_share(
    recipient_email: str,
    owner: str,
    resource_name: str,
) -> int:
    """Drop a leftover recipient row whose owner Google group is gone.

    Identity dismiss keys only (no name:), so AlMsLa (Shared) stays visible.
    Does not delete recipient Google contacts or other shares' mappings.
    """
    recipient_email = db.normalize_email(recipient_email)
    owner = db.normalize_email(owner)
    resource_name = str(resource_name or "").strip()
    if not recipient_email or not owner or not resource_name:
        return 0
    data = db.get_app_data(recipient_email) or {}
    kept: list[dict[str, Any]] = []
    removed = 0
    keys: list[str] = []
    for group in data.get("sharedGroups") or []:
        group_owner = db.normalize_email(str(group.get("owner") or ""))
        group_resource = str(group.get("resourceName") or "").strip()
        if group_owner == owner and group_resource == resource_name:
            keys.extend(_dismissal_identity_keys(group))
            removed += 1
            continue
        kept.append(group)
    if not keys:
        keys = _append_dismissal_keys(
            [],
            owner=owner,
            resource_name=resource_name,
            share_id="",
            created="",
            name="",
        )
    _record_dismissal_keys(data, keys)
    data["sharedGroups"] = kept
    db.cancel_push_jobs(owner, resource_name, recipient_email)
    db.save_app_data(recipient_email, data)
    return removed


def _clear_dismissal_for_group(recipient_data: dict[str, Any], group: dict[str, Any]) -> None:
    """Owner share/sync should unhide this group after a prior Dismiss."""
    drop = set(_all_dismissal_keys_for_group(group))
    dismissed = [
        str(key).strip()
        for key in (recipient_data.get("dismissedSharedGroups") or [])
        if str(key).strip() and str(key).strip() not in drop
    ]
    recipient_data["dismissedSharedGroups"] = dismissed


def _find_shared_group_for_removal(
    shared_groups: list[dict[str, Any]],
    owner_key: str,
    resource_name: str,
    share_id: str = "",
    created: str = "",
    group_name: str = "",
) -> tuple[int, dict[str, Any] | None]:
    owner_key_normalized = db.normalize_email(owner_key)
    resource_name = str(resource_name or "").strip()
    share_id = str(share_id or "").strip()
    created = str(created or "").strip()
    group_name_normalized = str(group_name or "").strip().lower()

    for index, group in enumerate(shared_groups):
        group_resource = str(group.get("resourceName") or "").strip()
        group_share_id = str(group.get("shareId") or "").strip()
        group_created = str(group.get("created") or "").strip()
        group_owner = db.normalize_email(str(group.get("owner") or ""))

        if share_id and group_share_id == share_id:
            if group_owner in ("", "unknown", owner_key_normalized):
                return index, group
        if created and group_created == created:
            return index, group
        if group_resource != resource_name:
            continue
        if group_owner == owner_key_normalized:
            return index, group
        if group_owner in ("", "unknown") and owner_key_normalized:
            return index, group

    if group_name_normalized and owner_key_normalized:
        name_matches = [
            index
            for index, group in enumerate(shared_groups)
            if db.normalize_email(str(group.get("owner") or ""))
            in ("", "unknown", owner_key_normalized)
            and str(group.get("name") or "").strip().lower() == group_name_normalized
        ]
        if len(name_matches) == 1:
            return name_matches[0], shared_groups[name_matches[0]]
    return -1, None


def _clear_owner_group_sync_fields(group: dict[str, Any]) -> None:
    for key in ("ownerSyncRunId", "ownerSyncStartedAt", "syncProgress", "status"):
        group.pop(key, None)


def _resolve_shared_group_owner(
    group: dict[str, Any], invited_by_owner: dict[str, Any]
) -> str:
    owner = str(group.get("owner") or "").strip().lower()
    if owner and owner != "unknown":
        return owner
    invited_owners = sorted(
        {
            str(key).strip().lower()
            for key in (invited_by_owner or {}).keys()
            if str(key).strip()
        }
    )
    if len(invited_owners) == 1:
        return invited_owners[0]
    return owner


def _normalize_groups_pending_recipient_connect(
    shared_groups: list[dict[str, Any]],
) -> None:
    """Do not show import complete before recipient OAuth connect."""
    for group in shared_groups:
        status = str(group.get("status") or "")
        if not status.startswith(
            ("Shared", "Photos", "Ready", "Importing", "Pushing", "Queued")
        ):
            continue
        member_count = int(group.get("memberCount") or 0)
        cursor = int(group.get("importCursor") or 0)
        if member_count > 0:
            group["status"] = f"Connect account to import ({cursor}/{member_count})"
        else:
            group["status"] = "Connect account to import"
        group.pop("importPhase", None)


def _normalize_shared_groups_for_display(
    shared_groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Lightweight status labels for the table — no Google API calls."""
    for group in shared_groups:
        if not group:
            continue
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
    return shared_groups


def load_app(owner_email: str, view_mode: str, *, recover: bool = False) -> dict[str, Any]:
    creds = owner_credentials(owner_email)
    if not creds:
        raise PermissionError("Not signed in. Open /auth/owner/login")

    profile = get_profile(creds)
    stored = db.get_app_data(owner_email) or {}
    groups_dict: dict[str, Any] = {}
    shared_groups: list[Any] = []

    if view_mode == "shared":
        shared_groups = _filter_dismissed_shared_groups(stored)
        invited_by_owner = dict(stored.get("invitedByOwner") or {})
        recipient_connected = recipient_credentials(owner_email) is not None
        if recover and recipient_connected:
            recovered: list[Any] | None
            try:
                recovered = maybe_recover_recipient_shared_groups(
                    owner_email, shared_groups
                )
            except Exception as exc:
                # Recovery reads the People API (list groups + read members). When Google
                # rate-limits (HTTP 429) or otherwise errors, don't fail the whole page —
                # fall back to the groups already stored locally so the recipient still
                # sees their shared groups and can retry in a minute.
                if is_rate_limit_error(exc):
                    logger.info("Recipient recovery skipped (Google rate limit): %s", exc)
                else:
                    logger.warning("Recipient recovery failed: %s", exc)
                recovered = None
            if recovered is not None:
                shared_groups = recovered
                if shared_groups and not (stored.get("sharedGroups") or []):
                    stored = db.get_app_data(owner_email) or stored
                    invited_by_owner = dict(stored.get("invitedByOwner") or invited_by_owner)
                stored = db.get_app_data(owner_email) or stored
                shared_groups = _filter_dismissed_shared_groups(stored, shared_groups)
            else:
                shared_groups = _normalize_shared_groups_for_display(shared_groups)
        elif not recipient_connected:
            _normalize_groups_pending_recipient_connect(shared_groups)
            for group in shared_groups:
                status = str(group.get("status") or "").lower()
                if (
                    "refresh the access token" in status
                    or status.startswith("error:")
                    or "invalid_grant" in status
                ):
                    member_count = int(group.get("memberCount") or 0)
                    cursor = int(group.get("importCursor") or 0)
                    if member_count > 0:
                        group["status"] = f"Connect account to import ({cursor}/{member_count})"
                    else:
                        group["status"] = "Connect account to import"
                    group.pop("importPhase", None)
        else:
            shared_groups = _normalize_shared_groups_for_display(shared_groups)
        for group in shared_groups:
            resolved_owner = _resolve_shared_group_owner(group, invited_by_owner)
            if resolved_owner:
                group["owner"] = resolved_owner
    else:
        api_groups = list_contact_groups(creds)
        cached_groups = stored.get("groups") or {}
        for g in api_groups:
            rn = g["resourceName"]
            cached = cached_groups.get(rn) or {}
            g["shared"] = cached.get("shared") or []
            if cached.get("syncProgress"):
                g["syncProgress"] = cached.get("syncProgress")
            if cached.get("status"):
                g["status"] = cached.get("status")
            groups_dict[rn] = g
        shared_groups = list(stored.get("sharedGroups") or [])

    if view_mode != "shared":
        recipient_connected = recipient_credentials(owner_email) is not None
    # recipient_connected already set for shared view above

    from .card_repair import card_repair_summary_line, get_card_repair_status

    app_data = {
        "groups": groups_dict,
        "sharedGroups": shared_groups,
        "recipientConnected": recipient_connected,
        "profile": profile,
        "cardRepair": get_card_repair_status(),
        "cardRepairSummary": card_repair_summary_line(),
        "appConfig": {
            "sendShareEmails": SEND_SHARE_EMAILS,
            "inviteUrl": build_recipient_invite_url(owner_email if view_mode == "shared" else ""),
            "sharedContactsGroupName": str(stored.get("sharedContactsGroupName") or "").strip(),
            "sharedContactsGroupResourceName": str(
                stored.get("sharedContactsGroupResourceName") or ""
            ).strip(),
        },
    }
    if view_mode == "shared":
        dismissed = [
            str(key).strip()
            for key in (stored.get("dismissedSharedGroups") or [])
            if str(key).strip()
        ]
        if dismissed:
            app_data["dismissedSharedGroups"] = dismissed
    to_save = compact_app_data(app_data, stored)
    if view_mode == "shared" and stored.get("groups"):
        to_save["groups"] = stored["groups"]
    if view_mode == "shared" and recipient_connected:
        from .push_worker import reconcile_stale_share_rows

        if reconcile_stale_share_rows(owner_email):
            stored = db.get_app_data(owner_email) or stored
            shared_groups = _filter_dismissed_shared_groups(stored)
            app_data["sharedGroups"] = shared_groups
            to_save = compact_app_data(app_data, stored)
            if stored.get("groups"):
                to_save["groups"] = stored["groups"]
    db.save_app_data(owner_email, to_save)
    return app_data


def build_recipient_invite_url(recipient_email: str = "") -> str:
    base = f"{PUBLIC_WEB_URL.rstrip('/')}/?mode=shared"
    recipient = db.normalize_email(recipient_email)
    if recipient:
        return f"{base}&{urlencode({'email': recipient})}"
    return base


def _has_prior_share_invite(owner_email: str, recipient_email: str) -> bool:
    recipient_data = db.get_app_data(recipient_email) or {}
    invited = recipient_data.get("invitedByOwner") or {}
    if owner_email in invited:
        return True
    shared_groups = recipient_data.get("sharedGroups") or []
    return any(g.get("owner") == owner_email for g in shared_groups)


def _recipient_contacts_imported(target: dict[str, Any]) -> bool:
    """True when this recipient already has contacts in Google (photos may still be pending)."""
    member_count = int(target.get("memberCount") or 0)
    if member_count <= 0:
        return False
    status = str(target.get("status") or "")
    if status.startswith("Shared"):
        return True
    if status.startswith("Photos") or status.startswith("Importing photos"):
        return True
    cursor = int(target.get("importCursor") or 0)
    if cursor >= member_count:
        return True
    created = str(target.get("created") or "")
    actual = int(target.get("actualCount") or 0)
    if created.startswith("contactGroups/") and actual >= member_count:
        return True
    return False


def _reconcile_owner_sync_status(
    target: dict[str, Any], share_id: str, member_count: int
) -> bool:
    """Upgrade stale 'Waiting for owner sync' when contacts are already staged."""
    status = str(target.get("status") or "")
    if not status.startswith("Waiting for owner sync"):
        return False
    if not share_id:
        return False
    stored_count = db.count_shared_contacts(share_id)
    if stored_count <= 0:
        return False
    new_status = f"Ready ({stored_count}/{member_count})"
    target["status"] = new_status
    target["pushStatus"] = new_status
    return True


def _recipient_skip_owner_push(
    target: dict[str, Any],
    *,
    share_id: str,
    owner: str,
    resource_name: str,
    recipient_email: str,
    reset: bool,
    owner_member_count: int | None = None,
) -> bool:
    """Skip re-push only when recipient already has every current owner member.

    Previously, status \"Shared\" always skipped — so Sync never delivered contacts
    added to the owner group after the first import finished.
    """
    if reset:
        return False

    current_count = int(
        owner_member_count
        if owner_member_count is not None
        else (target.get("memberCount") or 0)
    )
    mapped = 0
    if share_id:
        by_owner, _ = db.load_sync_mappings(
            share_id, owner, resource_name, recipient_email
        )
        mapped = len(by_owner)

    # Owner group grew, shrank with leftover orphans, or prior import left gaps.
    if current_count > 0 and mapped != current_count:
        return False

    actual = int(target.get("actualCount") or 0)
    cursor = int(target.get("importCursor") or 0)
    if current_count > 0 and max(actual, cursor) < current_count:
        return False

    if _recipient_contacts_imported(target):
        from .recipient_import import group_needs_photo_backfill

        if group_needs_photo_backfill(target):
            return False
        return True
    if share_id and current_count > 0 and mapped >= current_count:
        return True
    return False


def _recipient_is_fully_shared(target: dict[str, Any]) -> bool:
    status = str(target.get("status") or "")
    if not status.startswith("Shared"):
        return False
    if target.get("importPhase") == "photos":
        return False
    if target.get("photoPendingIndexes") or target.get("photoRetryIndexes"):
        return False
    from .recipient_import import group_needs_photo_backfill

    if group_needs_photo_backfill(target):
        return False
    return True


def _maybe_enqueue_recipient_push(
    owner_email: str,
    resource_name: str,
    recipient_email: str,
    target: dict[str, Any],
    *,
    stored_count: int,
    member_count: int,
) -> str:
    from .push_worker import enqueue_full_group_push

    if not recipient_credentials(recipient_email):
        if not str(target.get("status") or "").startswith(("Importing", "Photos", "Pushing")):
            target["status"] = f"Ready ({stored_count}/{member_count})"
            target["pushStatus"] = target["status"]
        return f"Ready — waiting for recipient to connect ({stored_count}/{member_count})"

    enqueue_full_group_push(owner_email, resource_name, recipient_email)
    cursor = int(target.get("importCursor") or 0)
    if not str(target.get("status") or "").startswith(("Importing", "Photos")):
        target["status"] = (
            f"Pushing ({cursor}/{member_count})" if member_count else "Pushing"
        )
    target["pushStatus"] = "Queued"
    return f"Pushing (queued) ({stored_count}/{member_count})"


def enqueue_pending_pushes_for_recipient(recipient_email: str) -> list[int]:
    """After recipient OAuth connect, push any groups already staged by owner."""
    from .push_worker import enqueue_full_group_push

    recipient_email = db.normalize_email(recipient_email)
    if not recipient_credentials(recipient_email):
        return []

    recipient_data = db.get_app_data(recipient_email) or {}
    shared_groups = list(recipient_data.get("sharedGroups") or [])
    job_ids: list[int] = []
    changed = False

    for group in shared_groups:
        owner = str(group.get("owner") or "")
        resource_name = str(group.get("resourceName") or "")
        share_id = str(group.get("shareId") or "")
        status = str(group.get("status") or "")
        if not owner or not resource_name or not share_id:
            continue
        if _recipient_skip_owner_push(
            group,
            share_id=share_id,
            owner=owner,
            resource_name=resource_name,
            recipient_email=recipient_email,
            reset=False,
        ):
            continue
        if status.startswith("Waiting for owner sync"):
            continue
        if db.count_shared_contacts(share_id) <= 0:
            continue
        if db.has_active_push_job(owner, resource_name, recipient_email):
            continue

        job_id = enqueue_full_group_push(owner, resource_name, recipient_email)
        job_ids.append(job_id)
        member_count = int(group.get("memberCount") or 0)
        cursor = int(group.get("importCursor") or 0)
        group["pushStatus"] = "Queued"
        if not status.startswith(("Importing", "Photos")):
            group["status"] = (
                f"Pushing ({cursor}/{member_count})" if member_count else "Pushing"
            )
        changed = True

    if changed:
        recipient_data["sharedGroups"] = shared_groups
        db.save_app_data(recipient_email, recipient_data)
    return job_ids


def _existing_share_id_for_resource(
    owner_email: str,
    resource_name: str,
    recipient_emails: list[str],
) -> str:
    """Find a stable share_id already tied to (owner, resource) to avoid minting a new one.

    Prefers a share_id any recipient row still references, then the staged snapshot with
    the most existing recipient mappings (the 'real' one). Reusing it on re-share prevents
    orphaned staged snapshots and duplicate recipient rows.
    """
    owner_email = db.normalize_email(owner_email)
    resource_name = str(resource_name or "").strip()
    for email in recipient_emails:
        recipient_data = db.get_app_data(email) or {}
        for group in recipient_data.get("sharedGroups") or []:
            if (
                db.normalize_email(str(group.get("owner") or "")) == owner_email
                and str(group.get("resourceName") or "").strip() == resource_name
            ):
                share_id = str(group.get("shareId") or "").strip()
                if share_id:
                    return share_id

    best_share_id = ""
    best_score = -1
    for staged in db.list_staged_share_groups_for_owner(owner_email):
        if str(staged.get("group_resource_name") or "").strip() != resource_name:
            continue
        share_id = str(staged.get("share_id") or "").strip()
        if not share_id:
            continue
        score = sum(
            db.count_sync_mappings_for_share(email, owner_email, share_id)
            for email in recipient_emails
        )
        if score > best_score:
            best_score = score
            best_share_id = share_id
    return best_share_id


def _canonical_share_id_for_resource(
    owner_group: dict[str, Any],
    owner_email: str,
    resource_name: str,
    recipient_emails: list[str],
) -> str:
    """Stable share_id per (owner, resource), stored on the owner group entry."""
    canonical = str(owner_group.get("shareId") or "").strip()
    if not canonical:
        canonical = _existing_share_id_for_resource(
            owner_email, resource_name, recipient_emails
        )
    if not canonical:
        canonical = str(uuid.uuid4())
    owner_group["shareId"] = canonical
    return canonical


def _upsert_recipient_shared_group(
    recipient_email: str,
    *,
    owner_email: str,
    resource_name: str,
    name: str,
    member_count: int,
    share_id: str,
) -> dict[str, Any]:
    """Re-read then add/update one shared group so concurrent meeting shares are not lost."""
    from .recipient_import import _lock_for

    email = db.normalize_email(recipient_email)
    owner_email = db.normalize_email(owner_email)
    resource_name = str(resource_name or "").strip()
    share_id = str(share_id or "").strip()
    name = str(name or "").strip() or "Shared group"
    member_count = int(member_count or 0)
    lock = _lock_for(email)
    with lock:
        recipient_data = db.get_app_data(email) or {}
        shared_groups = list(recipient_data.get("sharedGroups") or [])
        idx = next(
            (
                i
                for i, group in enumerate(shared_groups)
                if db.normalize_email(str(group.get("owner") or "")) == owner_email
                and str(group.get("resourceName") or "") == resource_name
            ),
            -1,
        )
        if idx >= 0:
            existing = dict(shared_groups[idx] or {})
            share_id = str(existing.get("shareId") or share_id)
            existing["name"] = name
            existing["memberCount"] = member_count or int(existing.get("memberCount") or 0)
            existing["shareId"] = share_id
            shared_groups[idx] = existing
            row = existing
        else:
            row = {
                "owner": owner_email,
                "name": name,
                "resourceName": resource_name,
                "members": [],
                "memberCount": member_count,
                "shareId": share_id,
                "importCursor": 0,
                "status": f"Waiting for owner sync (0/{member_count})",
                "created": "",
            }
            shared_groups.append(row)
        recipient_data["sharedGroups"] = shared_groups
        _clear_dismissal_for_group(recipient_data, row)
        invited = recipient_data.setdefault("invitedByOwner", {})
        invited[owner_email] = _iso_now()
        db.save_app_data(email, recipient_data, flush=True)
        return row


def share_contact_group(
    owner_email: str,
    resource_name: str,
    name: str,
    members: list[str],
    member_count: int,
    emails: list[str],
    notify_recipients: bool = True,
) -> dict[str, Any]:
    creds = owner_credentials(owner_email)
    if not creds:
        raise PermissionError("Not signed in.")

    emails = sorted({e.strip().lower() for e in emails if e and e.strip()})
    member_ids = get_group_member_ids(creds, resource_name)
    members_data_by_id = get_people_batch(creds, member_ids)
    members_data = [members_data_by_id[m] for m in member_ids if m in members_data_by_id]

    app_data = db.get_app_data(owner_email) or {"groups": {}, "sharedGroups": []}
    groups = app_data.setdefault("groups", {})
    group = groups.setdefault(resource_name, {"shared": []})
    existing_shared = set(group.get("shared") or [])
    allowed = []
    manual_invite_reminder: list[str] = []
    for email in emails:
        if email in existing_shared:
            allowed.append(email)
            continue
        allowed.append(email)
        if not notify_recipients and not _has_prior_share_invite(owner_email, email):
            manual_invite_reminder.append(email)

    group["shared"] = sorted(existing_shared.union(allowed))
    canonical_share_id = _canonical_share_id_for_resource(
        group, owner_email, resource_name, allowed
    )
    from .invite_email import invite_display_name

    invite_name = invite_display_name(
        name,
        settings_name=str(app_data.get("sharedContactsGroupName") or "").strip(),
        group_resource=resource_name,
        settings_resource=str(app_data.get("sharedContactsGroupResourceName") or "").strip(),
    )
    db.save_app_data(owner_email, app_data)

    share_status = {
        "sent": len(allowed),
        "notified": 0,
        "blockedNoInvite": len(manual_invite_reminder),
        "blockedNoInviteEmails": manual_invite_reminder,
        "requiresOwnerSync": True,
        "requestedContacts": len(member_ids),
        "preparedContacts": len(members_data),
        "missingContacts": len(member_ids) - len(members_data),
        "inviteGroupName": invite_name,
    }

    for email in allowed:
        _upsert_recipient_shared_group(
            email,
            owner_email=owner_email,
            resource_name=resource_name,
            name=invite_name,
            member_count=len(member_ids),
            share_id=canonical_share_id,
        )
        if notify_recipients:
            share_status["notified"] += 1
            _send_invite_email(owner_email, email, invite_name, creds)

    return {"groups": app_data.get("groups", {}), "shareStatus": share_status}


def _send_invite_email(owner: str, recipient: str, group_name: str, creds) -> None:
    from ..config import SEND_SHARE_EMAILS
    from .invite_email import invite_email_html

    if not SEND_SHARE_EMAILS:
        return
    try:
        from googleapiclient.discovery import build

        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        url = build_recipient_invite_url(recipient)
        body = invite_email_html(owner, recipient, group_name, url)
        import base64
        from email.mime.text import MIMEText

        msg = MIMEText(body, "html")
        msg["to"] = recipient
        msg["from"] = owner
        msg["subject"] = "Share Google Contacts"
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
    except Exception:
        pass  # best effort


def remove_recipient_shared_group(
    recipient_email: str,
    owner_key: str,
    resource_name: str,
    share_id: str = "",
    created: str = "",
    group_name: str = "",
) -> dict[str, Any]:
    recipient_email = db.normalize_email(recipient_email)
    owner_key = db.normalize_email(owner_key)
    resource_name = str(resource_name or "").strip()
    share_id = str(share_id or "").strip()
    created = str(created or "").strip()
    group_name = str(group_name or "").strip()
    if not share_id and not created and not resource_name and not group_name:
        raise ValueError("Group identity is required.")

    recipient_data = db.get_app_data(recipient_email) or {}
    shared_groups = list(recipient_data.get("sharedGroups") or [])
    owner_key_normalized = db.normalize_email(owner_key)
    kept_groups, removed_groups = _split_shared_groups_for_dismissal(
        shared_groups,
        owner_key,
        resource_name,
        share_id=share_id,
        created=created,
        group_name=group_name,
    )
    removed_group = removed_groups[0] if removed_groups else None

    for group in removed_groups:
        _record_dismissed_shared_group(recipient_data, group)
    _record_dismissal_request(
        recipient_data,
        owner_key,
        resource_name,
        share_id=share_id,
        created=created,
        group_name=group_name,
    )

    if removed_group:
        share_id = str(removed_group.get("shareId") or share_id).strip()
        resource_for_jobs = str(removed_group.get("resourceName") or resource_name).strip()
        db.cancel_push_jobs(owner_key, resource_for_jobs, recipient_email)
        if share_id:
            db.delete_sync_mapping_rows(
                share_id, owner_key, resource_for_jobs, recipient_email
            )

    recipient_data["sharedGroups"] = _filter_dismissed_shared_groups(recipient_data)
    invited = recipient_data.get("invitedByOwner") or {}
    if not any(
        db.normalize_email(str(group.get("owner") or "")) == owner_key_normalized
        for group in kept_groups
    ):
        invited.pop(owner_key_normalized, None)
        invited.pop(owner_key, None)
    if invited:
        recipient_data["invitedByOwner"] = invited
    else:
        recipient_data.pop("invitedByOwner", None)
    db.save_app_data(recipient_email, recipient_data)

    owner_data = db.get_app_data(owner_key_normalized) or db.get_app_data(owner_key)
    if owner_data and isinstance(owner_data.get("groups"), dict):
        group = dict(owner_data.get("groups", {}).get(resource_name) or {})
        if group:
            group["shared"] = [
                e
                for e in (group.get("shared") or [])
                if db.normalize_email(str(e or "")) != recipient_email
            ]
            if not group["shared"]:
                _clear_owner_group_sync_fields(group)
            owner_data.setdefault("groups", {})[resource_name] = group
            db.save_app_data(owner_key_normalized, compact_app_data(owner_data, owner_data))

    visible_groups = _filter_dismissed_shared_groups(recipient_data)
    dismissed = [
        str(key).strip()
        for key in (recipient_data.get("dismissedSharedGroups") or [])
        if str(key).strip()
    ]
    return {"sharedGroups": visible_groups, "dismissedSharedGroups": dismissed}


def stop_share_contact_group(
    owner_email: str, recipient_email: str, resource_name: str
) -> dict[str, Any]:
    owner_email = owner_email.strip().lower()
    recipient_email = recipient_email.strip().lower()
    resource_name = str(resource_name or "").strip()

    recipient_data = db.get_app_data(recipient_email) or {}
    recipient_data["sharedGroups"] = [
        g
        for g in (recipient_data.get("sharedGroups") or [])
        if not (
            g.get("resourceName") == resource_name
            and str(g.get("owner") or "").strip().lower() == owner_email
        )
    ]
    invited = recipient_data.get("invitedByOwner") or {}
    invited.pop(owner_email, None)
    if invited:
        recipient_data["invitedByOwner"] = invited
    else:
        recipient_data.pop("invitedByOwner", None)
    db.save_app_data(recipient_email, recipient_data)

    owner_data = db.get_app_data(owner_email) or {"groups": {}}
    group = dict(owner_data.get("groups", {}).get(resource_name) or {})
    group["shared"] = [
        e
        for e in (group.get("shared") or [])
        if str(e or "").strip().lower() != recipient_email
    ]
    if not group["shared"]:
        _clear_owner_group_sync_fields(group)
    owner_data.setdefault("groups", {})[resource_name] = group
    db.save_app_data(owner_email, compact_app_data(owner_data, owner_data))
    return {"groups": owner_data.get("groups", {})}


def get_connect_url(recipient_email: str) -> dict[str, Any]:
    if recipient_credentials(recipient_email):
        return {"authorized": True}
    return {"authorized": False, "url": "/auth/recipient/login"}


def disconnect_recipient(recipient_email: str) -> dict[str, Any]:
    from ..google_auth import clear_recipient_connection

    clear_recipient_connection(recipient_email)
    return get_connect_url(recipient_email)



def refresh_staged_shared_contacts(
    owner_email: str,
    resource_name: str,
    share_id: str,
    group_name: str = "",
) -> int:
    """Rewrite staged shared_contacts for one share from live owner Google Contacts."""
    owner = db.normalize_email(owner_email)
    resource = str(resource_name or "").strip()
    share_key = str(share_id or "").strip()
    if not owner or not resource or not share_key:
        return 0
    creds = owner_credentials(owner)
    if not creds:
        return 0
    member_ids = get_group_member_ids(creds, resource)
    members_by_id = get_people_batch(creds, member_ids)
    members_data = [members_by_id[m] for m in member_ids if m in members_by_id]
    if not members_data:
        return 0
    label = str(group_name or "").strip() or resource
    db.delete_shared_contacts(share_key)
    db.write_shared_contacts(share_key, owner, label, resource, members_data)
    return len(members_data)


def maybe_refresh_staged_shared_contacts_before_import(
    *,
    recipient_email: str,
    owner_email: str,
    resource_name: str,
) -> bool:
    """Refresh staged snapshot before first import so pre-connect owner edits are included."""
    recipient = db.normalize_email(recipient_email)
    owner = db.normalize_email(owner_email)
    resource = str(resource_name or "").strip()
    if not recipient or not owner or not resource:
        return False
    recipient_data = db.get_app_data(recipient) or {}
    target = next(
        (
            g
            for g in (recipient_data.get("sharedGroups") or [])
            if db.normalize_email(str(g.get("owner") or "")) == owner
            and str(g.get("resourceName") or "") == resource
        ),
        None,
    )
    if not target:
        return False
    share_id = str(target.get("shareId") or "").strip()
    if not share_id:
        return False
    cursor = int(target.get("importCursor") or 0)
    mapped = db.count_sync_mappings_for_share(recipient, owner, share_id)
    if cursor > 0 or mapped > 0:
        return False
    status = str(target.get("status") or "")
    if status.startswith("Shared"):
        return False
    refreshed = refresh_staged_shared_contacts(
        owner,
        resource,
        share_id,
        group_name=str(target.get("name") or ""),
    )
    if refreshed > 0:
        target["memberCount"] = refreshed
        recipient_data["sharedGroups"] = list(recipient_data.get("sharedGroups") or [])
        db.save_app_data(recipient, recipient_data)
        return True
    return False


def patch_staged_shared_contacts_for_owner_person(
    owner_email: str,
    owner_person_id: str,
    member_data: dict[str, Any],
) -> int:
    """Keep staged invite snapshots current when recipients are not connected yet."""
    owner = db.normalize_email(owner_email)
    person_id = str(owner_person_id or "").strip()
    if not owner or not person_id or not isinstance(member_data, dict):
        return 0
    patched = 0
    for staged in db.list_staged_share_groups_for_owner(owner):
        share_id = str(staged.get("share_id") or "").strip()
        if not share_id:
            continue
        if db.patch_shared_contact_member(share_id, person_id, member_data):
            patched += 1
    return patched


def process_shared_import_for_group(
    recipient_email: str, owner_key: str, resource_name: str, max_ops: int = 200
) -> dict[str, Any]:
    from .recipient_import import process_shared_import_for_group as _run

    maybe_refresh_staged_shared_contacts_before_import(
        recipient_email=recipient_email,
        owner_email=owner_key,
        resource_name=resource_name,
    )
    return _run(recipient_email, owner_key, resource_name, max_ops)


def get_recipient_shared_groups(recipient_email: str) -> dict[str, Any]:
    from .push_worker import (
        pump_push_queue,
        reconcile_stale_share_rows,
        resume_stalled_imports_for_recipient,
    )
    from .recipient_import import get_recipient_shared_groups as _get

    try:
        db.reclaim_stale_push_jobs()
        reconcile_stale_share_rows(recipient_email)
        resume_stalled_imports_for_recipient(recipient_email)
        if db.count_queued_push_jobs() > 0:
            # Long enough to finish a photo batch, short enough to stay far
            # under Cloud Run's 300s request limit.
            pump_push_queue(max_seconds=25)
    except Exception:
        logger.exception("Failed to pump share push queue during progress poll")
    return _get(recipient_email)


def owner_contact_list_is_complete(
    members_data: list[dict[str, Any]], member_count: int
) -> bool:
    """True when every member of the owner's group was actually read.

    Callers delete recipient contacts that are missing from this list, so a
    short read must never be mistaken for contacts that stopped being shared.
    """
    if member_count <= 0:
        return False
    return len(members_data) >= member_count


def sync_shared_group(
    owner_email: str, resource_name: str, max_ops: int = 90, reset: bool = False
) -> dict[str, Any]:
    """Prepare or refresh shared contact payloads for each recipient."""
    creds = owner_credentials(owner_email)
    if not creds:
        raise PermissionError("Not signed in.")

    app_data = db.get_app_data(owner_email) or {}
    groups = dict(app_data.get("groups") or {})
    group = groups.get(resource_name)
    if not group:
        return {
            "groups": groups,
            "syncStatus": {"system": "Group not found"},
            "progressByGroup": {},
            "pending": False,
            "busy": False,
        }

    shared_emails = list(group.get("shared") or [])
    if not shared_emails:
        return {
            "groups": groups,
            "syncStatus": {"system": "Share this group before syncing."},
            "progressByGroup": {},
            "pending": False,
            "busy": False,
        }

    member_ids = get_group_member_ids(creds, resource_name)
    member_count = len(member_ids)
    members_data_by_id = get_people_batch(creds, member_ids)
    members_data = [members_data_by_id[m] for m in member_ids if m in members_data_by_id]

    sync_status: dict[str, str] = {}
    prepared_total = len(members_data)

    for recipient_email in shared_emails:
        recipient_data = db.get_app_data(recipient_email) or {}
        shared_groups = list(recipient_data.get("sharedGroups") or [])
        target = next(
            (
                g
                for g in shared_groups
                if g.get("owner") == owner_email and g.get("resourceName") == resource_name
            ),
            None,
        )
        if not target:
            share_id = _canonical_share_id_for_resource(
                group, owner_email, resource_name, [recipient_email]
            )
            invite_name = str(group.get("name") or "").strip()
            if not invite_name:
                try:
                    from ..people_recipient import get_group_details

                    details = get_group_details(creds, resource_name, 1)
                    invite_name = str(
                        details.get("formattedName") or details.get("name") or ""
                    ).strip()
                except Exception:
                    invite_name = ""
            target = _upsert_recipient_shared_group(
                recipient_email,
                owner_email=owner_email,
                resource_name=resource_name,
                name=invite_name or "Shared group",
                member_count=member_count,
                share_id=share_id,
            )
            recipient_data = db.get_app_data(recipient_email) or {}
            shared_groups = list(recipient_data.get("sharedGroups") or [])

        share_id = str(target.get("shareId") or "")
        if share_id and not str(group.get("shareId") or "").strip():
            group["shareId"] = share_id
        member_count_early = int(target.get("memberCount") or 0)
        if _reconcile_owner_sync_status(target, share_id, member_count_early):
            recipient_data["sharedGroups"] = shared_groups
            db.save_app_data(recipient_email, recipient_data)
        group_name = str(target.get("name") or group.get("name") or "Shared Group")
        stored_count = db.count_shared_contacts(share_id) if share_id else 0

        if reset and share_id:
            db.delete_shared_contacts(share_id)
            stored_count = 0
            _clear_recipient_import_state(target)
            target["status"] = f"Waiting for owner sync (0/{member_count})"
            db.delete_sync_mapping_rows(
                share_id, owner_email, resource_name, recipient_email
            )

        if members_data and share_id:
            db.delete_shared_contacts(share_id)
            db.write_shared_contacts(
                share_id, owner_email, group_name, resource_name, members_data
            )
            stored_count = len(members_data)
        elif not share_id:
            stored_count = 0

        # Drop recipient copies of owner contacts that are no longer shared.
        # Only when the owner's list came back whole: pruning against a short
        # read deletes contacts from the recipient's account that are still
        # shared, and Google drops entries from a bulk read often enough that
        # this has already cost real contacts.
        owner_list_complete = owner_contact_list_is_complete(
            members_data, member_count
        )
        if share_id and members_data and not owner_list_complete:
            logger.warning(
                "Skipping orphan prune for %s: read %s of %s owner contacts",
                recipient_email,
                len(members_data),
                member_count,
            )
        if share_id and members_data and owner_list_complete:
            from .contact_sync import prune_orphan_recipient_contacts

            current_owner_ids = {
                str(member.get("resourceName") or "").strip()
                for member in members_data
                if str(member.get("resourceName") or "").strip()
            }
            prune_result = prune_orphan_recipient_contacts(
                share_id=share_id,
                owner=owner_email,
                group_resource_name=resource_name,
                recipient_email=recipient_email,
                current_owner_person_ids=current_owner_ids,
            )
            if int(prune_result.get("deleted") or 0) > 0:
                mapped_now = db.count_sync_mappings_for_share(
                    recipient_email, owner_email, share_id
                )
                target["actualCount"] = mapped_now
                target["importCursor"] = max(int(target.get("importCursor") or 0), mapped_now)
                if str(target.get("status") or "").startswith("Shared"):
                    target["status"] = "Shared"
                    target["pushStatus"] = target["status"]

        target["memberCount"] = member_count
        target["name"] = group_name
        if stored_count <= 0 or prepared_total <= 0:
            target["status"] = "Missing data"
            sync_status[recipient_email] = "Missing contact data — try Share again"
        elif _recipient_skip_owner_push(
            target,
            share_id=share_id,
            owner=owner_email,
            resource_name=resource_name,
            recipient_email=recipient_email,
            reset=reset,
            owner_member_count=member_count,
        ):
            _reconcile_owner_sync_status(target, share_id, member_count)
            status_now = str(target.get("status") or "")
            if stored_count > 0 and status_now.startswith("Waiting for owner sync"):
                target["status"] = f"Ready ({stored_count}/{member_count})"
                target["pushStatus"] = target["status"]
            elif stored_count > 0 and not status_now.startswith(
                ("Importing", "Photos", "Pushing", "Shared", "Ready")
            ):
                target["status"] = f"Ready ({stored_count}/{member_count})"
                target["pushStatus"] = target["status"]
            sync_status[recipient_email] = f"Up to date ({stored_count}/{member_count})"
            if not str(target.get("pushStatus") or ""):
                target["pushStatus"] = str(target.get("status") or "")
        else:
            status_before = str(target.get("status") or "")
            if reset or status_before.startswith("Waiting for owner sync"):
                _clear_recipient_import_state(target)
            # Includes reopening finished "Shared" so newly added owner members import.
            if not status_before.startswith(("Importing", "Photos", "Pushing", "Queued")):
                target["status"] = f"Ready ({stored_count}/{member_count})"
                target["pushStatus"] = target["status"]
            sync_status[recipient_email] = _maybe_enqueue_recipient_push(
                owner_email,
                resource_name,
                recipient_email,
                target,
                stored_count=stored_count,
                member_count=member_count,
            )

        recipient_data["sharedGroups"] = shared_groups
        db.save_app_data(recipient_email, recipient_data)

    group["syncProgress"] = {
        "done": prepared_total,
        "total": member_count,
        "active": False,
    }
    if prepared_total >= member_count and member_count > 0:
        group["status"] = f"Synced ({prepared_total}/{member_count})"
    elif prepared_total > 0:
        group["status"] = f"Synced ({prepared_total}/{member_count})"
    else:
        group["status"] = "Sync failed — no contacts prepared"
    groups[resource_name] = group
    app_data["groups"] = groups
    db.save_app_data(owner_email, compact_app_data(app_data, app_data))

    return {
        "groups": groups,
        "syncStatus": sync_status,
        "progressByGroup": {
            resource_name: {
                "done": prepared_total,
                "total": member_count,
                "active": False,
            }
        },
        "pending": False,
        "busy": False,
    }


def get_owner_group_sync_progress(owner_email: str, resource_name: str) -> dict[str, Any]:
    app_data = db.get_app_data(owner_email) or {}
    groups = app_data.get("groups") or {}
    group = groups.get(resource_name) or {}
    progress = group.get("syncProgress") or {"done": 0, "total": 0, "active": False}
    return {"group": group, "progress": progress}


def get_share_push_status(
    owner_email: str, resource_name: str | None = None
) -> dict[str, Any]:
    def _display_status(
        group_status: str,
        push_status: str,
        job: dict[str, Any] | None,
        *,
        recipient_email: str = "",
    ) -> str:
        group_status = str(group_status or "")
        push_status = str(push_status or "")
        job_status = str((job or {}).get("status") or "")
        if group_status.startswith("Shared"):
            if "skipped" in group_status.lower():
                return group_status
            return "Push complete"
        if group_status.startswith("Photos") or group_status.startswith("Importing photos"):
            return group_status
        if group_status.startswith("Error"):
            return group_status
        if job_status == "done" and group_status:
            if group_status.startswith("Shared"):
                return "Push complete"
            return group_status
        if push_status.startswith("Ready") and not (
            job_status == "queued" or job_status == "running"
        ):
            if recipient_email and recipient_credentials(recipient_email):
                return push_status or "Ready for recipient import"
            return "Waiting for recipient to connect"
        if group_status.startswith(("Importing", "Photos", "Queued")):
            return group_status
        if push_status.startswith("Pushing") and group_status.startswith("Shared"):
            return "Push complete"
        return push_status or group_status or "unknown"

    owner = db.normalize_email(owner_email)
    jobs = db.list_push_jobs(owner, resource_name)
    recipients: dict[str, dict[str, Any]] = {}

    app_data = db.get_app_data(owner_email) or {}
    groups = app_data.get("groups") or {}
    shared_emails: set[str] = set()
    if resource_name:
        group = groups.get(resource_name) or {}
        shared_emails.update(group.get("shared") or [])
    else:
        for group in groups.values():
            shared_emails.update(group.get("shared") or [])

    for recipient_email in shared_emails:
        recipient_data = db.get_app_data(recipient_email) or {}
        shared_groups = list(recipient_data.get("sharedGroups") or [])
        recipient_changed = False
        for shared_group in shared_groups:
            if shared_group.get("owner") != owner:
                continue
            rn = str(shared_group.get("resourceName") or "")
            if resource_name and rn != resource_name:
                continue
            share_id = str(shared_group.get("shareId") or "")
            member_count = int(shared_group.get("memberCount") or 0)
            if _reconcile_owner_sync_status(shared_group, share_id, member_count):
                recipient_changed = True
            key = f"{recipient_email}|{rn}"
            recipients[key] = {
                "recipientEmail": recipient_email,
                "resourceName": rn,
                "groupStatus": shared_group.get("status"),
                "pushStatus": shared_group.get("pushStatus") or shared_group.get("status"),
                "importCursor": shared_group.get("importCursor"),
                "memberCount": shared_group.get("memberCount"),
            }
        if recipient_changed:
            recipient_data["sharedGroups"] = shared_groups
            db.save_app_data(recipient_email, recipient_data)

    for job in jobs:
        key = f"{job.get('recipient_email') or ''}|{job.get('resource_name') or ''}"
        if key not in recipients:
            continue
        existing = recipients[key].get("job")
        if existing and int(existing.get("id") or 0) > int(job.get("id") or 0):
            continue
        recipients[key]["job"] = {
            "id": job.get("id"),
            "status": job.get("status"),
            "jobType": job.get("job_type"),
            "attempts": job.get("attempts"),
            "lastError": job.get("last_error"),
        }

    for key, row in recipients.items():
        row["displayStatus"] = _display_status(
            str(row.get("groupStatus") or ""),
            str(row.get("pushStatus") or ""),
            row.get("job"),
            recipient_email=str(row.get("recipientEmail") or ""),
        )

    return {"jobs": jobs, "recipients": list(recipients.values())}


def sync_contact_changes_rpc(
    owner_email: str, owner_person_ids: list[str]
) -> dict[str, Any]:
    from .push_worker import enqueue_contact_push

    ids = [str(item) for item in owner_person_ids if str(item or "").strip()]
    job_id = enqueue_contact_push(owner_email, ids)
    return {"ok": True, "queued": len(ids), "job_id": job_id}


def _recipient_group_resource(group: dict[str, Any]) -> str:
    return _contact_group_resource(group.get("created") or group.get("googleGroupId"))


def rename_shared_group_rpc(
    owner_email: str,
    resource_name: str,
    old_name: str,
    new_name: str,
) -> dict[str, Any]:
    """Rename one shared group label in Share records and recipient Google accounts."""
    from ..people_recipient import (
        group_names_equivalent,
        update_contact_group_name,
    )

    owner = db.normalize_email(owner_email)
    next_name = str(new_name or "").strip()
    previous_name = str(old_name or "").strip()
    requested_resource = str(resource_name or "").strip()
    summary: dict[str, Any] = {
        "ok": True,
        "owner": owner,
        "resource_name": requested_resource,
        "old_name": previous_name,
        "new_name": next_name,
        "recipients_updated": 0,
        "google_renamed": 0,
        "skipped": 0,
        "errors": [],
    }
    if not owner or not next_name:
        summary["ok"] = False
        summary["errors"].append("missing_owner_or_name")
        return summary

    owner_data = db.get_app_data(owner) or {}
    owner_data["sharedContactsGroupName"] = next_name
    if requested_resource:
        owner_data["sharedContactsGroupResourceName"] = requested_resource
    db.save_app_data(owner, owner_data)
    if previous_name and previous_name.casefold() == next_name.casefold():
        summary["display_name_saved"] = True
        return summary

    owner_groups = owner_data.get("groups") or {}
    matched_resources: list[str] = []
    if requested_resource:
        matched_resources.append(requested_resource)
    else:
        for resource, group in owner_groups.items():
            if group_names_equivalent(str(group.get("name") or ""), previous_name):
                matched_resources.append(str(resource))

    recipient_emails = set(db.list_app_data_emails())
    recipient_emails.update(db.list_recipient_emails())
    for group in owner_groups.values():
        for email in group.get("shared") or []:
            normalized = db.normalize_email(str(email or ""))
            if normalized:
                recipient_emails.add(normalized)
    recipient_emails.discard(owner)

    if not matched_resources and previous_name:
        for email in recipient_emails:
            recipient_data = db.get_app_data(email) or {}
            for group in recipient_data.get("sharedGroups") or []:
                if db.normalize_email(str(group.get("owner") or "")) != owner:
                    continue
                if group_names_equivalent(str(group.get("name") or ""), previous_name):
                    resource = str(group.get("resourceName") or "").strip()
                    if resource and resource not in matched_resources:
                        matched_resources.append(resource)

    if not matched_resources:
        summary["ok"] = False
        summary["errors"].append("shared_group_not_found")
        return summary

    summary["resource_name"] = matched_resources[0]
    for resource in matched_resources:
        group = owner_groups.get(resource)
        if isinstance(group, dict):
            group["name"] = next_name
            for email in group.get("shared") or []:
                normalized = db.normalize_email(str(email or ""))
                if normalized:
                    recipient_emails.add(normalized)
        db.update_shared_contacts_group_name(owner, resource, next_name)

    if owner_groups:
        owner_data["groups"] = owner_groups
        db.save_app_data(owner, owner_data)

    for email in sorted(recipient_emails):
        recipient_data = db.get_app_data(email) or {}
        shared_groups = list(recipient_data.get("sharedGroups") or [])
        changed = False
        for group in shared_groups:
            if db.normalize_email(str(group.get("owner") or "")) != owner:
                continue
            group_resource = str(group.get("resourceName") or "").strip()
            matches_resource = bool(group_resource and group_resource in matched_resources)
            matches_name = group_names_equivalent(str(group.get("name") or ""), previous_name)
            if not matches_resource and not matches_name:
                continue
            created = _recipient_group_resource(group)
            if created:
                creds = recipient_credentials(email)
                if not creds:
                    summary["skipped"] += 1
                    summary["errors"].append(f"{email}: not_connected")
                    continue
                try:
                    update_contact_group_name(creds, created, next_name)
                    summary["google_renamed"] += 1
                except Exception as exc:
                    summary["ok"] = False
                    summary["errors"].append(f"{email}: {exc}")
                    continue
            group["name"] = next_name
            changed = True
            summary["recipients_updated"] += 1
        if changed:
            recipient_data["sharedGroups"] = shared_groups
            db.save_app_data(email, recipient_data, flush=True)

    if summary["errors"] and summary["recipients_updated"] <= 0:
        summary["ok"] = False
    return summary


def retry_share_push(
    caller_email: str, owner_key: str, resource_name: str
) -> dict[str, Any]:
    from .push_worker import (
        _contacts_mapping_is_complete,
        _has_open_import_queues,
        _latest_recipient_push_job,
        enqueue_full_group_push,
        reconcile_stale_share_rows,
    )
    from .recipient_import import get_recipient_shared_groups

    recipient_email = db.normalize_email(caller_email)
    creds = recipient_credentials(recipient_email)
    if not creds:
        raise PermissionError("Connect your account first, then retry push.")

    db.reclaim_stale_push_jobs()
    reconcile_stale_share_rows(recipient_email)
    recipient_data = db.get_app_data(recipient_email) or {}
    shared_groups = list(recipient_data.get("sharedGroups") or [])
    skip_enqueue = False
    target_found = False
    for group in shared_groups:
        if (
            group.get("owner") == owner_key
            and group.get("resourceName") == resource_name
        ):
            target_found = True
            status = str(group.get("status") or "")
            import_phase = str(group.get("importPhase") or "")
            from .recipient_import import apply_missing_photo_backfill

            latest = _latest_recipient_push_job(
                owner_key, resource_name, recipient_email
            )
            last_job_done = str((latest or {}).get("status") or "") == "done"
            force = status.startswith(("Shared", "Queued"))
            # A finished import with a stale Photos (n/m) counter must not
            # reopen contact create. Scan Google for missing photos only.
            backfill_creds = creds if last_job_done else None
            if apply_missing_photo_backfill(
                group,
                force=force,
                creds=backfill_creds,
                live_only=last_job_done,
            ):
                break
            if (
                last_job_done
                and _contacts_mapping_is_complete(group, recipient_email)
                and not _has_open_import_queues(group)
            ):
                group["status"] = "Shared"
                group["pushStatus"] = "Shared"
                group["importPhase"] = "done"
                skip_enqueue = True
                break
            # Do not rewind an in-progress photo import back to contacts.
            if import_phase == "photos" or status.startswith(
                ("Photos (", "Importing photos")
            ):
                group["pushStatus"] = group.get("status") or "Queued"
                break
            if status.startswith("Shared"):
                group["status"] = "Queued"
                group["pushStatus"] = "Queued"
                break
            member_count = int(group.get("memberCount") or 0)
            cursor = int(group.get("importCursor") or 0)
            if member_count > 0:
                group["status"] = f"Pushing ({cursor}/{member_count})"
            else:
                group["status"] = "Pushing"
            group["pushStatus"] = group["status"]
            break
    recipient_data["sharedGroups"] = shared_groups
    db.save_app_data(recipient_email, recipient_data)

    if not target_found:
        return {
            "jobId": 0,
            "status": "owner_group_gone",
            **get_recipient_shared_groups(recipient_email),
        }

    if skip_enqueue:
        return {
            "jobId": 0,
            "status": "already_complete",
            **get_recipient_shared_groups(recipient_email),
        }

    already_running = db.has_active_push_job(
        owner_key, resource_name, recipient_email
    )
    job_id = enqueue_full_group_push(
        owner_key, resource_name, recipient_email, pump_seconds=0
    )
    return {
        "jobId": job_id,
        "status": "already_running" if already_running else "queued",
        **get_recipient_shared_groups(recipient_email),
    }


def reset_group_sync_state(owner_email: str, resource_name: str) -> dict[str, Any]:
    creds = owner_credentials(owner_email)
    if not creds:
        raise PermissionError("Not signed in.")

    app_data = db.get_app_data(owner_email) or {}
    groups = dict(app_data.get("groups") or {})
    group = groups.get(resource_name)
    if not group:
        raise ValueError("Group not found.")

    shared_emails = list(group.get("shared") or [])
    _clear_owner_group_sync_fields(group)
    groups[resource_name] = group
    app_data["groups"] = groups
    db.save_app_data(owner_email, compact_app_data(app_data, app_data))

    for recipient_email in shared_emails:
        recipient_data = db.get_app_data(recipient_email) or {}
        shared_groups = list(recipient_data.get("sharedGroups") or [])
        changed = False
        for target in shared_groups:
            if (
                target.get("owner") == owner_email
                and target.get("resourceName") == resource_name
            ):
                share_id = str(target.get("shareId") or "")
                member_count = int(target.get("memberCount") or 0)
                stored_count = db.count_shared_contacts(share_id) if share_id else 0
                if share_id:
                    db.delete_sync_mapping_rows(
                        share_id, owner_email, resource_name, recipient_email
                    )
                _clear_recipient_import_state(target)
                if stored_count > 0 and member_count > 0:
                    target["status"] = f"Ready ({stored_count}/{member_count})"
                elif member_count > 0:
                    target["status"] = f"Waiting for owner sync (0/{member_count})"
                changed = True
        if changed:
            recipient_data["sharedGroups"] = shared_groups
            db.save_app_data(recipient_email, recipient_data)

    app_result = load_app(owner_email, "owner")
    return {
        "groups": app_result["groups"],
        "sharedGroups": app_result["sharedGroups"],
    }


def list_skipped_contacts_for_owner_group(
    owner_email: str, resource_name: str
) -> dict[str, Any]:
    return {"contacts": []}
