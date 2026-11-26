"""One-button repair for the Share roster banner.

Runs in a background thread for whichever Editor holds the Editor lock. Order:

1. Pull the latest from Google Drive.
2. Re-run the quick roster check so every step works from fresh ids.
3. Fix Google Drive (remove deleted contacts, add missing ones).
4. Re-create CFS contacts that are missing from the owner's Google account.
5. Tell Share about the known missing and extra contact ids (one targeted call).
6. Do not download a recipient's whole shared group. That path is too slow when
   the banner already named the contacts.
7. Re-check the id lists and write one Edit List entry.

Owner-Google contacts that CFS does not have are never changed here. The banner
asks the Editor to Import or Delete each one (see import_google_extras and
delete_google_extras).
"""

from __future__ import annotations

import logging
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode

from app.database import get_connection
from app.services.google_sync_service import (
    GOOGLE_IMPORT_PERSON_FIELDS,
    GOOGLE_PEOPLE_BASE_URL,
    _api_json_request,
    _contact_groups_map,
    _find_local_contact_id_by_identity,
    _get_valid_access_token,
    _google_person_import_fields,
    _insert_contact_from_google_person,
    _queue_unuploaded_local_contacts,
    _schedule_share_app_contact_sync,
    _update_contact_from_google_person,
    export_shared_contacts_to_google_drive,
    export_shared_data_after_edit,
    get_current_editor_initials,
    get_google_sync_summary,
    get_share_web_api_key,
    is_editing_allowed,
    mark_contact_for_drive_export,
    mark_contacts_manifest_for_drive_export,
    sync_shared_data_from_google_drive,
    upload_google_contacts,
)
from app.services.roster_check_service import _load_saved_report, _save_report, refresh_share_roster_check
from app.services.share_sync_service import fix_share_recipient_cards, sync_share_app_contact_changes

_LOGGER = logging.getLogger(__name__)

MAX_FIX_ROUNDS = 8
MAX_BUSY_WAITS = 20
BUSY_WAIT_SECONDS = 30.0
PARTIAL_WAIT_SECONDS = 20.0
IMPORT_CHUNK = 50

_LOCK = threading.Lock()
_STATE: dict = {
    "running": False,
    "step": "",
    "log": [],
    "ok": None,
    "message": "",
    "started_at": 0.0,
    "finished_at": 0.0,
}


PROGRESS_TICK_SECONDS = 5.0


def get_roster_fix_status() -> dict:
    with _LOCK:
        snapshot = dict(_STATE)
        snapshot["log"] = list(_STATE["log"])
    started = float(snapshot.get("started_at") or 0)
    finished = float(snapshot.get("finished_at") or 0)
    if started:
        end = finished or time.time()
        snapshot["elapsed_seconds"] = max(0, int(end - started))
    else:
        snapshot["elapsed_seconds"] = 0
    return snapshot


def _set_state(**changes) -> None:
    with _LOCK:
        _STATE.update(changes)


def _note(text: str) -> None:
    with _LOCK:
        _STATE["log"].append(text)
        del _STATE["log"][:-60]
    _LOGGER.info("Roster fix: %s", text)


def _step(text: str) -> None:
    _set_state(step=text)
    _note(text)


def _progress(text: str) -> None:
    """Update the live banner without filling the log."""
    _set_state(step=text)


def _format_tally(totals: dict | None) -> str:
    totals = totals or {}
    parts: list[str] = []
    relinked = int(totals.get("relinked") or 0) + int(totals.get("linked_elsewhere") or 0)
    if relinked:
        parts.append(f"relinked {relinked}")
    if totals.get("created"):
        parts.append(f"created {totals['created']}")
    if totals.get("repaired"):
        parts.append(f"corrected {totals['repaired']}")
    if totals.get("added_back"):
        parts.append(f"added {totals['added_back']} back")
    if totals.get("deleted"):
        parts.append(f"deleted {totals['deleted']} duplicate(s)")
    if totals.get("ungrouped"):
        parts.append(f"removed {totals['ungrouped']} extra")
    return ", ".join(parts)


