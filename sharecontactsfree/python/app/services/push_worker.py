"""Background push job queue for server-side recipient sync."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Any

from .. import db
from ..google_auth import recipient_credentials
from .app_data import maybe_refresh_staged_shared_contacts_before_import
from .contact_sync import sync_contact_changes
from .recipient_import import process_shared_import_for_group

logger = logging.getLogger(__name__)

_worker_thread: threading.Thread | None = None
_worker_started = False
_worker_lock = threading.Lock()

MAX_FULL_GROUP_PASSES = 120
MAX_CONTACT_BATCH_PASSES = 20
# Release a long-running job so other queued groups (e.g. TriState) can make progress.
MAX_FULL_GROUP_WALL_SECONDS = 240

# Hard ceiling for a single job. A job is *supposed* to self-yield at
# MAX_FULL_GROUP_WALL_SECONDS, but that check only runs between passes — it cannot
# fire while the thread is blocked inside a single pass that makes many sequential
# Google calls (each only individually bounded). The watchdog runs every job in a
# daemon subthread and abandons it if it blows past this ceiling, so one stalled
# job can never permanently wedge the single worker thread (and the whole queue).
# Jobs also run inside poll/kick requests, so this must stay under Cloud Run's
# 300s request timeout or the request 504s and the import appears frozen.
MAX_JOB_HARD_SECONDS = int(os.getenv("SHARE_JOB_HARD_TIMEOUT", "200"))
# How many watchdog timeouts a single job may rack up before it is parked as an
# error instead of retried (prevents an unfixable "poison" job looping forever).
MAX_WATCHDOG_STRIKES = int(os.getenv("SHARE_JOB_WATCHDOG_STRIKES", "3"))

# Jobs whose worker thread was abandoned after a watchdog timeout but may still be
# alive — used to avoid running the same job twice concurrently.
_abandoned_jobs: dict[int, threading.Thread] = {}
_watchdog_strikes: dict[int, int] = {}


def start_push_worker() -> None:
    global _worker_thread, _worker_started
    with _worker_lock:
        reclaimed = db.reclaim_all_running_push_jobs_on_startup()
        if reclaimed:
            logger.info("Reclaimed %s running share push jobs after cold start", reclaimed)
        _repair_recipient_app_data_if_needed()
        if _worker_started and _worker_thread and _worker_thread.is_alive():
            return
        # Cold start only: re-drive any import that was left mid-flight (e.g. a
        # redeploy/instance-kill during the photo phase) so it resumes instead of
        # silently stalling with no job behind it.
        _resume_orphaned_imports()
        _worker_started = True
        _worker_thread = threading.Thread(target=_worker_loop, name="share-push-worker", daemon=True)
        _worker_thread.start()


def kick_push_queue(max_jobs: int = 1, wall_seconds: float = 20.0) -> None:
    """Process queued push jobs while this Cloud Run instance is warm.

    Callers are HTTP handlers, so keep the slice short — Cloud Run kills any
    request that runs past 300s.
    """
    start_push_worker()
    db.reclaim_stale_push_jobs()
    if db.count_queued_push_jobs() <= 0:
        return
    run_pending_jobs(max_jobs=max_jobs, wall_seconds=wall_seconds)


_pump_lock = threading.Lock()


def _current_job_wall_seconds(wall_seconds: float | None) -> float:
    if wall_seconds:
        return max(1.0, float(wall_seconds))
    return float(MAX_FULL_GROUP_WALL_SECONDS)


def pump_push_queue(max_seconds: float = 4.0) -> None:
    """Run queued jobs for a short slice so Retry/polls do not hold one HTTP request for minutes.

    Cloud Run only gives CPU during a request, so progress polls call this too.
    """
    start_push_worker()
    db.reclaim_stale_push_jobs()
    if db.count_queued_push_jobs() <= 0:
        return
    if not _pump_lock.acquire(blocking=False):
        return
    try:
        run_pending_jobs(max_jobs=1, wall_seconds=max(1.0, float(max_seconds)))
    finally:
        _pump_lock.release()


def _repair_recipient_app_data_if_needed() -> None:
    from .recipient_recovery import repair_recipient_app_data_from_staged

    for recipient_email in db.list_recipient_emails():
        try:
            repair_recipient_app_data_from_staged(recipient_email)
        except Exception:
            logger.exception("Failed to repair recipient app_data for %s", recipient_email)


def _latest_recipient_push_job(
    owner: str, resource_name: str, recipient_email: str
) -> dict | None:
    recipient_key = db.normalize_email(recipient_email)
    for row in db.list_push_jobs(owner, resource_name, limit=40):
        if db.normalize_email(str(row.get("recipient_email") or "")) == recipient_key:
            return row
    return None


def _has_open_import_queues(group: dict) -> bool:
    return bool(
        list(group.get("photoPendingIndexes") or [])
        or list(group.get("photoRetryIndexes") or [])
        or list(group.get("retryIndexes") or [])
    )


def _contacts_mapping_is_complete(group: dict, recipient_email: str) -> bool:
    member_count = int(group.get("memberCount") or 0)
    cursor = int(group.get("importCursor") or 0)
    if member_count <= 0 or cursor < member_count:
        return False
    share_id = str(group.get("shareId") or "")
    owner = str(group.get("owner") or "")
    if not share_id or not owner:
        return False
    mapped = db.count_sync_mappings_for_share(recipient_email, owner, share_id)
    return mapped >= member_count


def owner_group_is_gone_error(error: object, resource_name: str) -> bool:
    """True when Google 404'd the owner's contact group (deleted label)."""
    resource = str(resource_name or "").strip()
    if not resource.startswith("contactGroups/"):
        return False
    status = 0
    resp = getattr(error, "resp", None)
    if resp is not None:
        status = int(getattr(resp, "status", 0) or 0)
    blob = f"{getattr(error, 'uri', '') or ''} {error or ''}"
    if resource not in blob:
        return False
    if status == 404:
        return True
    lowered = blob.lower()
    return "404" in blob and (
        "not found" in lowered or "requested entity was not found" in lowered
    )


def _jobs_say_owner_group_gone(group: dict, recipient_email: str) -> bool:
    """True when any recent job 404'd this owner group, even if a newer one is queued."""
    resource_name = str(group.get("resourceName") or "")
    owner = str(group.get("owner") or "")
    recipient_key = db.normalize_email(recipient_email)
    for row in db.list_push_jobs(owner, resource_name, limit=40):
        if db.normalize_email(str(row.get("recipient_email") or "")) != recipient_key:
            continue
        if owner_group_is_gone_error(str(row.get("last_error") or ""), resource_name):
            return True
    return False


