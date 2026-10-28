#!/usr/bin/env python3
"""Run a recipient import and print DB progress every 250ms (dev diagnostic)."""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import DATABASE_PATH  # noqa: E402
from app.services.recipient_import import (  # noqa: E402
    _clear_recipient_import_state,
    get_recipient_shared_groups,
    process_shared_import_for_group,
)


def _load_group(recipient: str, owner: str, resource_name: str) -> dict | None:
    conn = sqlite3.connect(DATABASE_PATH)
    row = conn.execute(
        "SELECT data_json FROM app_data WHERE email = ?", (recipient,)
    ).fetchone()
    if not row:
        return None
    data = json.loads(row[0])
    for group in data.get("sharedGroups") or []:
        if group.get("owner") == owner and group.get("resourceName") == resource_name:
            return group
    return None


def _save_group(recipient: str, owner: str, resource_name: str, group: dict) -> None:
    conn = sqlite3.connect(DATABASE_PATH)
    row = conn.execute(
        "SELECT data_json FROM app_data WHERE email = ?", (recipient,)
    ).fetchone()
    if not row:
        return
    data = json.loads(row[0])
    groups = data.get("sharedGroups") or []
    for i, item in enumerate(groups):
        if item.get("owner") == owner and item.get("resourceName") == resource_name:
            groups[i] = group
            break
    data["sharedGroups"] = groups
    conn.execute(
        "UPDATE app_data SET data_json = ?, updated_at = datetime('now') WHERE email = ?",
        (json.dumps(data), recipient),
    )
    conn.commit()


def main() -> None:
    recipient = sys.argv[1] if len(sys.argv) > 1 else "menfisher@gmail.com"
    owner = sys.argv[2] if len(sys.argv) > 2 else "al.ms.lacontacts@gmail.com"
    resource = (
        sys.argv[3]
        if len(sys.argv) > 3
        else "contactGroups/5d6a2fdc886ffa91"
    )

    group = _load_group(recipient, owner, resource)
    if not group:
        print("Group not found")
        sys.exit(1)

    print(f"Group: {group.get('name')} ({group.get('memberCount')} contacts)")
    _clear_recipient_import_state(group)
    member_count = int(group.get("memberCount") or 0)
    group["status"] = f"Ready ({member_count}/{member_count})"
    _save_group(recipient, owner, resource, group)
    print("Reset to Ready. Starting import…")

    done = threading.Event()

    def run_import() -> None:
        try:
            while True:
                result = process_shared_import_for_group(
                    recipient, owner, resource, max_ops=200
                )
                groups = result.get("sharedGroups") or []
                target = next(
                    (
                        g
                        for g in groups
                        if g.get("owner") == owner
                        and g.get("resourceName") == resource
                    ),
                    None,
                )
                if not target:
                    break
                status = str(target.get("status") or "")
                if status.startswith("Shared") or status.startswith("Error"):
                    break
                if not result.get("busy"):
                    time.sleep(0.3)
                    continue
                time.sleep(0.3)
        finally:
            done.set()

    threading.Thread(target=run_import, daemon=True).start()

    seen: set[str] = set()
    while not done.is_set():
        payload = get_recipient_shared_groups(recipient)
        target = next(
            (
                g
                for g in payload.get("sharedGroups") or []
                if g.get("owner") == owner and g.get("resourceName") == resource
            ),
            None,
        )
        if target:
            key = (
                f"{target.get('status')}|{target.get('importCursor')}|"
                f"{target.get('photoDoneCount')}|{target.get('importPhase')}"
            )
            if key not in seen:
                seen.add(key)
                print(
                    f"  {target.get('status')} "
                    f"cursor={target.get('importCursor')} "
                    f"photos={target.get('photoDoneCount')}/{target.get('photoTargetCount')} "
                    f"phase={target.get('importPhase')}"
                )
        time.sleep(0.25)

    payload = get_recipient_shared_groups(recipient)
    target = next(
        (
            g
            for g in payload.get("sharedGroups") or []
            if g.get("owner") == owner and g.get("resourceName") == resource
        ),
        None,
    )
    print("Final:", target.get("status") if target else "missing")
    print(f"Progress snapshots: {len(seen)}")


if __name__ == "__main__":
    main()