def _recipient_progress(email: str, index: int, total: int, detail: str, totals: dict | None = None) -> None:
    pieces = [f"Fixing cards for {email} ({index} of {total})"]
    if detail:
        pieces.append(detail)
    tally = _format_tally(totals)
    if tally:
        pieces.append(tally)
    _progress(" · ".join(pieces))


def _sleep(seconds: float, on_tick=None) -> None:
    if on_tick is None or seconds <= PROGRESS_TICK_SECONDS:
        time.sleep(seconds)
        return
    deadline = time.time() + seconds
    remaining = seconds
    while remaining > 0.05:
        on_tick(int(max(remaining, 0)))
        time.sleep(min(PROGRESS_TICK_SECONDS, remaining))
        remaining = deadline - time.time()


def _log_to_edit_list(title: str, lines: list[str]) -> None:
    """One Edit List entry per fix, signed with the current Editor's initials."""
    cleaned = [line for line in lines if str(line).strip()]
    if not cleaned:
        return
    try:
        from app.services.changes_list_service import (
            add_changes_entry,
            complete_changes_entry,
            update_changes_entry,
        )

        initials = get_current_editor_initials()
        if not initials:
            return
        entry = add_changes_entry()
        entry_id = int(entry.get("id") or 0)
        if entry_id:
            update_changes_entry(entry_id, name_event=title, changes_text="\n".join(cleaned), initials=initials)
            # These rows only record what the fix already did, so they go straight to
            # Completed edit(s) (green) instead of waiting as Pending Edits.
            complete_changes_entry(entry_id, initials=initials)
    except Exception:  # noqa: BLE001 - logging must never break the fix
        _LOGGER.exception("Could not write roster fix to the Edit List")


def preflight() -> dict:
    """Why the fix cannot start right now, or {"ok": True}."""
    if not is_editing_allowed():
        return {
            "ok": False,
            "reason": "not_editor",
            "message": "Only the Editor who holds the Editor lock can run this fix.",
        }
    summary = get_google_sync_summary()
    account = summary.get("account") or {}
    state = summary.get("state") or {}
    if str(account.get("status") or "") != "connected" or not account.get("drive_scope_ready"):
        return {"ok": False, "reason": "google_not_connected", "message": "Connect Google first."}
    if not get_share_web_api_key():
        return {
            "ok": False,
            "reason": "share_key_missing",
            "message": "The Share connection key is not set. Ask the owner to open App Settings once.",
        }
    if bool(state.get("upload_in_progress") or 0) or bool(state.get("signin_sync_in_progress") or 0):
        return {
            "ok": False,
            "reason": "busy",
            "message": "An Upload or sign-in sync is still running. Try again when it finishes.",
        }
    return {"ok": True}


def start_roster_fix() -> dict:
    check = preflight()
    if not check["ok"]:
        return check
    with _LOCK:
        if _STATE["running"]:
            return {"ok": False, "reason": "already_running", "message": "The fix is already running."}
        _STATE.update(
            running=True,
            step="Starting",
            log=[],
            ok=None,
            message="",
            started_at=time.time(),
            finished_at=0.0,
        )
    threading.Thread(target=_run_fix, name="roster-fix", daemon=True).start()
    return {"ok": True, "message": "The fix has started."}


# --- steps ---------------------------------------------------------------------------


def _fix_drive(report: dict) -> list[str]:
    missing = [int(i) for i in report.get("drive_missing_ids") or [] if int(i) > 0]
    extra = [int(i) for i in report.get("drive_extra_ids") or [] if int(i) > 0]
    if not missing and not extra:
        return []
    _step("Fixing Google Drive")
    for contact_id in missing:
        mark_contact_for_drive_export(contact_id)
    mark_contacts_manifest_for_drive_export()
    export_shared_contacts_to_google_drive()
    lines = []
    if extra:
        lines.append(f"Google Drive: removed {len(extra)} contact(s) no longer in CFS")
    if missing:
        lines.append(f"Google Drive: added {len(missing)} contact(s) that were missing")
    return lines