def reconcile_vanished_owner_share_rows(recipient_email: str) -> int:
    """Hide leftover Pushing rows whose owner Google group 404s. Returns rows removed."""
    from .app_data import retire_vanished_owner_share

    recipient_email = db.normalize_email(recipient_email)
    data = db.get_app_data(recipient_email) or {}
    removed = 0
    for group in list(data.get("sharedGroups") or []):
        if not _jobs_say_owner_group_gone(group, recipient_email):
            continue
        removed += retire_vanished_owner_share(
            recipient_email,
            str(group.get("owner") or ""),
            str(group.get("resourceName") or ""),
        )
    return removed


def reconcile_stale_share_rows(recipient_email: str) -> int:
    return reconcile_stale_finished_photo_rows(
        recipient_email
    ) + reconcile_vanished_owner_share_rows(recipient_email)


def recipient_finished_import_is_stale_photo_row(
    group: dict, recipient_email: str
) -> bool:
    """True when Photos (n/m) is leftover UI from a completed import.

    Refresh/poll used to treat that counter as live work and queue another
    full_group job. Check mappings + the last job before doing any work.
    """
    status = str(group.get("status") or "")
    phase = str(group.get("importPhase") or "")
    if not (
        phase == "photos"
        or status.startswith(("Photos (", "Importing photos", "Pushing"))
    ):
        return False
    if _has_open_import_queues(group):
        return False
    if not _contacts_mapping_is_complete(group, recipient_email):
        return False
    job = _latest_recipient_push_job(
        str(group.get("owner") or ""),
        str(group.get("resourceName") or ""),
        recipient_email,
    )
    if job and str(job.get("status") or "") in ("queued", "running"):
        return False
    return True


def reconcile_stale_finished_photo_rows(recipient_email: str) -> int:
    """Rewrite finished Photos (n/m) rows back to Shared. Returns rows changed."""
    recipient_email = db.normalize_email(recipient_email)
    data = db.get_app_data(recipient_email) or {}
    groups = list(data.get("sharedGroups") or [])
    changed = 0
    for group in groups:
        if not recipient_finished_import_is_stale_photo_row(group, recipient_email):
            continue
        group["status"] = "Shared"
        group["pushStatus"] = "Shared"
        group["importPhase"] = "done"
        changed += 1
    if changed:
        data["sharedGroups"] = groups
        db.save_app_data(recipient_email, data)
    return changed


