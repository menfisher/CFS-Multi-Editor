#!/usr/bin/env python3
"""Re-upload already-imported recipient contact photos at full resolution.

Earlier imports uploaded the small =s100 thumbnail. This re-fetches each mapped
contact's photo at full resolution (via the fixed fetch logic in people_recipient)
and re-uploads it to the recipient's Google contact in HTTP batches, retrying any
failures (e.g. transient quota 429s) with backoff.

Runs entirely against Google APIs using tokens/mappings from the share DB; it does
not mutate the share DB, so it is safe to run while the service is live.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _bootstrap_env(database: str) -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT.parent.parent / ".env")
    os.environ["DATABASE_PATH"] = database
    os.environ.pop("SHARE_DB_GCS_URI", None)


def refresh_recipient_photos(
    *,
    owner_email: str,
    recipient_email: str,
    group_resource_name: str,
    share_id: str,
    database: str = "/tmp/sharecontacts-prod.db",
    chunk: int = 50,
    limit: int = 0,
    max_rounds: int = 4,
) -> dict:
    _bootstrap_env(database)
    from app import db
    from app.google_auth import owner_credentials, recipient_credentials
    from app.people_recipient import (
        batch_upload_contact_photos,
        has_contact_photo,
        prefetch_photo_bytes,
    )

    db._active_database_path = Path(database)  # noqa: SLF001

    owner_email = db.normalize_email(owner_email)
    recipient_email = db.normalize_email(recipient_email)

    rec_creds = recipient_credentials(recipient_email)
    if not rec_creds:
        raise RuntimeError(f"{recipient_email}: no usable recipient OAuth token")
    own_creds = owner_credentials(owner_email)
    own_token = own_creds.token if own_creds else None
    if not own_token:
        raise RuntimeError(f"{owner_email}: no usable owner OAuth token (needed to fetch photos)")

    by_owner, _ = db.load_sync_mappings(
        share_id, owner_email, group_resource_name, recipient_email
    )
    staged = db.read_shared_contacts(share_id)

    work: list[tuple[dict, str]] = []
    for member in staged:
        opid = str(member.get("resourceName") or "")
        if not opid.startswith("people/") or not has_contact_photo(member):
            continue
        rid = str((by_owner.get(opid) or {}).get("recipient_person_id") or "")
        if rid:
            work.append((member, rid))
    if limit:
        work = work[:limit]

    total = len(work)
    print(f"{recipient_email}: {total} photo contacts to refresh", flush=True)

    done_ok = 0
    pending = work
    for round_no in range(1, max_rounds + 1):
        if not pending:
            break
        failed: list[tuple[dict, str]] = []
        processed = 0
        for offset in range(0, len(pending), chunk):
            batch = pending[offset : offset + chunk]
            indexed = [(i, m) for i, (m, _rid) in enumerate(batch)]
            idx_to_rid = {i: rid for i, (_m, rid) in enumerate(batch)}
            try:
                photos = prefetch_photo_bytes(
                    indexed, rec_creds.token or "", owner_token=own_token
                )
                uploads = [
                    (i, idx_to_rid[i], b64)
                    for i, b64 in photos.items()
                    if b64 and idx_to_rid.get(i)
                ]
                outcomes = (
                    batch_upload_contact_photos(rec_creds, uploads) if uploads else {}
                )
            except Exception as exc:
                print(f"  batch error (will retry later): {type(exc).__name__}: {exc}", flush=True)
                outcomes = {}
            for i, (m, rid) in enumerate(batch):
                if outcomes.get(i):
                    done_ok += 1
                else:
                    failed.append((m, rid))
            processed += len(batch)
            print(
                f"  round {round_no}: {processed}/{len(pending)} "
                f"(ok_total={done_ok}, failed_this_round={len(failed)})",
                flush=True,
            )
            time.sleep(1.5)
        pending = failed
        if pending:
            backoff = min(60, 10 * round_no)
            print(f"  round {round_no}: {len(pending)} failed, backing off {backoff}s", flush=True)
            time.sleep(backoff)

    return {
        "recipient": recipient_email,
        "total": total,
        "uploaded_ok": done_ok,
        "still_failed": len(pending),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", default="al.ms.lacontacts@gmail.com")
    parser.add_argument(
        "--recipients",
        default="menfisher@gmail.com,charliekerr.inbox@gmail.com",
        help="Comma-separated recipient emails",
    )
    parser.add_argument("--group-resource-name", default="contactGroups/39c12a71884ce63a")
    parser.add_argument("--share-id", default="b0b64b16-b3a6-4c43-97f8-f05548e56215")
    parser.add_argument("--database", default=os.getenv("DATABASE_PATH", "/tmp/sharecontacts-prod.db"))
    parser.add_argument("--chunk", type=int, default=50)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    recipients = [e.strip() for e in args.recipients.split(",") if e.strip()]
    for recipient in recipients:
        result = refresh_recipient_photos(
            owner_email=args.owner,
            recipient_email=recipient,
            group_resource_name=args.group_resource_name,
            share_id=args.share_id,
            database=args.database,
            chunk=args.chunk,
            limit=args.limit,
        )
        print("RESULT:", result, flush=True)


if __name__ == "__main__":
    main()