def _fix_google_missing(report: dict) -> list[str]:
    ids = [int(i) for i in report.get("google_missing_ids") or [] if int(i) > 0]
    if not ids:
        return []
    with get_connection() as conn:
        unuploaded = int(
            conn.execute("SELECT COUNT(*) AS n FROM contacts WHERE COALESCE(google_contact_id, '') = ''").fetchone()["n"]
        )
        pending = int(
            conn.execute("SELECT COUNT(*) AS n FROM google_sync_queue WHERE status = 'pending'").fetchone()["n"]
        )
    if unuploaded or pending:
        _note("Skipped re-adding contacts to Google: other changes are waiting for Upload. Upload first, then run the fix again.")
        return []
    _step("Adding missing contacts back to the owner's Google account")
    placeholders = ",".join("?" for _ in ids)
    with get_connection() as conn:
        conn.execute(
            f"UPDATE contacts SET google_contact_id = '', etag = '' WHERE id IN ({placeholders})",
            tuple(ids),
        )
        _queue_unuploaded_local_contacts(conn)
        conn.commit()
    created = upload_google_contacts(finish_progress=True)
    return [f"Owner Google: re-added {int(created or len(ids))} contact(s) that were missing"]


def _missing_card_labels(report: dict) -> list[str]:
    labels: list[str] = []
    cards = ((report.get("share_cards") or {}).get("recipients") or {}) if isinstance(report.get("share_cards"), dict) else {}
    for card in cards.values():
        if not isinstance(card, dict):
            continue
        for label in (card.get("names") or {}).get("missing") or []:
            text = str(label or "").strip()
            if text:
                labels.append(text)
    return list(dict.fromkeys(labels))


def _drop_missing_card_names_from_saved_report(labels: list[str]) -> None:
    """Stop showing a stale 'missing card' after Share was asked to recreate it."""
    wanted = {str(item).strip() for item in labels if str(item).strip()}
    if not wanted:
        return
    saved = _load_saved_report()
    cards = ((saved.get("share_cards") or {}).get("recipients") or {})
    if not isinstance(cards, dict):
        return
    changed = False
    for card in cards.values():
        if not isinstance(card, dict):
            continue
        names = card.setdefault("names", {})
        missing = [item for item in (names.get("missing") or []) if str(item).strip() not in wanted]
        if missing != list(names.get("missing") or []):
            names["missing"] = missing
            counts = card.setdefault("counts", {})
            counts["missing"] = len(missing)
            card["changes"] = sum(int(counts.get(key) or 0) for key in counts)
            card["clean"] = not card["changes"] and bool(card.get("ok", True))
            changed = True
    if changed:
        _save_report(saved)


def _google_ids_for_missing_card_names(report: dict) -> list[str]:
    """Owner Google ids for names a prior card check said need a real recipient card."""
    labels = _missing_card_labels(report)
    if not labels:
        return []
    ids: list[str] = []
    with get_connection() as conn:
        for label in dict.fromkeys(labels):
            family, given = (label.split(", ", 1) + [""])[:2] if ", " in label else (label, "")
            row = conn.execute(
                """
                SELECT google_contact_id FROM contacts
                WHERE family_name = ? AND given_name = ?
                  AND COALESCE(google_contact_id, '') LIKE 'people/%'
                LIMIT 1
                """,
                (family.strip(), given.strip()),
            ).fetchone()
            resource = str(row["google_contact_id"] if row else "")
            if resource.startswith("people/"):
                ids.append(resource)
    return ids