def _is_idle_photo_scan(group: dict) -> bool:
    """True for leftover Photos (0/0) scans that never find work.

    Those jobs hang on a Google lookup for the full watchdog, then get
    re-queued, so every new share freezes behind them.
    """
    if list(group.get("photoPendingIndexes") or []) or list(
        group.get("photoRetryIndexes") or []
    ):
        return False
    if int(group.get("photoTargetCount") or 0) > 0:
        return False
    return str(group.get("status") or "").startswith("Photos (0/0)")


def _group_needs_import_job(group: dict, recipient_email: str) -> bool:
    """True when this group still needs a worker pass (mid-import or one-shot repair)."""
    in_progress_prefixes = ("Importing", "Photos", "Pushing", "Queued")
    status = str(group.get("status") or "")
    phase = str(group.get("importPhase") or "")
    if status.startswith(("Error", "Ready", "Waiting")):
        return False
    if _jobs_say_owner_group_gone(group, recipient_email):
        return False
    if _is_idle_photo_scan(group):
        return False
    if recipient_finished_import_is_stale_photo_row(group, recipient_email):
        return False
    if list(group.get("retryIndexes") or []):
        return True
    if status.startswith("Shared"):
        member_count = int(group.get("memberCount") or 0)
        actual = int(group.get("actualCount") or 0)
        if member_count > 0 and actual < member_count:
            return True
        mapped = db.count_sync_mappings_for_share(
            recipient_email,
            str(group.get("owner") or ""),
            str(group.get("shareId") or ""),
        )
        if actual > max(mapped, member_count) and int(group.get("repairCheckedActual") or -1) != actual:
            return True
        # Shared + a stale photoDone counter is not unfinished work. Retry
        # fills photoPendingIndexes; only that (or a short/duplicate group)
        # should reopen a finished share.
        if list(group.get("photoPendingIndexes") or []) or list(
            group.get("photoRetryIndexes") or []
        ):
            return True
        return False
    return phase in ("contacts", "photos") or status.startswith(in_progress_prefixes)


def _resume_orphaned_imports() -> None:
    """Re-enqueue connected shared groups left mid-import with no active push job.

    If a worker thread dies mid-pass (instance killed, redeploy during the photo
    phase, etc.), a group can be stranded in an in-progress state like
    'Photos (0/20)' with its job already marked done — nothing left to drive it.
    On cold start we scan for those and queue a fresh full_group job so the import
    always finishes. Enqueue directly (no kick) to avoid re-entering the worker
    lock; the worker loop picks the jobs up within ~1s.
    """
    resumed = 0
    for recipient_email in db.list_recipient_emails():
        reconcile_stale_share_rows(recipient_email)
        # NB: don't call recipient_credentials() here — it refreshes the OAuth
        # token over the network and this runs inside the worker-startup lock.
        # The job handler (_run_full_group_job) re-checks credentials when it runs
        # and parks disconnected recipients as "Waiting for recipient to connect".
        data = db.get_app_data(recipient_email) or {}
        seen: set[tuple[str, str]] = set()
        for group in data.get("sharedGroups") or []:
            if not _group_needs_import_job(group, recipient_email):
                continue
            owner = str(group.get("owner") or "").strip()
            resource_name = str(group.get("resourceName") or "").strip()
            if not owner or not resource_name:
                continue
            key = (owner, resource_name)
            if key in seen:
                continue
            seen.add(key)
            if db.has_active_push_job(owner, resource_name, recipient_email):
                continue
            try:
                db.enqueue_push_job(
                    owner=owner,
                    resource_name=resource_name,
                    recipient_email=recipient_email,
                    job_type="full_group",
                    payload={"resume": True},
                )
                resumed += 1
                logger.info(
                    "Resumed orphaned import: %s -> %s (%s)",
                    owner,
                    recipient_email,
                    group.get("status") or group.get("importPhase"),
                )
            except Exception:
                logger.exception(
                    "Failed to resume orphaned import for %s", recipient_email
                )
    if resumed:
        logger.info("Re-enqueued %s orphaned import(s) on startup", resumed)


