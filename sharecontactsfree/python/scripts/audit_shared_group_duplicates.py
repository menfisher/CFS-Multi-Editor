"""Read-only audit: find duplicate/untracked contacts in a recipient's shared group.

Reports what is in the recipient's Google group versus what our sync mappings
say should be there. Deletes nothing.

Usage:
    python3 scripts/audit_shared_group_duplicates.py <local_db_path> <recipient_email>
"""

from __future__ import annotations

import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

db_path = Path(sys.argv[1]).resolve()
recipient_email = sys.argv[2].strip().lower()

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
    get_group_details,
    normalize_person_resource_name,
    people_service,
)

db._active_database_path = db_path
db.init_db()

data = db.get_app_data(recipient_email) or {}
groups = [
    g
    for g in (data.get("sharedGroups") or [])
    if str(g.get("created") or "").startswith("contactGroups/")
]
if not groups:
    raise SystemExit(f"No shared groups with a created group for {recipient_email}")

creds = recipient_credentials(recipient_email)
if not creds:
    raise SystemExit(f"{recipient_email} is not connected")

service = people_service(creds)

for group in groups:
    group_id = str(group.get("created"))
    share_id = str(group.get("shareId") or "")
    owner = str(group.get("owner") or "")
    resource_name = str(group.get("resourceName") or "")
    print(f"\n=== {group.get('name')} ({group_id}) ===")
    print(f"staged contacts (memberCount): {group.get('memberCount')}")

    details = get_group_details(creds, group_id, 5000)
    members = [
        normalize_person_resource_name(m)
        for m in (details.get("memberResourceNames") or [])
    ]
    members = [m for m in members if m]
    print(f"members in recipient's Google group: {len(members)}")

    by_owner, _ = db.load_sync_mappings(share_id, owner, resource_name, recipient_email)
    tracked = {
        normalize_person_resource_name(str(m.get("recipient_person_id") or ""))
        for m in by_owner.values()
    }
    tracked.discard("")
    print(f"contacts we have a mapping for: {len(tracked)}")

    untracked = [m for m in members if m not in tracked]
    print(f"UNTRACKED members (likely duplicates): {len(untracked)}")

    names: dict[str, str] = {}
    for i in range(0, len(members), 50):
        chunk = members[i : i + 50]
        resp = None
        for attempt in range(5):
            try:
                resp = (
                    service.people()
                    .getBatchGet(
                        resourceNames=chunk, personFields="names,emailAddresses"
                    )
                    .execute()
                )
                break
            except Exception as exc:  # transient 502/429 from People API
                if attempt == 4:
                    print(f"  lookup failed for chunk {i}: {exc}")
                    resp = None
                    break
                time.sleep(2 * (attempt + 1))
        if resp is None:
            continue
        for entry in resp.get("responses") or []:
            person = entry.get("person") or {}
            rn = normalize_person_resource_name(str(person.get("resourceName") or ""))
            display = ""
            for n in person.get("names") or []:
                display = str(n.get("displayName") or "").strip()
                if display:
                    break
            if not display:
                for e in person.get("emailAddresses") or []:
                    display = str(e.get("value") or "").strip()
                    if display:
                        break
            if rn:
                names[rn] = display or "(no name)"

    counts = Counter(names.get(m, "(unknown)") for m in members)
    repeated = {name: n for name, n in counts.items() if n > 1}
    print(f"names appearing more than once: {len(repeated)}")
    print(f"extra copies beyond one each: {sum(n - 1 for n in repeated.values())}")

    by_name: dict[str, list[str]] = defaultdict(list)
    for m in members:
        by_name[names.get(m, "(unknown)")].append(m)
    shown = 0
    for name, n in sorted(repeated.items(), key=lambda kv: -kv[1]):
        ids = by_name[name]
        tracked_ids = [i for i in ids if i in tracked]
        print(f"  {name}: {n} copies, {len(tracked_ids)} tracked")
        shown += 1
        if shown >= 10:
            print("  ...")
            break