def _fix_share_mappings(report: dict, owner_email: str) -> list[str]:
    ids: set[str] = set()
    for item in report.get("recipients") or []:
        ids.update(item.get("missing_person_ids") or [])
        ids.update(item.get("extra_person_ids") or [])
    named_missing = _missing_card_labels(report)
    ids.update(_google_ids_for_missing_card_names(report))
    if not ids:
        return []
    _step(f"Updating Share for {len(ids)} known contact(s)")
    result = sync_share_app_contact_changes(owner_email, sorted(ids))
    if result is not None and result.get("ok") is False:
        _note(f"Share did not accept the update: {result.get('reason') or 'unknown reason'}")
        return []
    _drop_missing_card_names_from_saved_report(named_missing)
    return [f"Share: asked to update {len(ids)} contact(s) that were missing or out of date"]


def _recipient_targets(report: dict) -> list[str]:
    """Recipients whose real Google cards already showed drift.

    Known missing/extra mapping ids are sent in `_fix_share_mappings`. A mapping
    mismatch is not a reason to download that recipient's whole shared group.
    """
    cards = ((report.get("share_cards") or {}).get("recipients") or {}) if isinstance(report.get("share_cards"), dict) else {}
    targets: list[str] = []
    for item in report.get("recipients") or []:
        email = item["email"]
        card = cards.get(email)
        if isinstance(card, dict) and (card.get("changes") or not card.get("ok")):
            targets.append(email)
    return targets


def _sum_applied(result: dict) -> dict:
    totals: dict[str, int] = {}
    for group in result.get("groups") or []:
        for key, value in (group.get("applied") or {}).items():
            if isinstance(value, int) and not isinstance(value, bool) and value:
                totals[key] = totals.get(key, 0) + value
    return totals


def _fix_recipient(owner_email: str, email: str, index: int = 1, total: int = 1) -> tuple[dict, str]:
    """Run Share's card fix for one recipient until it is clean. Returns (totals, problem)."""
    totals: dict[str, int] = {}
    busy_waits = 0
    rounds = 0
    while rounds < MAX_FIX_ROUNDS:
        _recipient_progress(email, index, total, f"round {rounds + 1}", totals)
        result = fix_share_recipient_cards(owner_email, email)
        reason = str(result.get("reason") or "")
        if reason == "push_queue_busy":
            busy_waits += 1
            if busy_waits > MAX_BUSY_WAITS:
                return totals, "Share is still sending other updates"
            _recipient_progress(email, index, total, "waiting for Share to finish sending", totals)

            def tick(left: int) -> None:
                _recipient_progress(
                    email,
                    index,
                    total,
                    f"waiting for Share to finish sending ({left}s)",
                    totals,
                )

            _sleep(BUSY_WAIT_SECONDS, on_tick=tick)
            continue
        rounds += 1
        applied_now = _sum_applied(result)
        for key, value in applied_now.items():
            totals[key] = totals.get(key, 0) + value
        if applied_now:
            _note(f"{email}: round {rounds} {_format_tally(applied_now)}")
        if result.get("clean"):
            _recipient_progress(email, index, total, "done", totals)
            return totals, ""
        if reason in ("recipient_not_connected", "owner_not_connected", "share_key_not_configured", "share_app_endpoint_missing_redeploy_required"):
            return totals, str(result.get("message") or reason)
        refused = [g for g in result.get("groups") or [] if g.get("ok") is False and g.get("reason")]
        if refused and not result.get("partial"):
            first = refused[0]
            return totals, str(first.get("message") or first.get("reason"))
        if result.get("ok") is False and not result.get("groups") and not result.get("partial"):
            return totals, str(result.get("reason") or "Share could not be reached")
        wait_label = "waiting for remaining cards" if result.get("partial") else "checking again"

        def tick(left: int, label: str = wait_label) -> None:
            _recipient_progress(email, index, total, f"{label} ({left}s)", totals)

        wait_seconds = PARTIAL_WAIT_SECONDS if result.get("partial") else 3.0
        if wait_seconds > PROGRESS_TICK_SECONDS:
            _sleep(wait_seconds, on_tick=tick)
        else:
            _recipient_progress(email, index, total, wait_label, totals)
            _sleep(wait_seconds)
    return totals, "Still not finished after several rounds"


