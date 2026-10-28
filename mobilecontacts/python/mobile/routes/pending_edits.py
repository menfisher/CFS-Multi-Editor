from __future__ import annotations

from urllib.error import HTTPError, URLError

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.database import get_connection
from app.services.changes_list_service import (
    add_changes_entry,
    enrich_changes_entries_for_display,
    list_changes_entries,
    pending_edits_has_drive_upload,
    update_changes_entry,
)
from app.services.google_sync_service import (
    _clear_pending_changes_list_drive_export,
    can_edit_changes_list,
    export_changes_list_to_google_drive,
    export_pending_edits_support_to_google_drive,
    get_current_changes_list_initials,
    get_google_sync_summary,
    get_notice_message,
    import_changes_list_from_google_drive,
)

from mobile.access_session import mobile_access_context, mobile_is_non_editor
from mobile.routes.contacts import clear_google_contacts_refresh_flag
from mobile.user_db import use_user_database
from mobile.web_templates import templates

router = APIRouter(prefix="/m/pending-edits", tags=["mobile-pending-edits"])


def _owner_email(request: Request) -> str | None:
    email = str(request.session.get("owner_email") or "").strip()
    return email or None


def _pending_edits_entries() -> list[dict]:
    return enrich_changes_entries_for_display(
        list_changes_entries(),
        contact_edit_url_for_id=lambda contact_id: f"/m/contacts/{int(contact_id)}/edit",
    )


def _needs_drive_upload() -> bool:
    return pending_edits_has_drive_upload()


@router.get("")
@router.get("/")
def pending_edits_page(request: Request):
    email = _owner_email(request)
    if not email:
        return RedirectResponse(url="/", status_code=303)
    use_user_database(email)
    if not mobile_is_non_editor(request):
        return RedirectResponse(url="/m/contacts/", status_code=303)
    clear_google_contacts_refresh_flag(request)
    return templates.TemplateResponse(
        request=request,
        name="pending_edits/list.html",
        context={
            "owner_email": email,
            "entries": _pending_edits_entries(),
            "can_edit_changes": can_edit_changes_list(),
            "needs_drive_upload": _needs_drive_upload(),
            **mobile_access_context(request),
        },
    )


@router.post("/upload")
def pending_edits_upload(request: Request):
    email = _owner_email(request)
    if not email:
        return JSONResponse({"ok": False, "error": "Not signed in."}, status_code=401)
    use_user_database(email)
    if not mobile_is_non_editor(request):
        return JSONResponse({"ok": False, "error": "Upload is only for Pending Edits sign-in."}, status_code=403)
    if not can_edit_changes_list():
        return JSONResponse({"ok": False, "error": "Sign in before uploading Pending Edits."}, status_code=403)

    summary = get_google_sync_summary()
    account = summary.get("account") or {}
    if str(account.get("status") or "") != "connected":
        return JSONResponse({"ok": False, "error": "Connect Google before uploading Pending Edits."}, status_code=400)

    try:
        export_pending_edits_support_to_google_drive()
        export_changes_list_to_google_drive(merge_remote=True)
        with get_connection() as conn:
            _clear_pending_changes_list_drive_export(conn)
            conn.commit()
    except (HTTPError, URLError, RuntimeError) as exc:
        return JSONResponse({"ok": False, "error": str(exc) or "Upload failed."}, status_code=502)

    return JSONResponse(
        {
            "ok": True,
            "message": "Pending edits and any new contact photos were uploaded to Google Drive for your Editor.",
            "needs_drive_upload": _needs_drive_upload(),
        }
    )


@router.post("/download-from-drive")
def pending_edits_download_from_drive(request: Request):
    email = _owner_email(request)
    if not email:
        return JSONResponse({"ok": False, "error": "Not signed in."}, status_code=401)
    use_user_database(email)
    if not mobile_is_non_editor(request):
        return JSONResponse({"ok": False, "error": "Download is only for Pending Edits sign-in."}, status_code=403)
    if not can_edit_changes_list():
        return JSONResponse(
            {"ok": False, "error": "Sign in before downloading Pending Edits from Google Drive."},
            status_code=403,
        )

    summary = get_google_sync_summary()
    account = summary.get("account") or {}
    state = summary.get("state") or {}
    if str(account.get("status") or "") != "connected" or not bool(account.get("drive_scope_ready")):
        return JSONResponse(
            {"ok": False, "error": get_notice_message("drive_not_connected")},
            status_code=403,
        )

    local_revision_before = str(state.get("changes_list_sync_revision") or "").strip()
    try:
        import_changes_list_from_google_drive()
    except (HTTPError, URLError, RuntimeError) as exc:
        return JSONResponse(
            {
                "ok": False,
                "error": str(exc).strip() or get_notice_message("changes_list_download_failed"),
            },
            status_code=502,
        )

    summary_after = get_google_sync_summary()
    local_revision_after = str((summary_after.get("state") or {}).get("changes_list_sync_revision") or "").strip()
    updated = local_revision_after != local_revision_before
    message = get_notice_message("changes_list_download_success" if updated else "changes_list_download_current")
    entries = enrich_changes_entries_for_display(
        list_changes_entries(),
        contact_edit_url_for_id=lambda contact_id: f"/m/contacts/{int(contact_id)}/edit",
    )
    return JSONResponse(
        {
            "ok": True,
            "updated": updated,
            "message": message,
            "entries": entries,
        }
    )


@router.post("/rows")
def pending_edits_add_row(request: Request):
    email = _owner_email(request)
    if not email:
        return JSONResponse({"ok": False, "error": "Not signed in."}, status_code=401)
    use_user_database(email)
    if not can_edit_changes_list():
        return JSONResponse({"ok": False, "error": "Sign in before editing Pending Edits."}, status_code=403)
    entry = add_changes_entry()
    return JSONResponse({"ok": True, "entry": entry})


@router.post("/rows/{entry_id}/save")
def pending_edits_save_row(
    request: Request,
    entry_id: int,
    name_event: str = Form(default=""),
    changes_text: str = Form(default=""),
):
    email = _owner_email(request)
    if not email:
        return JSONResponse({"ok": False, "error": "Not signed in."}, status_code=401)
    use_user_database(email)
    initials = get_current_changes_list_initials()
    if not initials:
        return JSONResponse({"ok": False, "error": "Sign in before editing Pending Edits."}, status_code=403)
    entry = update_changes_entry(
        entry_id,
        name_event=name_event,
        changes_text=changes_text,
        initials=initials,
    )
    return JSONResponse({"ok": bool(entry), "entry": entry, "needs_drive_upload": _needs_drive_upload()})