def resume_stalled_imports_for_recipient(recipient_email: str) -> int:
    """Re-queue this recipient's mid-import groups that have no live job.

    A job parked as error (watchdog, max attempts) leaves the import frozen with
    nothing to drive it. Polling is the one moment we know the recipient cares.
    """
    reconcile_stale_share_rows(recipient_email)
    data = db.get_app_data(recipient_email) or {}
    resumed = 0
    seen: set[tuple[str, str]] = set()
    for group in data.get("sharedGroups") or []:
        if not _group_needs_import_job(group, recipient_email):
            continue
        owner = str(group.get("owner") or "").strip()
        resource_name = str(group.get("resourceName") or "").strip()
        if not owner or not resource_name:
            continue
        key = (owner, resource_name)
        if key in seen or db.has_active_push_job(owner, resource_name, recipient_email):
            continue
        seen.add(key)
        try:
            db.enqueue_push_job(
                owner=owner,
                resource_name=resource_name,
                recipient_email=recipient_email,
                job_type="full_group",
                payload={},
            )
            resumed += 1
            logger.info(
                "Resumed stalled import on poll: %s -> %s (%s)",
                owner,
                recipient_email,
                group.get("status") or group.get("importPhase"),
            )
        except Exception:
            logger.exception("Failed to resume stalled import for %s", recipient_email)
    return resumed


def _worker_loop() -> None:
    while True:
        try:
            db.reclaim_stale_push_jobs()
            run_pending_jobs(max_jobs=1)
        except Exception:
            logger.exception("share push worker loop error")
        time.sleep(1.0)


def enqueue_full_group_push(
    owner: str,
    resource_name: str,
    recipient_email: str,
    *,
    pump_seconds: float | None = None,
) -> int:
    if _jobs_say_owner_group_gone(
        {"owner": owner, "resourceName": resource_name},
        recipient_email,
    ):
        from .app_data import retire_vanished_owner_share

        retire_vanished_owner_share(recipient_email, owner, resource_name)
        latest = _latest_recipient_push_job(owner, resource_name, recipient_email)
        return int((latest or {}).get("id") or 0)
    if db.has_active_push_job(owner, resource_name, recipient_email):
        rows = db.list_push_jobs(owner, resource_name, limit=20)
        for row in rows:
            if (
                row.get("recipient_email") == db.normalize_email(recipient_email)
                and row.get("status") in ("queued", "running")
            ):
                return int(row["id"])
    job_id = db.enqueue_push_job(
        owner=owner,
        resource_name=resource_name,
        recipient_email=recipient_email,
        job_type="full_group",
        payload={},
    )
    if pump_seconds is None:
        kick_push_queue(max_jobs=1)
    elif pump_seconds > 0:
        pump_push_queue(max_seconds=pump_seconds)
    else:
        start_push_worker()
    return job_id


CONTACT_BATCH_SLICE = 25


def enqueue_contact_push(owner: str, owner_person_ids: list[str]) -> int:
    normalized = list(
        dict.fromkeys(
            str(pid).strip()
            for pid in owner_person_ids
            if str(pid or "").strip().startswith("people/")
        )
    )
    job_id = 0
    for index in range(0, len(normalized), CONTACT_BATCH_SLICE):
        chunk = normalized[index : index + CONTACT_BATCH_SLICE]
        job_id = db.enqueue_push_job(
            owner=owner,
            job_type="contact_batch",
            payload={"owner_person_ids": chunk},
        )
    if job_id:
        kick_push_queue(max_jobs=1)
    return job_id


def _dispatch_job(
    job: dict[str, Any], wall_seconds: float | None = None
) -> dict[str, Any]:
    if job["job_type"] == "full_group":
        return _run_full_group_job(job, wall_seconds)
    if job["job_type"] == "contact_batch":
        return _run_contact_batch_job(job)
    if job["job_type"] == "card_repair":
        from .card_repair import run_card_repair_slice

        result = run_card_repair_slice(job)
        if result.get("reason") == "rate_limit":
            time.sleep(60)
        return {"job_id": job["id"], **result}
    db.update_push_job(job["id"], status="error", last_error="Unknown job type")
    return {"job_id": job["id"], "status": "error"}


