"""Incremental owner→recipient contact sync using sync_mappings."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .. import db
from ..google_auth import owner_credentials, recipient_credentials
from ..people_api import get_group_member_ids, get_people_batch, people_service
from ..people_recipient import (
    create_contact_in_group,
    delete_contact,
    delete_contact_photo,
    ensure_assignment_contact_label,
    ensure_contact_in_group,
    get_person_etags_batch,
    normalize_person_resource_name,
    set_contact_photo,
    update_contact,
)
from .contact_hash import compute_contact_hash, compute_photo_hash
from .recipient_import import _ensure_recipient_group

logger = logging.getLogger(__name__)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_person_ids(person_ids: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in person_ids:
        normalized = normalize_person_resource_name(str(raw or "").strip())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def _membership_group_resource_names(member_data: dict[str, Any] | None) -> set[str]:
    names: set[str] = set()
    for membership in (member_data or {}).get("memberships") or []:
        if not isinstance(membership, dict):
            continue
        group_membership = membership.get("contactGroupMembership") or {}
        resource_name = str(group_membership.get("contactGroupResourceName") or "").strip()
        if resource_name:
            names.add(resource_name)
    return names


def _ensure_membership_group_resource_names(
    owner_creds,
    person_id: str,
    member_data: dict[str, Any],
) -> set[str]:
    """Use batch memberships when present; otherwise fetch them for this contact."""
    names = _membership_group_resource_names(member_data)
    if names:
        return names
    normalized = normalize_person_resource_name(person_id)
    if not normalized or owner_creds is None:
        return set()
    try:
        service = people_service(owner_creds)
        person = (
            service.people()
            .get(resourceName=normalized, personFields="memberships")
            .execute()
        )
    except Exception:
        logger.exception("Failed to load memberships for %s", normalized)
        return set()
    if isinstance(person, dict):
        member_data["memberships"] = person.get("memberships") or []
    return _membership_group_resource_names(member_data)


def _discover_mappings_for_unmapped_contact(
    *,
    owner: str,
    owner_person_id: str,
    member_data: dict[str, Any],
    owner_creds,
) -> list[dict[str, Any]]:
    """Build create mappings for shared groups this new contact belongs to."""
    owner_data = db.get_app_data(owner) or {}
    owner_groups = dict(owner_data.get("groups") or {})
    if not owner_groups:
        return []

    membership_names = _ensure_membership_group_resource_names(
        owner_creds, owner_person_id, member_data
    )
    discovered: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for resource_name, group in owner_groups.items():
        if not isinstance(group, dict):
            continue
        shared_emails = [
            db.normalize_email(email)
            for email in (group.get("shared") or [])
            if str(email or "").strip()
        ]
        if not shared_emails:
            continue

        in_group = resource_name in membership_names
        if not in_group and not membership_names:
            # Fallback when People API omits memberships: check group member list.
            try:
                member_ids = {
                    normalize_person_resource_name(item)
                    for item in get_group_member_ids(owner_creds, resource_name)
                }
            except Exception:
                logger.exception("Failed to list members for %s", resource_name)
                continue
            in_group = owner_person_id in member_ids
        if not in_group:
            continue

        share_id = str(group.get("shareId") or "").strip()
        for recipient_email in shared_emails:
            recipient_data = db.get_app_data(recipient_email) or {}
            target = next(
                (
                    g
                    for g in (recipient_data.get("sharedGroups") or [])
                    if isinstance(g, dict)
                    and g.get("owner") == owner
                    and g.get("resourceName") == resource_name
                ),
                None,
            )
            if not target:
                continue
            resolved_share_id = str(target.get("shareId") or share_id).strip()
            if not resolved_share_id:
                continue
            key = (resolved_share_id, resource_name, recipient_email)
            if key in seen:
                continue
            seen.add(key)
            discovered.append(
                {
                    "id": None,
                    "share_id": resolved_share_id,
                    "owner": owner,
                    "group_resource_name": resource_name,
                    "recipient_email": recipient_email,
                    "owner_person_id": owner_person_id,
                    "recipient_person_id": "",
                    "last_hash": "",
                    "last_photo_hash": "",
                    "owner_etag": "",
                }
            )
    return discovered


def prune_orphan_recipient_contacts(
    *,
    share_id: str,
    owner: str,
    group_resource_name: str,
    recipient_email: str,
    current_owner_person_ids: set[str] | list[str],
) -> dict[str, Any]:
    """Delete recipient Google contacts whose owner person is no longer in the shared group.

    When an owner contact is permanently deleted or recreated with a new Google ID, Sync
    used to create a replacement without removing the old recipient copy. This prunes those
    leftovers using sync_mappings that point at owner IDs no longer in the current set.
    """
    current_ids = {
        normalize_person_resource_name(str(item or "").strip())
        for item in current_owner_person_ids
        if str(item or "").strip()
    }
    current_ids.discard("")
    by_owner, _ = db.load_sync_mappings(
        share_id, owner, group_resource_name, recipient_email
    )
    orphans = [
        mapping
        for owner_person_id, mapping in by_owner.items()
        if normalize_person_resource_name(owner_person_id) not in current_ids
        and str(mapping.get("recipient_person_id") or "").strip()
    ]
    if not orphans:
        return {"checked": len(by_owner), "orphans": 0, "deleted": 0, "failed": 0}

    creds = recipient_credentials(recipient_email)
    if not creds:
        return {
            "checked": len(by_owner),
            "orphans": len(orphans),
            "deleted": 0,
            "failed": len(orphans),
            "reason": "not_connected",
        }

    deleted = 0
    failed = 0
    deleted_owner_ids: list[str] = []
    for mapping in orphans:
        owner_person_id = str(mapping.get("owner_person_id") or "").strip()
        rid = normalize_person_resource_name(str(mapping.get("recipient_person_id") or ""))
        try:
            ok = delete_contact(creds, rid) if rid else True
        except Exception:
            logger.exception(
                "Failed deleting orphan recipient contact %s for owner %s",
                rid,
                owner_person_id,
            )
            ok = False
        if ok:
            deleted += 1
            if owner_person_id:
                deleted_owner_ids.append(owner_person_id)
        else:
            failed += 1

    if deleted_owner_ids:
        db.delete_sync_mapping_rows(
            share_id,
            owner,
            group_resource_name,
            recipient_email,
            owner_person_ids=deleted_owner_ids,
        )
    return {
        "checked": len(by_owner),
        "orphans": len(orphans),
        "deleted": deleted,
        "failed": failed,
    }


def _apply_recipient_photo(
    creds,
    recipient_person_id: str,
    member_data: dict[str, Any],
    *,
    photo_hash: str,
    owner_token: str | None,
) -> bool:
    """Push or clear a recipient photo. Returns False when the photo step failed."""
    if photo_hash == "no-photo":
        return bool(delete_contact_photo(creds, recipient_person_id))
    return bool(
        set_contact_photo(
            creds,
            recipient_person_id,
            member_data,
            owner_token=owner_token,
        )
    )


def _propagate_to_siblings(
    *,
    owner: str,
    recipient_email: str,
    owner_person_id: str,
    member_data: dict[str, Any],
    contact_hash: str,
    photo_hash: str,
    owner_etag: str,
    photo_changed: bool,
    exclude_mapping_id: int | None,
    owner_token: str | None = None,
) -> int:
    creds = recipient_credentials(recipient_email)
    if not creds:
        return 0
    siblings = db.find_sibling_mappings(
        owner, recipient_email, owner_person_id, exclude_id=exclude_mapping_id
    )
    if not siblings:
        return 0
    recipient_ids = [
        normalize_person_resource_name(s.get("recipient_person_id") or "")
        for s in siblings
    ]
    recipient_ids = [rid for rid in recipient_ids if rid]
    etags = get_person_etags_batch(creds, recipient_ids)
    updated = 0
    for sibling in siblings:
        rid = normalize_person_resource_name(sibling.get("recipient_person_id") or "")
        etag = etags.get(rid)
        if not rid or not etag:
            continue
        try:
            if update_contact(creds, rid, member_data, etag):
                ensure_assignment_contact_label(creds, rid, member_data)
                stored_photo_hash = str(sibling.get("last_photo_hash") or "")
                if photo_changed:
                    if _apply_recipient_photo(
                        creds,
                        rid,
                        member_data,
                        photo_hash=photo_hash,
                        owner_token=owner_token,
                    ):
                        stored_photo_hash = photo_hash
                    # Keep the previous hash on failure so the next sync retries.
                else:
                    stored_photo_hash = photo_hash
                db.upsert_sync_mapping(
                    share_id=str(sibling.get("share_id") or ""),
                    owner=owner,
                    group_resource_name=str(sibling.get("group_resource_name") or ""),
                    recipient_email=recipient_email,
                    owner_person_id=owner_person_id,
                    recipient_person_id=rid,
                    last_hash=contact_hash,
                    last_photo_hash=stored_photo_hash,
                    owner_etag=owner_etag,
                )
                updated += 1
        except Exception:
            logger.exception("sibling propagate failed for %s", rid)
    return updated


def _apply_mapping_update(
    *,
    owner: str,
    mapping: dict[str, Any],
    member_data: dict[str, Any],
    owner_etag: str,
    owner_token: str | None = None,
) -> dict[str, Any]:
    recipient_email = str(mapping.get("recipient_email") or "")
    creds = recipient_credentials(recipient_email)
    if not creds:
        return {"status": "skipped", "reason": "not_connected", "mapping_id": mapping.get("id")}

    owner_person_id = str(mapping.get("owner_person_id") or "")
    contact_hash = compute_contact_hash(member_data)
    photo_hash = compute_photo_hash(member_data)
    prev_hash = str(mapping.get("last_hash") or "")
    prev_photo = str(mapping.get("last_photo_hash") or "")
    prev_etag = str(mapping.get("owner_etag") or "")
    recipient_person_id = str(mapping.get("recipient_person_id") or "")

    hash_changed = contact_hash != prev_hash
    photo_changed = photo_hash != prev_photo
    etag_changed = owner_etag != prev_etag

    if recipient_person_id:
        if not hash_changed and not photo_changed and not etag_changed:
            return {"status": "skipped", "reason": "unchanged", "mapping_id": mapping.get("id")}

        rid = normalize_person_resource_name(recipient_person_id)
        etags = get_person_etags_batch(creds, [rid])
        etag = etags.get(rid)
        if not etag:
            share_id = str(mapping.get("share_id") or "")
            group_resource_name = str(mapping.get("group_resource_name") or "")
            db.upsert_sync_mapping(
                share_id=share_id,
                owner=owner,
                group_resource_name=group_resource_name,
                recipient_email=recipient_email,
                owner_person_id=owner_person_id,
                recipient_person_id="",
                last_hash=prev_hash,
                last_photo_hash=prev_photo,
                owner_etag=prev_etag,
            )
            recipient_person_id = ""
        else:
            if hash_changed or etag_changed:
                if not update_contact(creds, rid, member_data, etag):
                    return {"status": "error", "reason": "update_failed", "mapping_id": mapping.get("id")}
                ensure_assignment_contact_label(creds, rid, member_data)
            stored_photo_hash = prev_photo
            photo_failed = False
            if photo_changed:
                if _apply_recipient_photo(
                    creds,
                    rid,
                    member_data,
                    photo_hash=photo_hash,
                    owner_token=owner_token,
                ):
                    stored_photo_hash = photo_hash
                else:
                    photo_failed = True
                    logger.warning(
                        "Recipient photo sync failed for %s -> %s; will retry next sync",
                        owner_person_id,
                        rid,
                    )

            share_id = str(mapping.get("share_id") or "")
            group_resource_name = str(mapping.get("group_resource_name") or "")
            db.upsert_sync_mapping(
                share_id=share_id,
                owner=owner,
                group_resource_name=group_resource_name,
                recipient_email=recipient_email,
                owner_person_id=owner_person_id,
                recipient_person_id=rid,
                last_hash=contact_hash,
                last_photo_hash=stored_photo_hash,
                owner_etag=owner_etag,
            )
            siblings = _propagate_to_siblings(
                owner=owner,
                recipient_email=recipient_email,
                owner_person_id=owner_person_id,
                member_data=member_data,
                contact_hash=contact_hash,
                photo_hash=photo_hash,
                owner_etag=owner_etag,
                photo_changed=photo_changed,
                exclude_mapping_id=int(mapping.get("id") or 0) or None,
                owner_token=owner_token,
            )
            result = {
                "status": "updated",
                "mapping_id": mapping.get("id"),
                "siblings": siblings,
            }
            if photo_failed:
                result["photo_failed"] = True
                result["reason"] = "photo_failed"
            return result

    if not recipient_person_id:
        recipient_data = db.get_app_data(recipient_email) or {}
        shared_groups = list(recipient_data.get("sharedGroups") or [])
        target = next(
            (
                g
                for g in shared_groups
                if g.get("owner") == owner
                and g.get("resourceName") == mapping.get("group_resource_name")
                and str(g.get("shareId") or "") == str(mapping.get("share_id") or "")
            ),
            None,
        )
        if not target:
            return {"status": "error", "reason": "group_not_found", "mapping_id": mapping.get("id")}

        group_name = str(target.get("name") or "Shared Group")
        group_id = _ensure_recipient_group(creds, target, group_name)
        if not group_id:
            return {"status": "error", "reason": "group_create_failed", "mapping_id": mapping.get("id")}

        rid = create_contact_in_group(creds, member_data, group_id, skip_photo=True)
        if not rid:
            return {"status": "error", "reason": "create_failed", "mapping_id": mapping.get("id")}

        stored_photo_hash = "no-photo"
        photo_failed = False
        if photo_hash != "no-photo":
            if _apply_recipient_photo(
                creds,
                rid,
                member_data,
                photo_hash=photo_hash,
                owner_token=owner_token,
            ):
                stored_photo_hash = photo_hash
            else:
                # Leave blank so the next incremental sync retries the photo.
                stored_photo_hash = ""
                photo_failed = True
                logger.warning(
                    "Recipient photo create failed for %s -> %s; will retry next sync",
                    owner_person_id,
                    rid,
                )
        else:
            stored_photo_hash = photo_hash

        db.upsert_sync_mapping(
            share_id=str(mapping.get("share_id") or ""),
            owner=owner,
            group_resource_name=str(mapping.get("group_resource_name") or ""),
            recipient_email=recipient_email,
            owner_person_id=owner_person_id,
            recipient_person_id=rid,
            last_hash=contact_hash,
            last_photo_hash=stored_photo_hash,
            owner_etag=owner_etag,
        )
        ensure_contact_in_group(creds, rid, group_id)
        ensure_assignment_contact_label(creds, rid, member_data)
        result = {"status": "created", "mapping_id": mapping.get("id"), "recipient_person_id": rid}
        if photo_failed:
            result["photo_failed"] = True
            result["reason"] = "photo_failed"
        return result

    return {"status": "error", "reason": "unexpected_state", "mapping_id": mapping.get("id")}


def sync_contact_changes(owner_email: str, owner_person_ids: list[str]) -> dict[str, Any]:
    """Push specific owner contacts to all mapped connected recipients."""
    owner = db.normalize_email(owner_email)
    person_ids = _normalize_person_ids(owner_person_ids)
    summary: dict[str, Any] = {
        "requested": len(person_ids),
        "updated": 0,
        "created": 0,
        "skipped": 0,
        "errors": 0,
        "pending": 0,
        "error_details": [],
        "results": [],
    }
    if not person_ids:
        return summary

    owner_creds = owner_credentials(owner)
    if not owner_creds:
        summary["errors"] = len(person_ids)
        summary["error_details"].append("Owner not signed in")
        return summary

    members_by_id = get_people_batch(owner_creds, person_ids)
    for person_id in person_ids:
        member_data = members_by_id.get(person_id)
        if not member_data:
            summary["errors"] += 1
            summary["error_details"].append(f"Missing owner data for {person_id}")
            continue

        owner_etag = str(member_data.get("etag") or "")
        mappings = db.find_mappings_for_owner_person(owner, person_id)
        # Existing mappings (Charlie, Emmy, ...) are not enough. A later
        # recipient of the same group, or a contact added back to AlMsLa,
        # still needs a create mapping.
        discovered = _discover_mappings_for_unmapped_contact(
            owner=owner,
            owner_person_id=person_id,
            member_data=member_data,
            owner_creds=owner_creds,
        )
        if discovered:
            existing = {
                (
                    str(item.get("share_id") or ""),
                    str(item.get("group_resource_name") or ""),
                    db.normalize_email(str(item.get("recipient_email") or "")),
                )
                for item in mappings
            }
            for item in discovered:
                key = (
                    str(item.get("share_id") or ""),
                    str(item.get("group_resource_name") or ""),
                    db.normalize_email(str(item.get("recipient_email") or "")),
                )
                if key not in existing:
                    mappings.append(item)
                    existing.add(key)
        if not mappings:
            from .app_data import patch_staged_shared_contacts_for_owner_person

            staged = patch_staged_shared_contacts_for_owner_person(owner, person_id, member_data)
            summary["skipped"] += 1
            summary["results"].append(
                {"person_id": person_id, "status": "no_mappings", "staged_patched": staged}
            )
            continue

        owner_token = str(owner_creds.token or "") or None
        for mapping in mappings:
            outcome = _apply_mapping_update(
                owner=owner,
                mapping=mapping,
                member_data=member_data,
                owner_etag=owner_etag,
                owner_token=owner_token,
            )
            if outcome.get("status") == "skipped" and outcome.get("reason") == "not_connected":
                from .app_data import patch_staged_shared_contacts_for_owner_person

                staged = patch_staged_shared_contacts_for_owner_person(owner, person_id, member_data)
                outcome = {**outcome, "staged_patched": staged}
            summary["results"].append({"person_id": person_id, **outcome})
            status = outcome.get("status")
            if status == "updated":
                summary["updated"] += 1
            elif status == "created":
                summary["created"] += 1
            elif status == "skipped":
                summary["skipped"] += 1
            elif status == "error":
                summary["errors"] += 1
                summary["error_details"].append(
                    f"{person_id} mapping {mapping.get('id')}: {outcome.get('reason')}"
                )
            if outcome.get("photo_failed"):
                summary["errors"] += 1
                summary["error_details"].append(
                    f"{person_id} mapping {mapping.get('id')}: photo_failed"
                )

    return summary
