"""One-shot: prune orphan recipient contacts for TriState Separates and upload DB."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path("/Users/charlesvaughn/Desktop/VScode_Test/PythonDB4-3-26/sharecontactsfree/python")
sys.path.insert(0, str(ROOT))

DB_PATH = Path("/tmp/share-prune/sharecontacts.db")
os.environ["SHARE_DB_GCS_URI"] = ""  # operate on local file only
os.environ["SHARE_DB_LOCAL_PATH"] = str(DB_PATH)
os.environ["DATABASE_PATH"] = str(DB_PATH)

# Load OAuth client from share .env for token refresh
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from app import db
from app.services.contact_sync import prune_orphan_recipient_contacts

SHARE = "b0b64b16-b3a6-4c43-97f8-f05548e56215"
RESOURCE = "contactGroups/39c12a71884ce63a"
OWNER = "al.ms.lacontacts@gmail.com"

db._active_database_path = DB_PATH
db.init_db()

current_ids: set[str] = set()
for row in db.read_shared_contacts(SHARE):
    rn = str(row.get("resourceName") or "").strip()
    if rn:
        current_ids.add(rn)
print(f"current staged owner ids: {len(current_ids)}")

recipients = [
    "charliekerr.inbox@gmail.com",
    "menfisher@gmail.com",
]
for recip in recipients:
    result = prune_orphan_recipient_contacts(
        share_id=SHARE,
        owner=OWNER,
        group_resource_name=RESOURCE,
        recipient_email=recip,
        current_owner_person_ids=current_ids,
    )
    print(recip, result)
    # Update recipient app_data counts
    data = db.get_app_data(recip) or {}
    groups = list(data.get("sharedGroups") or [])
    changed = False
    mapped = db.count_sync_mappings_for_share(recip, OWNER, SHARE)
    for group in groups:
        if group.get("owner") == OWNER and group.get("resourceName") == RESOURCE:
            group["memberCount"] = len(current_ids)
            group["actualCount"] = mapped
            group["importCursor"] = max(int(group.get("importCursor") or 0), mapped)
            if str(group.get("status") or "").startswith("Shared"):
                group["status"] = "Shared"
                group["pushStatus"] = "Shared"
            changed = True
    if changed:
        data["sharedGroups"] = groups
        db.save_app_data(recip, data)
        print(f"  updated app_data actualCount={mapped}")

print("done")
