#!/usr/bin/env python3
"""Relink a recipient to one shared group without re-importing existing Google contacts.

Rebuilds sync_mappings by matching staged owner contacts to people already in the
recipient's '(shared)' Google contact group (hash + email fallback).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _bootstrap_env(database: str) -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT.parent.parent / ".env")
    os.environ["DATABASE_PATH"] = database
    os.environ.pop("SHARE_DB_GCS_URI", None)


def _load_app():
    from app import db
    from app.google_auth import recipient_credentials
    from app.people_api import get_people_batch
    from app.people_recipient import list_shared_contact_groups
    from app.services.app_data import compact_app_data
    from app.services.contact_hash import compute_contact_hash, compute_photo_hash

    return db, recipient_credentials, get_people_batch, list_shared_contact_groups, compact_app_data, compute_contact_hash, compute_photo_hash
def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _primary_email(person: dict) -> str:
    for item in person.get("emailAddresses") or []:
        value = str(item.get("value") or "").strip().lower()
        if value:
            return value
    return ""


def _find_shared_group(creds, group_name_hint: str, list_shared_contact_groups) -> dict | None:
    hint = group_name_hint.strip().lower()
    shared_name = group_name_hint
    if "(shared)" not in hint:
        from app.people_recipient import build_shared_group_name

        shared_name = build_shared_group_name(group_name_hint)

    groups = list_shared_contact_groups(creds)
    for group in groups:
        base = str(group.get("baseName") or "").strip().lower()
        display = str(group.get("displayName") or "").strip().lower()
        if hint in base or hint in display or base in hint or display in shared_name.lower():
            return group
    for group in groups:
        if "tristate" in str(group.get("baseName") or "").lower():
            return group
    return groups[0] if len(groups) == 1 else None


def relink_recipient_share(
    *,
    owner_email: str,
    recipient_email: str,
    group_resource_name: str,
    share_id: str,
    group_name: str,
    dry_run: bool = False,
    database: str = "/tmp/sharecontacts.db",
) -> dict:
    _bootstrap_env(database)
    (
        db,
        recipient_credentials,
        get_people_batch,
        list_shared_contact_groups,
        compact_app_data,
        compute_contact_hash,
        compute_photo_hash,
    ) = _load_app()
    db._active_database_path = Path(database)  # noqa: SLF001

    owner_email = db.normalize_email(owner_email)
    recipient_email = db.normalize_email(recipient_email)
    group_resource_name = str(group_resource_name or "").strip()
    share_id = str(share_id or "").strip()
    group_name = str(group_name or "Shared group").strip()

    creds = recipient_credentials(recipient_email)
    if not creds:
        raise RuntimeError(
            f"{recipient_email} has no usable Google OAuth token. "
            "Recipient must Connect account in the share app first."
        )

    google_group = _find_shared_group(creds, group_name, list_shared_contact_groups)
    if not google_group:
        raise RuntimeError(
            f"No '(shared)' contact group found in {recipient_email}'s Google Contacts. "
            f"Expected something like '{group_name}'."
        )

    google_group_id = str(google_group.get("googleGroupId") or "").strip()
    from app.people_api import get_group_member_ids

    member_ids = get_group_member_ids(creds, google_group_id, max_members=2500)
    recipient_people = get_people_batch(creds, member_ids)

    by_hash: dict[str, list[str]] = {}
    by_email: dict[str, str] = {}
    for rid, person in recipient_people.items():
        contact_hash = compute_contact_hash(person)
        by_hash.setdefault(contact_hash, []).append(rid)
        email = _primary_email(person)
        if email and email not in by_email:
            by_email[email] = rid

    staged = db.read_shared_contacts(share_id)
    if not staged:
        raise RuntimeError(f"No staged contacts for share_id {share_id}")

    used_recipient_ids: set[str] = set()
    mapped = 0
    unmatched: list[str] = []

    for member in staged:
        owner_person_id = str(member.get("resourceName") or "").strip()
        if not owner_person_id.startswith("people/"):
            continue
        contact_hash = compute_contact_hash(member)
        photo_hash = compute_photo_hash(member)
        owner_etag = str(member.get("etag") or "")

        recipient_person_id = ""
        for candidate in by_hash.get(contact_hash, []):
            if candidate not in used_recipient_ids:
                recipient_person_id = candidate
                break
        if not recipient_person_id:
            email = _primary_email(member)
            if email:
                candidate = by_email.get(email, "")
                if candidate and candidate not in used_recipient_ids:
                    recipient_person_id = candidate

        if not recipient_person_id:
            names = member.get("names") or [{}]
            label = names[0].get("displayName") or owner_person_id
            unmatched.append(str(label))
            continue

        used_recipient_ids.add(recipient_person_id)
        mapped += 1
        if not dry_run:
            db.upsert_sync_mapping(
                share_id=share_id,
                owner=owner_email,
                group_resource_name=group_resource_name,
                recipient_email=recipient_email,
                owner_person_id=owner_person_id,
                recipient_person_id=recipient_person_id,
                last_hash=contact_hash,
                last_photo_hash=photo_hash,
                owner_etag=owner_etag,
            )

    member_count = len(staged)
    if mapped >= member_count:
        status = "Shared"
    else:
        status = f"Shared ({len(unmatched)} unmatched)"

    recipient_group = {
        "owner": owner_email,
        "name": group_name.replace(" (Shared)", "").strip() or group_name,
        "resourceName": group_resource_name,
        "members": [],
        "memberCount": member_count,
        "shareId": share_id,
        "importCursor": member_count,
        "actualCount": len(member_ids),
        "status": status,
        "pushStatus": status,
        "importPhase": "done",
        "created": google_group_id,
        "photoDoneCount": 0,
        "photoTargetCount": 0,
    }

    if not dry_run:
        owner_data = db.get_app_data(owner_email) or {"groups": {}}
        groups = dict(owner_data.get("groups") or {})
        group_entry = dict(groups.get(group_resource_name) or {"shared": []})
        shared = {db.normalize_email(e) for e in (group_entry.get("shared") or [])}
        shared.add(recipient_email)
        group_entry["shared"] = sorted(shared)
        groups[group_resource_name] = group_entry
        owner_data["groups"] = groups
        db.save_app_data(owner_email, compact_app_data(owner_data, owner_data), flush=True)

        recipient_data = {
            "sharedGroups": [recipient_group],
            "invitedByOwner": {owner_email: _iso_now()},
        }
        db.save_app_data(recipient_email, recipient_data, flush=True)
        db.cancel_push_jobs(owner_email, group_resource_name, recipient_email)

    return {
        "recipient": recipient_email,
        "owner": owner_email,
        "google_group_id": google_group_id,
        "google_member_count": len(member_ids),
        "staged_count": member_count,
        "mapped": mapped,
        "unmatched_count": len(unmatched),
        "unmatched_sample": unmatched[:10],
        "status": status,
        "dry_run": dry_run,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", default="al.ms.lacontacts@gmail.com")
    parser.add_argument("--recipient", default="charliekerr.inbox@gmail.com")
    parser.add_argument(
        "--group-resource-name",
        default="contactGroups/39c12a71884ce63a",
    )
    parser.add_argument(
        "--share-id",
        default="b0b64b16-b3a6-4c43-97f8-f05548e56215",
    )
    parser.add_argument("--group-name", default="TriState Separates (Shared)")
    parser.add_argument(
        "--database",
        default=os.getenv("DATABASE_PATH", "/tmp/sharecontacts.db"),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = relink_recipient_share(
        owner_email=args.owner,
        recipient_email=args.recipient,
        group_resource_name=args.group_resource_name,
        share_id=args.share_id,
        group_name=args.group_name,
        dry_run=args.dry_run,
        database=args.database,
    )
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
