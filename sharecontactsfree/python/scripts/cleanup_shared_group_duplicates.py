"""Delete duplicate copies of shared contacts from a recipient's group.

Only removes group members that (a) we have no sync mapping for, and (b) match
a contact we DO track. A member that matches nothing we track is left alone.

Dry run by default. Pass --apply to actually delete.

Usage:
    python3 scripts/cleanup_shared_group_duplicates.py <local_db> <recipient_email> [--apply]
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

os.environ["SHARE_DB_GCS_URI"] = ""  # never write back to cloud storage
os.environ["SHARE_DB_LOCAL_PATH"] = str(db_path)
os.environ["DATABASE_PATH"] = str(db_path)

env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from app import db  # noqa: E402
from app.google_auth import recipient_credentials  # noqa: E402
from app.people_recipient import (  # noqa: E402
    delete_contact,
    get_group_details,
    get_people_contacts_batch,
    normalize_person_resource_name,
)
from app.services.recipient_import import (  # noqa: E402
    _contact_match_keys,
    adopt_untracked_group_members,
)

db._active_database_path = db_path
db.init_db()

creds = recipient_credentials(recipient_email)
if not creds:
    raise SystemExit(f"{recipient_email} is not connected")

data = db.get_app_data(recipient_email) or {}
for group in data.get("sharedGroups") or []:
    group_id = str(group.get("created") or "")
    if not group_id.startswith("contactGroups/"):
        continue
    share_id = str(group.get("shareId") or "")
    owner = str(group.get("owner") or "")
    resource_name = str(group.get("resourceName") or "")

    print(f"\n=== {group.get('name')} ({group_id}) ===")

    # Rescue first: a person whose recorded contact was deleted may survive
    # only as an untracked copy. Claim those before deciding what is a
    # duplicate, or the delete below would remove them for good.
    staged = db.read_shared_contacts(share_id)
    adopted = adopt_untracked_group_members(creds, group, group_id, staged)
    print(f"re-pointed to surviving copies: {adopted}")

    by_owner, _ = db.load_sync_mappings(share_id, owner, resource_name, recipient_email)
    tracked = {
        normalize_person_resource_name(str(m.get("recipient_person_id") or ""))
        for m in by_owner.values()
    }
    tracked.discard("")

    details = get_group_details(creds, group_id, 5000)
    members = [
        rn
        for rn in (
            normalize_person_resource_name(m)
            for m in (details.get("memberResourceNames") or [])
        )
        if rn
    ]
    untracked = [m for m in members if m not in tracked]
    print(f"members: {len(members)}  tracked: {len(tracked)}  untracked: {len(untracked)}")
    if not untracked:
        continue

    # strict=True raises on API failure, so anything simply absent from the
    # response is a contact that no longer exists rather than one we failed to
    # read. That distinction decides whether deleting is safe.
    tracked_people = get_people_contacts_batch(creds, sorted(tracked), strict=True)
    gone = [rid for rid in tracked if rid not in tracked_people]
    if gone:
        print(f"tracked contacts that no longer exist in Google: {len(gone)}")
    tracked_keys: set[str] = set()
    for person in tracked_people.values():
        tracked_keys |= _contact_match_keys(person)

    untracked_people = get_people_contacts_batch(creds, untracked, strict=True)
    duplicates = []
    unknown = []
    for rid in untracked:
        person = untracked_people.get(rid)
        if person and (_contact_match_keys(person) & tracked_keys):
            duplicates.append(rid)
        else:
            unknown.append(rid)

    print(f"duplicates of tracked contacts: {len(duplicates)}")
    print(f"left alone (match nothing we track): {len(unknown)}")

    def _label(rid: str) -> str:
        person = untracked_people.get(rid) or {}
        for n in person.get("names") or []:
            name = str(n.get("displayName") or "").strip()
            if name:
                return name
        for e in person.get("emailAddresses") or []:
            value = str(e.get("value") or "").strip()
            if value:
                return value
        return "(not readable)"

    for rid in unknown[:15]:
        print(f"  left alone: {_label(rid)} ({rid})")
    if len(unknown) > 15:
        print(f"  ...and {len(unknown) - 15} more")

    if not apply_changes:
        print("DRY RUN - nothing deleted. Re-run with --apply to delete duplicates.")
        continue

    deleted = 0
    failed = 0
    for i, rid in enumerate(duplicates, 1):
        try:
            if delete_contact(creds, rid):
                deleted += 1
            else:
                failed += 1
        except Exception as exc:
            failed += 1
            print(f"  failed {rid}: {exc}")
        if i % 50 == 0:
            print(f"  deleted {deleted}/{len(duplicates)}")
            time.sleep(1)
    print(f"deleted {deleted}, failed {failed}")
