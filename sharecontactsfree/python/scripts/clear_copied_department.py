#!/usr/bin/env python3
"""Clear leftover Field-copied Department values on recipient Google cards.

Card repair hashed contacts after dropping a copied Department, so Google never
got an empty Department value and the duplicate Job Title line stayed.

Dry run by default. Pass --apply to write.

Usage:
    python3 scripts/clear_copied_department.py <local_db> <recipient_email> [--apply]
"""

from __future__ import annotations

import os
import sys
import time
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
from app.people_api import get_people_batch  # noqa: E402
from app.people_recipient import (  # noqa: E402
    normalize_person_resource_name,
    update_contact,
)


def _display_name(person: dict) -> str:
    for item in person.get("names") or []:
        name = str(item.get("displayName") or "").strip()
        if name:
            return name
    return "(unnamed)"


def has_copied_department(person: dict) -> bool:
    for item in person.get("organizations") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        department = str(item.get("department") or "").strip()
        if department and (not title or title.casefold() == department.casefold()):
            return True
    return False


def main() -> None:
    db._active_database_path = db_path
    db.init_db()

    creds = recipient_credentials(recipient_email)
    if not creds:
        raise SystemExit(f"{recipient_email} is not connected")

    ids: list[str] = []
    seen: set[str] = set()
    for mapping in db.list_sync_mappings_for_recipient(recipient_email):
        rid = normalize_person_resource_name(str(mapping.get("recipient_person_id") or ""))
        if not rid or rid in seen:
            continue
        seen.add(rid)
        ids.append(rid)

    people = get_people_batch(creds, ids)
    dirty = [rid for rid in ids if has_copied_department(people.get(rid) or {})]
    missing = [rid for rid in ids if rid not in people]
    print(
        f"{recipient_email}: {len(ids)} cards, {len(dirty)} with copied Department, "
        f"{len(missing)} missing"
    )
    for rid in dirty[:8]:
        person = people.get(rid) or {}
        org = (person.get("organizations") or [{}])[0]
        print(
            f"  {_display_name(person)} title={org.get('title')!r} "
            f"dept={org.get('department')!r} name={org.get('name')!r}"
        )
    if len(dirty) > 8:
        print(f"  ... {len(dirty) - 8} more")

    if not apply_changes:
        print("DRY RUN — nothing updated. Re-run with --apply to clear Department.")
        return

    updated = 0
    failed = 0
    for index, rid in enumerate(dirty, start=1):
        person = people.get(rid) or {}
        etag = str(person.get("etag") or "")
        if not etag or not update_contact(creds, rid, person, etag):
            failed += 1
            print(f"  FAILED {_display_name(person)} {rid}")
        else:
            updated += 1
        if index % 25 == 0:
            print(f"  ... {index}/{len(dirty)}")
            time.sleep(1.0)
    print(f"updated {updated}, failed {failed}")


if __name__ == "__main__":
    main()