def _handle_job_exception(job: dict[str, Any], exc: BaseException) -> dict[str, Any]:
    logger.exception("push job %s failed", job.get("id"))
    err = str(exc)
    resource_name = str(job.get("resource_name") or "")
    if owner_group_is_gone_error(exc, resource_name) or owner_group_is_gone_error(
        err, resource_name
    ):
        db.update_push_job(job["id"], status="error", last_error=err[:500])
        from .app_data import retire_vanished_owner_share

        retire_vanished_owner_share(
            str(job.get("recipient_email") or ""),
            str(job.get("owner") or ""),
            resource_name,
        )
        return {
            "job_id": job["id"],
            "status": "error",
            "error": err,
            "reason": "owner_group_gone",
        }
    disk_io = "disk I/O error" in err.lower()
    if disk_io or int(job.get("attempts") or 0) < 5:
        db.update_push_job(job["id"], requeue=True, last_error=err[:500])
    else:
        db.update_push_job(job["id"], status="error", last_error=err[:500])
    return {"job_id": job["id"], "status": "error", "error": err}


WATCHDOG_STALL_SECONDS = int(os.getenv("SHARE_JOB_WATCHDOG_STALL", "90"))


def _watchdog_join_seconds(wall_seconds: float | None = None) -> float:
    """How long to wait before checking whether a job thread has stalled.

    Poll slices already stop the import via time_budget_ms. Do not shorten this
    below MAX_JOB_HARD_SECONDS or a still-working photo batch gets abandoned.
    """
    del wall_seconds
    return float(MAX_JOB_HARD_SECONDS)


def _parse_job_timestamp(value: str) -> float | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _job_is_stalled(job_id: int, *, now: float | None = None) -> bool:
    """True when the job has not touched the DB for WATCHDOG_STALL_SECONDS."""
    job = db.get_push_job(job_id)
    if not job:
        return True
    stamped = _parse_job_timestamp(str(job.get("updated_at") or ""))
    if stamped is None:
        return True
    current = time.time() if now is None else float(now)
    return (current - stamped) >= float(WATCHDOG_STALL_SECONDS)


def _abandon_watchdog_job(job_id: int, thread: threading.Thread) -> dict[str, Any]:
    _abandoned_jobs[job_id] = thread
    stalled = _job_is_stalled(job_id)
    if not stalled:
        # Still uploading. Requeue for the next kick/scheduler slice without
        # burning a strike — a human should not have to restart this.
        db.update_push_job(
            job_id,
            requeue=True,
            last_error="time slice ended; will resume",
            reset_attempts=True,
        )
        logger.info("push job %s yielded after watchdog; still making progress", job_id)
        return {"job_id": job_id, "status": "queued", "reason": "slice_yield"}
    strikes = _watchdog_strikes.get(job_id, 0) + 1
    _watchdog_strikes[job_id] = strikes
    msg = (
        f"job exceeded hard time limit ({MAX_JOB_HARD_SECONDS}s) and was "
        f"abandoned (stalled Google API call); strike {strikes}"
    )
    logger.error("push job %s watchdog timeout (strike %s)", job_id, strikes)
    if strikes >= MAX_WATCHDOG_STRIKES:
        db.update_push_job(job_id, status="error", last_error=msg[:500])
        return {"job_id": job_id, "status": "error", "reason": "watchdog_timeout"}
    db.update_push_job(job_id, requeue=True, last_error=msg[:500])
    return {"job_id": job_id, "status": "queued", "reason": "watchdog_timeout"}


def _run_job_with_watchdog(
    job: dict[str, Any], wall_seconds: float | None = None
) -> dict[str, Any]:
    """Run a job in a daemon subthread bounded by MAX_JOB_HARD_SECONDS.

    A job that is still touching the DB is not stalled — requeue it for the
    next kick/scheduler slice instead of parking it. Only a silent hang
    (no updated_at for WATCHDOG_STALL_SECONDS) burns a watchdog strike.
    """
    job_id = int(job["id"])

    prev = _abandoned_jobs.get(job_id)
    if prev is not None:
        if prev.is_alive():
            # Prior slice is still uploading. Wait for this request's budget
            # rather than starting a second worker or spinning.
            wait_for = float(wall_seconds or MAX_JOB_HARD_SECONDS)
            prev.join(max(1.0, wait_for))
            if prev.is_alive():
                return _abandon_watchdog_job(job_id, prev)
            _abandoned_jobs.pop(job_id, None)
            current = db.get_push_job(job_id) or {}
            if current.get("status") in ("done", "error"):
                return {
                    "job_id": job_id,
                    "status": current.get("status"),
                    "reason": "abandoned_finished",
                }
            db.update_push_job(job_id, requeue=True, reset_attempts=True)
            return {"job_id": job_id, "status": "queued", "reason": "abandoned_finished"}
        _abandoned_jobs.pop(job_id, None)

    holder: dict[str, Any] = {}

    def _target() -> None:
        try:
            holder["outcome"] = _dispatch_job(job, wall_seconds)
        except BaseException as exc:  # noqa: BLE001 - surfaced to caller below
            holder["exc"] = exc

    thread = threading.Thread(
        target=_target, name=f"share-push-job-{job_id}", daemon=True
    )
    thread.start()
    join_seconds = _watchdog_join_seconds(wall_seconds)
    max_wait = min(float(wall_seconds or join_seconds), 280.0)
    deadline = time.monotonic() + max(1.0, max_wait)
    while thread.is_alive() and time.monotonic() < deadline:
        thread.join(min(15.0, max(0.1, deadline - time.monotonic())))

    if thread.is_alive():
        return _abandon_watchdog_job(job_id, thread)

    _watchdog_strikes.pop(job_id, None)
    if "exc" in holder:
        return _handle_job_exception(job, holder["exc"])
    return holder.get("outcome") or {"job_id": job_id, "status": "done"}


