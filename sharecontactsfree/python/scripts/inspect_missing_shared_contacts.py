"""Read-only: explain tracked recipient contacts that Google no longer returns.

For each mapping whose recipient contact is missing, report whether the contact
is really gone (404), whether the owner still shares that person, and whether a
copy of that person exists elsewhere in the recipient's group.

Usage:
    python3 scripts/inspect_missing_shared_contacts.py <local_db> <recipient_email>
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

db_path = Path(sys.argv[1]).resolve()
recipient_email = sys.argv[2].strip().lower()

os.environ["SHARE_DB_GCS_URI"] = ""
os.environ["SHARE_DB_LOCAL_PATH"] = str(db_path)
os.environ["DATABASE_PATH"] = str(db_path)

env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from googleapiclient.errors import HttpError  # noqa: E402

from app import db  # noqa: E402
from app.google_auth import recipient_credentials  # noqa: E402
from app.people_api import people_service  # noqa: E402
from app.people_recipient import (  # noqa: E402
    get_group_details,
    get_people_contacts_batch,
    normalize_person_resource_name,
)
from app.services.recipient_import import _contact_match_keys  # noqa: E402

db._active_database_path = db_path
db.init_db()

creds = recipient_credentials(recipient_email)
if not creds:
    raise SystemExit(f"{recipient_email} is not connected")
service = people_service(creds)

data = db.get_app_data(recipient_email) or {}
for group in data.get("sharedGroups") or []:
    group_id = str(group.get("created") or "")
    if not group_id.startswith("contactGroups/"):
        continue
    share_id = str(group.get("shareId") or "")
    owner = str(group.get("owner") or "")
    resource_name = str(group.get("resourceName") or "")

    print(f"\n=== {group.get('name')} ({group_id}) ===")
    by_owner, _ = db.load_sync_mappings(share_id, owner, resource_name, recipient_email)
    tracked = {
        normalize_person_resource_name(str(m.get("recipient_person_id") or "")): oid
        for oid, m in by_owner.items()
        if str(m.get("recipient_person_id") or "").strip()
    }
    tracked.pop("", None)

    found = get_people_contacts_batch(creds, sorted(tracked), strict=True)
    missing = [rid for rid in tracked if rid not in found]
    print(f"tracked: {len(tracked)}   missing from Google: {len(missing)}")
    if not missing:
        continue

    staged = {
        str(row.get("resourceName") or ""): row for row in db.read_shared_contacts(share_id)
    }
    details = get_group_details(creds, group_id, 5000)
    members = [
        rn
        for rn in (
            normalize_person_resource_name(m)
            for m in (details.get("memberResourceNames") or [])
        )
        if rn
    ]
    member_people = get_people_contacts_batch(creds, members, strict=True)
    member_keys: dict[str, str] = {}
    for rid, person in member_people.items():
        for key in _contact_match_keys(person):
            member_keys.setdefault(key, rid)

    statuses: dict[str, int] = {}
    still_shared = 0
    has_copy_in_group = 0
    for rid in missing:
        owner_id = tracked[rid]
        try:
            service.people().get(
                resourceName=rid, personFields="metadata"
            ).execute()
            status = 200
        except HttpError as exc:
            status = int(getattr(exc.resp, "status", 0) or 0)
        statuses[str(status)] = statuses.get(str(status), 0) + 1

        owner_member = staged.get(owner_id)
        if owner_member:
            still_shared += 1
            if _contact_match_keys(owner_member) & set(member_keys):
                has_copy_in_group += 1

    print(f"direct lookup status counts: {statuses}")
    print(f"owner still shares that person: {still_shared} of {len(missing)}")
    print(f"a copy already exists in the group: {has_copy_in_group}")
    print(f"truly absent and need re-import: {still_shared - has_copy_in_group}")
