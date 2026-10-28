#!/usr/bin/env python3
"""Collapse extra recipient Google cards onto the AlMsLa copy.

emmynelson1970-style duplicates: the same owner person was imported once into
AlMsLa and again into Field+Meeting groups, creating two Google contacts.

Keeps the AlMsLa recipient_person_id, adds the extra group labels to that
card, remaps sync_mappings, then deletes the extra Google contacts.

Dry run by default. Pass --apply to write.

Usage:
    python3 scripts/collapse_recipient_duplicates.py <local_db> <recipient_email> [--apply]
"""

from __future__ import annotations

import os
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

db_path = Path(sys.argv[1]).resolve()
recipient_email = sys.argv[2].strip().lower()
apply_changes = "--apply" in sys.argv[3:]

os.environ["SHARE_DB_GCS_URI"] = ""
os.environ["SHARE_DB_LOCAL_PATH"] = str(db_path)
os.environ["DATABASE_PATH"] = str(db_path)

env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

from app import db  # noqa: E402
from app.google_auth import recipient_credentials  # noqa: E402
from app.people_recipient import (  # noqa: E402
    delete_contact,
    ensure_contact_in_group,
    get_people_contacts_batch,
    normalize_person_resource_name,
)

ALMSLA = "contactGroups/25249bb40acfab63"


def _display_name(person: dict) -> str:
    for item in person.get("names") or []:
        name = str(item.get("displayName") or "").strip()
        if name:
            return name
    return "(unnamed)"


def main() -> None:
    db._active_database_path = db_path
    db.init_db()

    creds = recipient_credentials(recipient_email)
    if not creds:
        raise SystemExit(f"{recipient_email} is not connected")

    data = db.get_app_data(recipient_email) or {}
    created_by_owner_group = {}
    for group in data.get("sharedGroups") or []:
        if not isinstance(group, dict):
            continue
        owner_rn = str(group.get("resourceName") or "").strip()
        created = str(group.get("created") or "").strip()
        if owner_rn.startswith("contactGroups/") and created.startswith("contactGroups/"):
            created_by_owner_group[owner_rn] = created

    rows = db.list_sync_mappings_for_recipient(recipient_email)
    by_owner: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        owner_id = normalize_person_resource_name(str(row.get("owner_person_id") or ""))
        recip_id = normalize_person_resource_name(str(row.get("recipient_person_id") or ""))
        if owner_id and recip_id:
            by_owner[owner_id].append(row)

    jobs = []
    for owner_id, mappings in by_owner.items():
        rids = {
            normalize_person_resource_name(str(item.get("recipient_person_id") or ""))
            for item in mappings
        }
        rids.discard("")
        if len(rids) < 2:
            continue
        keep = ""
        for item in mappings:
            if str(item.get("group_resource_name") or "") == ALMSLA:
                keep = normalize_person_resource_name(str(item.get("recipient_person_id") or ""))
                break
        if not keep:
            keep = sorted(rids)[0]
        extras = sorted(rids - {keep})
        extra_groups = sorted(
            {
                str(item.get("group_resource_name") or "")
                for item in mappings
                if normalize_person_resource_name(str(item.get("recipient_person_id") or ""))
                in extras
            }
        )
        jobs.append(
            {
                "owner_id": owner_id,
                "keep": keep,
                "extras": extras,
                "extra_groups": extra_groups,
                "mappings": mappings,
            }
        )

    extra_count = sum(len(job["extras"]) for job in jobs)
    print(f"{recipient_email}: {len(jobs)} people with duplicate cards, {extra_count} extras to remove")
    print(f"unique owner contacts: {len(by_owner)}")
    if not jobs:
        return

    keep_ids = sorted({job["keep"] for job in jobs})
    extra_ids = sorted({rid for job in jobs for rid in job["extras"]})
    keep_people = get_people_contacts_batch(creds, keep_ids, strict=False)
    extra_people = get_people_contacts_batch(creds, extra_ids, strict=False)
    print(f"AlMsLa cards in Google: {len(keep_people)}/{len(keep_ids)}")
    print(f"extra cards still in Google: {len(extra_people)}/{len(extra_ids)}")
    if jobs:
        print("sample keep:", _display_name(keep_people.get(jobs[0]["keep"]) or {}))
        for rid in jobs[0]["extras"][:2]:
            print("sample extra:", _display_name(extra_people.get(rid) or {}))

    if not apply_changes:
        print("DRY RUN — nothing deleted. Re-run with --apply to collapse duplicates.")
        return

    deleted = 0
    remapped = 0
    labeled = 0
    missing_keep = 0
    failed_delete = 0
    skipped_gone = 0
    for index, job in enumerate(jobs, 1):
        keep = job["keep"]
        if keep not in keep_people:
            missing_keep += 1
            print(f"  skip, AlMsLa card missing: {keep}")
            continue
        for owner_rn in job["extra_groups"]:
            created = created_by_owner_group.get(owner_rn)
            if created:
                if ensure_contact_in_group(creds, keep, created):
                    labeled += 1
        for mapping in job["mappings"]:
            current = normalize_person_resource_name(str(mapping.get("recipient_person_id") or ""))
            if current == keep:
                continue
            db.upsert_sync_mapping(
                share_id=str(mapping.get("share_id") or ""),
                owner=str(mapping.get("owner") or ""),
                group_resource_name=str(mapping.get("group_resource_name") or ""),
                recipient_email=recipient_email,
                owner_person_id=str(mapping.get("owner_person_id") or ""),
                recipient_person_id=keep,
                last_hash=str(mapping.get("last_hash") or ""),
                last_photo_hash=str(mapping.get("last_photo_hash") or ""),
                owner_etag=str(mapping.get("owner_etag") or ""),
            )
            remapped += 1
        for rid in job["extras"]:
            if rid not in extra_people:
                skipped_gone += 1
                continue
            try:
                if delete_contact(creds, rid):
                    deleted += 1
                else:
                    failed_delete += 1
            except Exception as exc:
                failed_delete += 1
                print(f"  failed delete {rid}: {exc}")
        if index % 20 == 0:
            print(f"  {index}/{len(jobs)} people  deleted={deleted} remapped={remapped}")
            time.sleep(1)

    remaining = {
        normalize_person_resource_name(str(row.get("recipient_person_id") or ""))
        for row in db.list_sync_mappings_for_recipient(recipient_email)
    }
    remaining.discard("")
    print(
        {
            "people_collapsed": len(jobs) - missing_keep,
            "labels_added": labeled,
            "mappings_remapped": remapped,
            "google_deleted": deleted,
            "delete_failed": failed_delete,
            "extras_already_gone": skipped_gone,
            "missing_keep": missing_keep,
            "unique_recipient_ids_now": len(remaining),
        }
    )


if __name__ == "__main__":
    main()
