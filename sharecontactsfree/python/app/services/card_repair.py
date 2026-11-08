"""Paced one-recipient-at-a-time repair of Job Title / Field+Meeting labels."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from .. import db
from ..google_auth import owner_credentials, recipient_credentials
from ..people_api import get_people_batch, is_rate_limit_error
from ..people_recipient import (
    assignment_shared_group_name,
    ensure_assignment_contact_label,
    list_group_names,
    normalize_person_resource_name,
    remove_outdated_assignment_labels,
    update_contact,
)
from .contact_hash import compute_contact_hash

logger = logging.getLogger(__name__)

STATUS_EMAIL = "__cfs_card_repair__"
REPAIR_SLICE = 50
RECIPIENT_PERSON_FIELDS = "metadata,memberships,organizations,names"
STALL_SECONDS = 180
PAUSE_STALL_SECONDS = 300
CARD_REPAIR_JOB_TYPE = "card_repair"

_PUBLIC_KEYS = (
    "state",
    "current_recipient",
    "recipients_done",
    "recipients_total",
    "contacts_fixed",
    "contacts_total",
    "last_error",
    "updated_at",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_status() -> dict[str, Any]:
    return {
        "state": "idle",
        "current_recipient": "",
        "recipients_done": 0,
        "recipients_total": 0,
        "contacts_fixed": 0,
        "contacts_total": 0,
        "last_error": "",
        "updated_at": "",
        "recipient_emails": [],
        "recipient_index": 0,
        "mapping_offset": 0,
        "error_recipients": [],
        "pending_person_ids": [],
        "repair_person_ids": [],
    }


def _public_status(status: dict[str, Any]) -> dict[str, Any]:
    return {key: status.get(key) for key in _PUBLIC_KEYS}


def _parse_updated_at(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _apply_stall(status: dict[str, Any]) -> dict[str, Any]:
    state = str(status.get("state") or "")
    if state not in {"queued", "running", "paused_rate_limit"}:
        return status
    stamp = _parse_updated_at(status.get("updated_at"))
    if stamp is None:
        return status
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - stamp).total_seconds()
    limit = PAUSE_STALL_SECONDS if state == "paused_rate_limit" else STALL_SECONDS
    if age <= limit:
        return status
    out = dict(status)
    out["state"] = "stalled"
    return out


def load_card_repair_status() -> dict[str, Any]:
    stored = db.get_app_data(STATUS_EMAIL)
    status = dict(_empty_status())
    if isinstance(stored, dict):
        status.update(stored)
    return status


def save_card_repair_status(status: dict[str, Any]) -> dict[str, Any]:
    payload = dict(status)
    payload["updated_at"] = _now()
    db.save_app_data(STATUS_EMAIL, payload, flush=True)
    return payload


def get_card_repair_status() -> dict[str, Any]:
    return _public_status(_apply_stall(load_card_repair_status()))


def card_repair_summary_line(status: dict[str, Any] | None = None) -> str:
    item = status or get_card_repair_status()
    state = str(item.get("state") or "idle")
    if state in {"idle", ""}:
        return ""
    done = int(item.get("recipients_done") or 0)
    total = int(item.get("recipients_total") or 0)
    current = str(item.get("current_recipient") or "")
    if state == "done":
        return f"Card repair: done {done}/{total}"
    if current:
        return f"Card repair: {state} {done}/{total} — {current}"
    return f"Card repair: {state} {done}/{total}"


def queue_person_for_label_repair(owner_person_id: str) -> None:
    """Remember a CFS contact whose Field+Meeting label changed."""
    person_id = normalize_person_resource_name(owner_person_id)
    if not person_id:
        return
    status = load_card_repair_status()
    pending = [
        normalize_person_resource_name(str(item or ""))
        for item in (status.get("pending_person_ids") or [])
        if str(item or "").strip()
    ]
    if person_id in pending:
        return
    pending.append(person_id)
    status["pending_person_ids"] = pending
    save_card_repair_status(status)


def _pending_person_ids(status: dict[str, Any] | None = None) -> set[str]:
    item = status or load_card_repair_status()
    return {
        normalize_person_resource_name(str(person_id or ""))
        for person_id in (item.get("pending_person_ids") or [])
        if str(person_id or "").strip()
    }


def _staged_assignment_labels(owner: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for staged in db.list_staged_share_groups_for_owner(owner):
        share_id = str(staged.get("share_id") or "").strip()
        if not share_id:
            continue
        for member in db.read_shared_contacts(share_id):
            person_id = normalize_person_resource_name(
                str((member or {}).get("resourceName") or "")
            )
            label = assignment_shared_group_name(member)
            if person_id and label:
                labels[person_id] = label
    return labels


def _collect_repair_person_ids() -> list[str]:
    """Owner Google IDs that need a label fix. Same set for every recipient."""
    ids = set(_pending_person_ids())
    staged_by_owner: dict[str, dict[str, str]] = {}
    labeled: set[str] = set()
    unlabeled: set[str] = set()
    for email in db.list_recipients_with_mapped_contacts():
        for mapping in db.list_sync_mappings_for_recipient(email):
            owner = str(mapping.get("owner") or "")
            person_id = normalize_person_resource_name(
                str(mapping.get("owner_person_id") or "")
            )
            if not owner or not person_id:
                continue
            stored = str(mapping.get("last_assignment_label") or "").strip()
            if stored:
                labeled.add(person_id)
            else:
                unlabeled.add(person_id)
            if owner not in staged_by_owner:
                staged_by_owner[owner] = _staged_assignment_labels(owner)
            current = str(staged_by_owner[owner].get(person_id) or "").strip()
            if stored and current and stored.casefold() != current.casefold():
                ids.add(person_id)
    # One recipient already got the correct label recorded; the others still
    # need that same owner contact repaired.
    ids.update(labeled & unlabeled)
    return sorted(ids)


def _repair_person_ids() -> set[str]:
    status = load_card_repair_status()
    stored = [
        normalize_person_resource_name(str(item or ""))
        for item in (status.get("repair_person_ids") or [])
        if str(item or "").strip()
    ]
    if stored:
        return {item for item in stored if item}
    return set(_collect_repair_person_ids())


def _repair_items_for_recipient(recipient_email: str) -> list[dict[str, Any]]:
    repair_ids = _repair_person_ids()
    if not repair_ids:
        return []
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for mapping in db.list_sync_mappings_for_recipient(recipient_email):
        owner = str(mapping.get("owner") or "")
        owner_person_id = normalize_person_resource_name(
            str(mapping.get("owner_person_id") or "")
        )
        recipient_person_id = normalize_person_resource_name(
            str(mapping.get("recipient_person_id") or "")
        )
        if not owner or not owner_person_id or not recipient_person_id:
            continue
        if owner_person_id not in repair_ids:
            continue
        key = (owner, owner_person_id, recipient_person_id)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                **mapping,
                "owner_person_id": owner_person_id,
                "recipient_person_id": recipient_person_id,
            }
        )
    return items


def _count_repair_candidates(emails: list[str]) -> int:
    return sum(len(_repair_items_for_recipient(email)) for email in emails)


def _persist_mapping_hash(
    mapping: dict[str, Any], last_hash: str, owner_etag: str, last_assignment_label: str = ""
) -> None:
    siblings = db.find_sibling_mappings(
        str(mapping.get("owner") or ""),
        str(mapping.get("recipient_email") or ""),
        str(mapping.get("owner_person_id") or ""),
    )
    rows = siblings or [mapping]
    seen_ids: set[int] = set()
    for row in rows:
        row_id = int(row.get("id") or 0)
        if row_id in seen_ids:
            continue
        if row_id:
            seen_ids.add(row_id)
        db.upsert_sync_mapping(
            share_id=str(row.get("share_id") or mapping.get("share_id") or ""),
            owner=str(row.get("owner") or mapping.get("owner") or ""),
            group_resource_name=str(
                row.get("group_resource_name") or mapping.get("group_resource_name") or ""
            ),
            recipient_email=str(
                row.get("recipient_email") or mapping.get("recipient_email") or ""
            ),
            owner_person_id=str(
                row.get("owner_person_id") or mapping.get("owner_person_id") or ""
            ),
            recipient_person_id=str(
                row.get("recipient_person_id") or mapping.get("recipient_person_id") or ""
            ),
            last_hash=last_hash,
            last_photo_hash=str(row.get("last_photo_hash") or ""),
            owner_etag=owner_etag or str(row.get("owner_etag") or ""),
            last_assignment_label=last_assignment_label,
        )


def _recipient_repair_context(recipient_email: str, recipient_creds) -> dict[str, Any]:
    """Per-recipient caches. Group ids are account-specific, so never share across recipients."""
    app_data = db.get_app_data(recipient_email) or {}
    protected = {
        str(group.get("created") or "").strip()
        for group in (app_data.get("sharedGroups") or [])
        if isinstance(group, dict) and str(group.get("created") or "").startswith("contactGroups/")
    }
    try:
        names = list_group_names(recipient_creds)
    except Exception:
        logger.exception("Could not list contact groups for %s", recipient_email)
        names = {}
    ids = {name: group_id for group_id, name in names.items() if name}
    return {"ids": ids, "names": names, "protected": protected}


def _membership_group_ids(person: dict[str, Any] | None) -> set[str]:
    ids: set[str] = set()
    for membership in (person or {}).get("memberships") or []:
        if not isinstance(membership, dict):
            continue
        group_id = str(
            (membership.get("contactGroupMembership") or {}).get("contactGroupResourceName")
            or ""
        ).strip()
        if group_id:
            ids.add(group_id)
    return ids


def _repair_mapping(
    mapping: dict[str, Any],
    owner_people: dict[str, dict[str, Any]],
    recipient_creds,
    context: dict[str, Any],
    recipient_people: dict[str, dict[str, Any]],
) -> str:
    owner_person_id = str(mapping.get("owner_person_id") or "")
    owner_person = owner_people.get(owner_person_id)
    if not owner_person:
        return "missing_owner"
    recipient_person_id = str(mapping.get("recipient_person_id") or "")
    recipient_person = recipient_people.get(recipient_person_id)
    if not recipient_person:
        return "missing_recipient"
    etag = str(recipient_person.get("etag") or "")
    if not etag:
        return "missing_recipient"

    contact_hash = compute_contact_hash(owner_person)
    hash_changed = contact_hash != str(mapping.get("last_hash") or "")
    if hash_changed and not update_contact(
        recipient_creds, recipient_person_id, owner_person, etag
    ):
        return "update_failed"

    new_assignment_label = assignment_shared_group_name(owner_person)
    membership_ids = _membership_group_ids(recipient_person)
    current_group_id = str((context.get("ids") or {}).get(new_assignment_label) or "")
    if not current_group_id or current_group_id not in membership_ids:
        ensure_assignment_contact_label(
            recipient_creds, recipient_person_id, owner_person, context["ids"]
        )
    remove_outdated_assignment_labels(
        recipient_creds,
        recipient_person_id,
        new_assignment_label,
        context["protected"],
        context["names"],
        memberships=list(recipient_person.get("memberships") or []),
    )
    _persist_mapping_hash(
        mapping,
        contact_hash,
        str(owner_person.get("etag") or ""),
        last_assignment_label=new_assignment_label,
    )
    return "ok"


def start_card_repair(*, resume: bool = False, restart: bool = False) -> dict[str, Any]:
    status = _apply_stall(load_card_repair_status())
    state = str(status.get("state") or "idle")
    if state in {"queued", "running"} and not resume:
        return _public_status(status)
    if state == "paused_rate_limit" and not resume:
        return _public_status(status)
    if state == "done" and not resume and not restart:
        return _public_status(status)

    if state == "stalled" or resume:
        emails = list(status.get("recipient_emails") or [])
        pending = list(status.get("pending_person_ids") or [])
        if not emails:
            emails = db.list_recipients_with_mapped_contacts()
        status["recipient_emails"] = emails
        status["pending_person_ids"] = pending
        status["repair_person_ids"] = list(status.get("repair_person_ids") or []) or _collect_repair_person_ids()
        status["recipients_total"] = len(emails)
        status["contacts_total"] = _count_repair_candidates(emails)
    else:
        pending = list(status.get("pending_person_ids") or [])
        emails = db.list_recipients_with_mapped_contacts()
        status = _empty_status()
        status["pending_person_ids"] = pending
        status["repair_person_ids"] = _collect_repair_person_ids()
        status["recipient_emails"] = emails
        status["recipients_total"] = len(emails)
        status["contacts_total"] = _count_repair_candidates(emails)

    if not status.get("recipient_emails"):
        status["state"] = "done"
        status["last_error"] = "No mapped recipient contacts were found."
        save_card_repair_status(status)
        return _public_status(status)

    status["state"] = "queued"
    status["last_error"] = ""
    save_card_repair_status(status)
    if not db.has_active_job_type(CARD_REPAIR_JOB_TYPE):
        db.enqueue_push_job(
            owner=STATUS_EMAIL,
            job_type=CARD_REPAIR_JOB_TYPE,
            payload={},
        )
    from .push_worker import kick_push_queue

    kick_push_queue(max_jobs=1)
    return _public_status(load_card_repair_status())


def run_card_repair_slice(
    job: dict[str, Any] | None = None, wall_seconds: float | None = None
) -> dict[str, Any]:
    status = load_card_repair_status()
    emails = [str(item) for item in (status.get("recipient_emails") or []) if str(item)]
    if not emails:
        emails = db.list_recipients_with_mapped_contacts()
        status["recipient_emails"] = emails
        status["recipients_total"] = len(emails)
        status["contacts_total"] = _count_repair_candidates(emails)
    if not emails:
        status["state"] = "done"
        status["current_recipient"] = ""
        save_card_repair_status(status)
        if job:
            db.update_push_job(int(job["id"]), status="done", last_error="")
        return {"status": "done", "reason": "empty"}

    status["state"] = "running"
    save_card_repair_status(status)

    recipient_index = max(0, int(status.get("recipient_index") or 0))
    mapping_offset = max(0, int(status.get("mapping_offset") or 0))
    deadline = time.monotonic() + max(8.0, float(wall_seconds or 20.0) - 2.0)
    contexts: dict[str, dict[str, Any]] = {}
    owner_people_cache: dict[str, dict[str, dict[str, Any]]] = {}

    try:
        while time.monotonic() < deadline and recipient_index < len(emails):
            recipient_email = emails[recipient_index]
            status["current_recipient"] = recipient_email
            items = _repair_items_for_recipient(recipient_email)
            if mapping_offset >= len(items):
                recipient_index += 1
                mapping_offset = 0
                status["recipients_done"] = min(recipient_index, len(emails))
                continue

            creds = recipient_credentials(recipient_email)
            if not creds:
                status["last_error"] = f"{recipient_email} is not connected"
                errors = list(status.get("error_recipients") or [])
                if recipient_email not in errors:
                    errors.append(recipient_email)
                status["error_recipients"] = errors
                recipient_index += 1
                mapping_offset = 0
                status["recipients_done"] = min(recipient_index, len(emails))
                continue

            owner = str(items[mapping_offset].get("owner") or "")
            batch: list[dict[str, Any]] = []
            for item in items[mapping_offset : mapping_offset + REPAIR_SLICE]:
                if str(item.get("owner") or "") != owner:
                    break
                batch.append(item)
            if not batch:
                mapping_offset += 1
                continue
            owner_ids = list(
                dict.fromkeys(
                    str(item.get("owner_person_id") or "")
                    for item in batch
                    if str(item.get("owner") or "") == owner
                )
            )
            missing_owner_ids = [
                person_id
                for person_id in owner_ids
                if person_id not in (owner_people_cache.get(owner) or {})
            ]
            if missing_owner_ids:
                owner_creds = owner_credentials(owner)
                if not owner_creds:
                    status["last_error"] = f"Owner {owner} is not signed in"
                    recipient_index += 1
                    mapping_offset = 0
                    status["recipients_done"] = min(recipient_index, len(emails))
                    continue
                fetched = get_people_batch(owner_creds, missing_owner_ids)
                owner_people_cache.setdefault(owner, {}).update(fetched)

            recipient_ids = [
                str(item.get("recipient_person_id") or "")
                for item in batch
                if str(item.get("recipient_person_id") or "")
            ]
            recipient_people = get_people_batch(
                creds, recipient_ids, person_fields=RECIPIENT_PERSON_FIELDS
            )
            if recipient_email not in contexts:
                contexts[recipient_email] = _recipient_repair_context(recipient_email, creds)
            for mapping in batch:
                result = _repair_mapping(
                    mapping,
                    owner_people_cache.get(owner) or {},
                    creds,
                    contexts[recipient_email],
                    recipient_people,
                )
                mapping_offset += 1
                if result == "ok":
                    status["contacts_fixed"] = int(status.get("contacts_fixed") or 0) + 1
                    status["last_error"] = ""
                else:
                    status["last_error"] = f"{recipient_email}: {result}"
            status["recipient_index"] = recipient_index
            status["mapping_offset"] = mapping_offset
            save_card_repair_status(status)

        status["recipient_index"] = recipient_index
        status["mapping_offset"] = mapping_offset
        if recipient_index >= len(emails):
            status["state"] = "done"
            status["current_recipient"] = ""
            status["recipients_done"] = len(emails)
            save_card_repair_status(status)
            if job:
                db.update_push_job(int(job["id"]), status="done", last_error="")
            return {"status": "done"}

        status["state"] = "queued"
        save_card_repair_status(status)
        if job:
            db.update_push_job(int(job["id"]), requeue=True, last_error="", reset_attempts=True)
        return {"status": "queued", "reason": "slice"}
    except Exception as exc:
        if is_rate_limit_error(exc):
            status["state"] = "paused_rate_limit"
            status["last_error"] = "Google rate limit, waiting"
            status["recipient_index"] = recipient_index
            status["mapping_offset"] = mapping_offset
            save_card_repair_status(status)
            if job:
                db.update_push_job(
                    int(job["id"]),
                    requeue=True,
                    last_error="Google rate limit, waiting",
                    reset_attempts=True,
                )
            return {"status": "queued", "reason": "rate_limit"}
        logger.exception("card repair slice failed")
        status["state"] = "error"
        status["last_error"] = str(exc)[:500]
        status["recipient_index"] = recipient_index
        status["mapping_offset"] = mapping_offset
        save_card_repair_status(status)
        raise
