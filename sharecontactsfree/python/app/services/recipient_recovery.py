"""Recover recipient share metadata from Google Contacts when server DB was lost."""

from __future__ import annotations

import uuid
from typing import Any

from .. import db
from ..google_auth import recipient_credentials
from ..people_recipient import group_member_count, list_shared_contact_groups


def _owner_hint(recipient_data: dict[str, Any], recipient_email: str) -> str:
    invited = recipient_data.get("invitedByOwner") or {}
    owners = sorted(
        {str(key).strip().lower() for key in invited.keys() if str(key).strip()}
    )
    if len(owners) == 1:
        return owners[0]

    for group in recipient_data.get("sharedGroups") or []:
        owner = str(group.get("owner") or "").strip().lower()
        if owner and owner != "unknown":
            owners.append(owner)
    owners = sorted(set(owners))
    if len(owners) == 1:
        return owners[0]

    mapping_owners = db.list_distinct_owners_for_recipient(recipient_email)
    if len(mapping_owners) == 1:
        return mapping_owners[0]
    if len(owners) == 1:
        return owners[0]
    if len(mapping_owners) == 1:
        return mapping_owners[0]
    return owners[0] if owners else ""


def recover_recipient_shared_groups(recipient_email: str) -> tuple[list[dict[str, Any]], bool]:
    """Rebuild sharedGroups rows from imported Google contact groups named '* (shared)'.

    Returns (shared_groups, changed). Does not delete or modify Google contacts.
    """
    recipient_email = db.normalize_email(recipient_email)
    creds = recipient_credentials(recipient_email)
    if not creds:
        return [], False

    google_groups = list_shared_contact_groups(creds)
    if not google_groups:
        return [], False

    recipient_data = db.get_app_data(recipient_email) or {}
    shared_groups: list[dict[str, Any]] = list(recipient_data.get("sharedGroups") or [])
    existing_created = {
        str(group.get("created") or "").strip()
        for group in shared_groups
        if str(group.get("created") or "").strip()
    }
    owner_hint = _owner_hint(recipient_data, recipient_email)
    changed = False

    for google_group in google_groups:
        group_id = str(google_group.get("googleGroupId") or "").strip()
        if not group_id or group_id in existing_created:
            continue
        dismissed = {
            str(key).strip()
            for key in (recipient_data.get("dismissedSharedGroups") or [])
            if str(key).strip()
        }
        if f"created:{group_id}" in dismissed:
            continue

        member_count = int(google_group.get("memberCount") or 0)
        base_name = str(google_group.get("baseName") or "Shared group").strip() or "Shared group"
        shared_groups.append(
            {
                "owner": owner_hint or "unknown",
                "name": base_name,
                "resourceName": f"recovered/{uuid.uuid4()}",
                "members": [],
                "memberCount": member_count,
                "shareId": str(uuid.uuid4()),
                "importCursor": member_count,
                "actualCount": member_count,
                "status": "Shared (recovered)",
                "created": group_id,
                "recovered": True,
            }
        )
        existing_created.add(group_id)
        changed = True

    if changed:
        recipient_data["sharedGroups"] = shared_groups
        db.save_app_data(recipient_email, recipient_data)

    return shared_groups, changed


def maybe_recover_recipient_shared_groups(
    recipient_email: str,
    shared_groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if shared_groups:
        return shared_groups
    if repair_recipient_app_data_from_staged(recipient_email):
        restored = db.get_app_data(recipient_email) or {}
        staged_groups = list(restored.get("sharedGroups") or [])
        if staged_groups:
            return staged_groups
    recovered, changed = recover_recipient_shared_groups(recipient_email)
    return recovered if changed else shared_groups


def repair_recipient_app_data_from_staged(recipient_email: str) -> bool:
    """Rebuild empty recipient sharedGroups from staged shared_contacts + sync_mappings."""
    recipient_email = db.normalize_email(recipient_email)
    existing = db.get_app_data(recipient_email)
    if existing and existing.get("sharedGroups"):
        return False

    owners = db.list_distinct_owners_for_recipient(recipient_email)
    if not owners:
        return False

    from .app_data import _dismissal_identity_keys

    dismissed = {
        str(key).strip()
        for key in ((existing or {}).get("dismissedSharedGroups") or [])
        if str(key).strip()
    }
    shared_groups: list[dict[str, Any]] = []
    seen_share_ids: set[str] = set()
    for owner in owners:
        owner_data = db.get_app_data(owner) or {}
        owner_groups = owner_data.get("groups") or {}
        for staged in db.list_staged_share_groups_for_owner(owner):
            share_id = str(staged.get("share_id") or "").strip()
            if not share_id or share_id in seen_share_ids:
                continue
            resource = str(staged.get("group_resource_name") or "").strip()
            if dismissed.intersection(
                _dismissal_identity_keys(
                    {
                        "owner": owner,
                        "resourceName": resource,
                        "shareId": share_id,
                    }
                )
            ):
                continue
            mapped = db.count_sync_mappings_for_share(recipient_email, owner, share_id)
            owner_group = owner_groups.get(resource) or {}
            shared_with = {
                db.normalize_email(e) for e in (owner_group.get("shared") or [])
            }
            # Only rebuild rows this recipient actually owns: either they have mappings
            # for this staged snapshot, or the owner currently shares the group with them.
            # This prevents pulling in other recipients' staged snapshots as phantom rows.
            if mapped <= 0 and recipient_email not in shared_with:
                continue
            seen_share_ids.add(share_id)
            member_count = int(staged.get("member_count") or 0)
            base_name = str(staged.get("group_name") or "Shared group").replace(" (Shared)", "").strip()
            if mapped >= member_count and member_count > 0:
                status = "Shared"
            elif mapped > 0:
                status = f"Pushing ({mapped}/{member_count})"
            else:
                status = f"Ready ({member_count}/{member_count})" if member_count else "Ready"
            shared_groups.append(
                {
                    "owner": owner,
                    "name": base_name or "Shared group",
                    "resourceName": str(staged.get("group_resource_name") or ""),
                    "shareId": share_id,
                    "memberCount": member_count,
                    "importCursor": mapped,
                    "actualCount": mapped,
                    "status": status,
                    "pushStatus": status,
                    "recoveredFromStaged": True,
                }
            )

    if not shared_groups:
        return False

    app_data = dict(existing or {})
    app_data["sharedGroups"] = shared_groups
    db.save_app_data(recipient_email, app_data, flush=True)
    return True