def _fix_share_cards(report: dict, owner_email: str) -> list[str]:
    targets = _recipient_targets(report)
    lines: list[str] = []
    for index, email in enumerate(targets, start=1):
        _step(f"Fixing cards for {email} ({index} of {len(targets)})")
        totals, problem = _fix_recipient(owner_email, email, index=index, total=len(targets))
        parts = []
        if totals.get("relinked") or totals.get("linked_elsewhere"):
            parts.append(f"relinked {totals.get('relinked', 0) + totals.get('linked_elsewhere', 0)}")
        if totals.get("created"):
            parts.append(f"created {totals['created']}")
        if totals.get("repaired"):
            parts.append(f"corrected {totals['repaired']}")
        if totals.get("added_back"):
            parts.append(f"added {totals['added_back']} back to the group")
        if totals.get("deleted"):
            parts.append(f"deleted {totals['deleted']} duplicate(s)")
        if totals.get("ungrouped"):
            parts.append(f"removed {totals['ungrouped']} unrecognized card(s) from the group")
        if parts:
            lines.append(f"{email}: " + ", ".join(parts))
        if problem:
            note = f"{email}: not finished ({problem})"
            lines.append(note)
            _note(note)
    return lines


def _run_fix() -> None:
    lines: list[str] = []
    try:
        _, owner_email = _get_valid_access_token()
        _step("Getting the latest from Google Drive")
        try:
            sync_shared_data_from_google_drive()
        except Exception as exc:  # noqa: BLE001
            _note(f"Could not pull from Drive first ({str(exc)[:80]}); continuing with this copy")

        _step("Checking what needs fixing")
        report = refresh_share_roster_check(deep=False)

        lines += _fix_drive(report)
        lines += _fix_google_missing(report)
        if lines:
            _step("Re-checking after the Drive and Google changes")
            report = refresh_share_roster_check(deep=False)
        lines += _fix_share_mappings(report, owner_email)

        _step("Final check")
        final = refresh_share_roster_check(deep=False)
        remaining = [item for item in final.get("issues") or [] if item.get("kind") != "google_extra"]
        if remaining:
            _set_state(ok=False, message="The fix finished, but some problems remain. See the list above the button.")
        else:
            _set_state(ok=True, message="Everything matches now.")
        lines.append(
            "Result: everything matches" if not remaining else f"Result: {len(remaining)} problem(s) still listed"
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.exception("Roster fix failed")
        _set_state(ok=False, message=f"The fix stopped: {str(exc)[:160]}")
        lines.append(f"Fix stopped early: {str(exc)[:120]}")
    finally:
        _log_to_edit_list("Share roster fix", lines)
        _set_state(running=False, step="", finished_at=time.time())


# --- owner Google contacts CFS does not have -------------------------------------------


def _allowed_google_extras(person_ids: list[str]) -> dict[str, str]:
    """Only ids the last roster check listed as extra may be acted on."""
    listed = {
        str(item.get("id")): str(item.get("name") or item.get("id"))
        for item in (_load_saved_report().get("google_extra_people") or [])
        if isinstance(item, dict)
    }
    return {pid: listed[pid] for pid in dict.fromkeys(str(p).strip() for p in person_ids) if pid in listed}


def _refresh_banner_later() -> None:
    def _run() -> None:
        try:
            refresh_share_roster_check(deep=False)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Roster refresh after Google extra failed")

    threading.Thread(target=_run, daemon=True).start()


def _fetch_google_people(access_token: str, person_ids: list[str]) -> list[dict]:
    people: list[dict] = []
    for start in range(0, len(person_ids), IMPORT_CHUNK):
        chunk = person_ids[start : start + IMPORT_CHUNK]
        params = [("resourceNames", pid) for pid in chunk] + [("personFields", GOOGLE_IMPORT_PERSON_FIELDS)]
        payload = _api_json_request(
            f"{GOOGLE_PEOPLE_BASE_URL}/people:batchGet?{urlencode(params)}", access_token
        )
        for response in payload.get("responses") or []:
            person = response.get("person") if isinstance(response, dict) else None
            if isinstance(person, dict) and person.get("resourceName"):
                people.append(person)
    return people


def import_google_extras(person_ids: list[str]) -> dict:
    """Copy owner-Google contacts into CFS, then tell Drive and Share."""
    if not is_editing_allowed():
        return {"ok": False, "reason": "not_editor", "message": "Only the Editor who holds the Editor lock can do this."}
    allowed = _allowed_google_extras(person_ids)
    if not allowed:
        return {"ok": False, "reason": "not_listed", "message": "That contact is no longer listed as an extra."}
    access_token, owner_email = _get_valid_access_token()
    groups_map = _contact_groups_map(access_token)
    people = _fetch_google_people(access_token, list(allowed))
    imported: list[int] = []
    names: list[str] = []
    person_ids_done: list[str] = []
    with get_connection() as conn:
        for person in people:
            fields = _google_person_import_fields(person, groups_map)
            if not fields:
                continue
            existing = conn.execute(
                "SELECT id FROM contacts WHERE google_contact_id = ? LIMIT 1", (fields["resource_name"],)
            ).fetchone()
            contact_id = int(existing["id"]) if existing else 0
            if not contact_id:
                contact_id = int(
                    _find_local_contact_id_by_identity(
                        conn,
                        fields.get("given_name"),
                        fields.get("family_name"),
                        fields.get("phones") or [],
                        fields.get("emails") or [],
                    )
                    or 0
                )
            if contact_id:
                _update_contact_from_google_person(conn, contact_id, fields)
            else:
                contact_id = _insert_contact_from_google_person(conn, fields, status="synced")
            imported.append(contact_id)
            person_ids_done.append(fields["resource_name"])
            names.append(allowed.get(fields["resource_name"], fields["resource_name"]))
        conn.commit()
    for contact_id in imported:
        mark_contact_for_drive_export(contact_id)
    if imported:
        mark_contacts_manifest_for_drive_export()
        try:
            export_shared_data_after_edit()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Drive export after importing Google extras failed")
        _schedule_share_app_contact_sync(owner_email, person_ids_done)
        _log_to_edit_list("Imported from owner Google", [f"Imported into CFS: {name}" for name in names])
    _refresh_banner_later()
    return {"ok": True, "imported": len(imported), "names": names}


def delete_google_extras(person_ids: list[str]) -> dict:
    """Delete owner-Google contacts CFS does not have, then prune them on Share."""
    if not is_editing_allowed():
        return {"ok": False, "reason": "not_editor", "message": "Only the Editor who holds the Editor lock can do this."}
    allowed = _allowed_google_extras(person_ids)
    if not allowed:
        return {"ok": False, "reason": "not_listed", "message": "That contact is no longer listed as an extra."}
    access_token, owner_email = _get_valid_access_token()
    deleted: list[str] = []
    failed: list[str] = []
    for person_id, name in allowed.items():
        try:
            _api_json_request(f"{GOOGLE_PEOPLE_BASE_URL}/{person_id}:deleteContact", access_token, method="DELETE")
            deleted.append(person_id)
        except HTTPError as exc:
            if exc.code in (404, 410):
                deleted.append(person_id)
            else:
                failed.append(name)
        except Exception:  # noqa: BLE001
            failed.append(name)
    if deleted:
        _schedule_share_app_contact_sync(owner_email, deleted)
        _log_to_edit_list(
            "Deleted from owner Google",
            [f"Deleted from Google: {allowed[person_id]}" for person_id in deleted],
        )
    _refresh_banner_later()
    return {"ok": not failed, "deleted": len(deleted), "failed": failed}