def run_pending_jobs(
    max_jobs: int = 1, wall_seconds: float | None = None
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for job in db.claim_push_jobs(max_jobs):
        results.append(_run_job_with_watchdog(job, wall_seconds))
    return results


def _mark_recipient_push_running(
    recipient_email: str,
    owner: str,
    resource_name: str,
) -> None:
    """Mark push as running without clobbering in-progress photo import status."""
    data = db.get_app_data(recipient_email) or {}
    groups = list(data.get("sharedGroups") or [])
    changed = False
    for group in groups:
        if group.get("owner") == owner and group.get("resourceName") == resource_name:
            status = str(group.get("status") or "")
            import_phase = str(group.get("importPhase") or "")
            group["pushStatus"] = "Running"
            if import_phase == "photos" or status.startswith(
                ("Photos (", "Importing photos", "Importing (", "Queued")
            ):
                changed = True
                break
            if not status.startswith(("Pushing", "Importing")):
                group["status"] = "Pushing"
            changed = True
            break
    if changed:
        data["sharedGroups"] = groups
        db.save_app_data(recipient_email, data)


def _set_recipient_push_status(
    recipient_email: str,
    owner: str,
    resource_name: str,
    status: str,
) -> None:
    data = db.get_app_data(recipient_email) or {}
    groups = list(data.get("sharedGroups") or [])
    changed = False
    for group in groups:
        if group.get("owner") == owner and group.get("resourceName") == resource_name:
            group["status"] = status
            group["pushStatus"] = status
            changed = True
            break
    if changed:
        data["sharedGroups"] = groups
        db.save_app_data(recipient_email, data)


def _finalize_group_if_photos_done(
    recipient_email: str, owner: str, resource_name: str
) -> bool:
    """Mark Shared only when leftover photos are actually finished."""
    from .recipient_import import _contacts_are_imported, apply_missing_photo_backfill

    data = db.get_app_data(recipient_email) or {}
    groups = list(data.get("sharedGroups") or [])
    changed = False
    complete = False
    for group in groups:
        if group.get("owner") == owner and group.get("resourceName") == resource_name:
            if recipient_finished_import_is_stale_photo_row(group, recipient_email):
                group["status"] = "Shared"
                group["importPhase"] = "done"
                group["pushStatus"] = "Shared"
                changed = True
                complete = True
                break
            if apply_missing_photo_backfill(group):
                changed = True
                break
            status = str(group.get("status") or "")
            if not _contacts_are_imported(group):
                break
            if not status.startswith("Shared"):
                skipped = len(group.get("skippedIndexes") or [])
                group["status"] = (
                    f"Shared ({skipped} skipped)" if skipped else "Shared"
                )
                group["importPhase"] = "done"
                group["pushStatus"] = group["status"]
                group["photoPendingIndexes"] = []
                group["photoRetryIndexes"] = []
                group.pop("photoTargets", None)
                changed = True
            complete = True
            break
    if changed:
        data["sharedGroups"] = groups
        db.save_app_data(recipient_email, data)
    return complete


def _group_is_complete(recipient_email: str, owner: str, resource_name: str) -> bool:
    _finalize_group_if_photos_done(recipient_email, owner, resource_name)
    data = db.get_app_data(recipient_email) or {}
    for group in data.get("sharedGroups") or []:
        if group.get("owner") == owner and group.get("resourceName") == resource_name:
            status = str(group.get("status") or "")
            import_phase = str(group.get("importPhase") or "")
            if status.startswith("Error"):
                return True
            if _is_idle_photo_scan(group):
                return True
            if recipient_finished_import_is_stale_photo_row(group, recipient_email):
                return True
            if import_phase == "photos" or status.startswith(("Photos (", "Importing photos")):
                return False
            if status.startswith("Shared"):
                # Duplicates / deleted mappings / a short group still need a pass.
                return not _group_needs_import_job(group, recipient_email)
            return False
    return False


def _group_is_pending(recipient_email: str, owner: str, resource_name: str) -> bool:
    data = db.get_app_data(recipient_email) or {}
    for group in data.get("sharedGroups") or []:
        if group.get("owner") == owner and group.get("resourceName") == resource_name:
            status = str(group.get("status") or "")
            import_phase = str(group.get("importPhase") or "")
            if status.startswith("Error"):
                return False
            if _is_idle_photo_scan(group):
                return False
            if recipient_finished_import_is_stale_photo_row(group, recipient_email):
                return False
            if import_phase == "photos" or status.startswith(("Photos (", "Importing photos")):
                return True
            if status.startswith("Shared"):
                if _group_needs_import_job(group, recipient_email):
                    return True
                from .recipient_import import group_needs_photo_backfill

                return group_needs_photo_backfill(group)
            if status.startswith("Waiting for owner sync"):
                return False
            return True
    return False


def _sync_recipient_push_status_from_group(
    recipient_email: str, owner: str, resource_name: str
) -> None:
    data = db.get_app_data(recipient_email) or {}
    groups = list(data.get("sharedGroups") or [])
    changed = False
    for group in groups:
        if group.get("owner") == owner and group.get("resourceName") == resource_name:
            status = str(group.get("status") or "")
            if status:
                group["pushStatus"] = status
                changed = True
            break
    if changed:
        data["sharedGroups"] = groups
        db.save_app_data(recipient_email, data)


def _refresh_staged_then_backfill_photos(
    recipient_email: str, owner: str, resource_name: str
) -> None:
    """Refresh owner snapshot then reopen leftover photos after Shared/Queued.

    Never refresh or recount during an in-progress photo pass — rewriting
    staged members mid-import scrambled indexes and made the counter jump
    backwards with no Retry click.
    """
    from .app_data import refresh_staged_shared_contacts
    from .recipient_import import _contacts_are_imported, apply_missing_photo_backfill

    data = db.get_app_data(recipient_email) or {}
    groups = list(data.get("sharedGroups") or [])
    changed = False
    for group in groups:
        if group.get("owner") != owner or group.get("resourceName") != resource_name:
            continue
        status = str(group.get("status") or "")
        import_phase = str(group.get("importPhase") or "")
        if not _contacts_are_imported(group):
            break
        if import_phase == "photos" or status.startswith(
            ("Photos", "Importing photos")
        ):
            pending = list(group.get("photoPendingIndexes") or [])
            retry = list(group.get("photoRetryIndexes") or [])
            done = int(group.get("photoDoneCount") or 0)
            target = int(group.get("photoTargetCount") or 0)
            queued = len(pending) + len(retry)
            needs_scan = bool(group.get("photoNeedsRecipientScan"))
            full_reupload = target > 0 and queued >= target and done > 0
            if needs_scan or not (pending or retry) or full_reupload:
                if apply_missing_photo_backfill(
                    group,
                    force=True,
                    creds=recipient_credentials(recipient_email),
                ):
                    changed = True
            break
        if not status.startswith(("Shared", "Queued")):
            break
        refresh_staged_shared_contacts(
            owner,
            resource_name,
            str(group.get("shareId") or ""),
            group_name=str(group.get("name") or ""),
        )
        if apply_missing_photo_backfill(
            group,
            force=True,
            creds=recipient_credentials(recipient_email),
        ):
            changed = True
        break
    if changed:
        data["sharedGroups"] = groups
        db.save_app_data(recipient_email, data)


def _run_full_group_job(
    job: dict[str, Any], wall_seconds_limit: float | None = None
) -> dict[str, Any]:
    owner = job["owner"]
    resource_name = job["resource_name"]
    recipient_email = job["recipient_email"]
    job_id = int(job["id"])

    data = db.get_app_data(recipient_email) or {}
    matched = None
    for group in data.get("sharedGroups") or []:
        if group.get("owner") == owner and group.get("resourceName") == resource_name:
            matched = group
            break
    gone_hint = matched or {"owner": owner, "resourceName": resource_name}
    if _jobs_say_owner_group_gone(gone_hint, recipient_email):
        from .app_data import retire_vanished_owner_share

        retire_vanished_owner_share(recipient_email, owner, resource_name)
        db.update_push_job(
            job_id,
            status="error",
            last_error="owner group no longer exists",
        )
        return {"job_id": job_id, "status": "error", "reason": "owner_group_gone"}
    if matched is not None and not _group_needs_import_job(matched, recipient_email):
        _sync_recipient_push_status_from_group(
            recipient_email, owner, resource_name
        )
        db.update_push_job(job_id, status="done", last_error="")
        return {
            "job_id": job_id,
            "status": "done",
            "reason": "already_complete",
        }

    if not recipient_credentials(recipient_email):
        db.update_push_job(job_id, status="error", last_error="Recipient not connected")
        _set_recipient_push_status(
            recipient_email, owner, resource_name, "Waiting for recipient to connect"
        )
        return {"job_id": job_id, "status": "error", "reason": "not_connected"}

    _mark_recipient_push_running(recipient_email, owner, resource_name)

    maybe_refresh_staged_shared_contacts_before_import(
        recipient_email=recipient_email,
        owner_email=owner,
        resource_name=resource_name,
    )
    _refresh_staged_then_backfill_photos(recipient_email, owner, resource_name)

    started_at = time.time()
    wall_seconds = _current_job_wall_seconds(wall_seconds_limit)
    for _ in range(MAX_FULL_GROUP_PASSES):
        if time.time() - started_at >= wall_seconds:
            db.update_push_job(
                job_id,
                requeue=True,
                last_error="yielding for fair queue",
                reset_attempts=True,
            )
            return {"job_id": job_id, "status": "queued", "reason": "wall_clock"}

        db.update_push_job(job_id, touch=True)

        if _group_is_complete(recipient_email, owner, resource_name):
            _sync_recipient_push_status_from_group(recipient_email, owner, resource_name)
            db.update_push_job(job_id, status="done", last_error="")
            return {"job_id": job_id, "status": "done"}

        remaining_wall = wall_seconds - (time.time() - started_at)
        result = process_shared_import_for_group(
            recipient_email,
            owner,
            resource_name,
            max_ops=200,
            time_budget_ms=int(max(1.0, remaining_wall) * 1000),
        )
        if result.get("rate_limited"):
            db.update_push_job(
                job_id,
                requeue=True,
                last_error="Google rate limit, waiting",
                reset_attempts=True,
            )
            time.sleep(60)
            return {"job_id": job_id, "status": "queued", "reason": "rate_limit"}
        if result.get("busy"):
            time.sleep(0.5)
            continue

        if not _group_is_pending(recipient_email, owner, resource_name):
            _sync_recipient_push_status_from_group(recipient_email, owner, resource_name)
            db.update_push_job(job_id, status="done", last_error="")
            return {"job_id": job_id, "status": "done"}

        time.sleep(0.3)

    db.update_push_job(job_id, requeue=True, last_error="", reset_attempts=True)
    return {"job_id": job_id, "status": "queued", "reason": "in_progress"}


def _run_contact_batch_job(job: dict[str, Any]) -> dict[str, Any]:
    owner = job["owner"]
    job_id = int(job["id"])
    person_ids = list((job.get("payload") or {}).get("owner_person_ids") or [])
    if not person_ids:
        db.update_push_job(job_id, status="done", last_error="")
        return {"job_id": job_id, "status": "done", "updated": 0}

    summary = sync_contact_changes(owner, person_ids)
    pending = int(summary.get("pending") or 0)
    if pending > 0 and int(job.get("attempts") or 0) < MAX_CONTACT_BATCH_PASSES:
        db.update_push_job(job_id, requeue=True, last_error="")
        return {"job_id": job_id, "status": "queued", "summary": summary}

    if int(summary.get("errors") or 0) > 0 and not summary.get("updated"):
        db.update_push_job(
            job_id,
            status="error",
            last_error=json.dumps(summary.get("error_details") or [])[:500],
        )
    else:
        db.update_push_job(job_id, status="done", last_error="")
    return {"job_id": job_id, "status": "done", "summary": summary}
