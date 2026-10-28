import os
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.services.app_revision_service import (
    APP_REVISION_DISPLAY_LIMIT,
    load_app_revisions_for_page,
    save_app_revisions,
    sync_app_revisions_from_google_drive,
)
from app.services.app_update_service import (
    app_update_failure_help_text,
    app_update_install_progress_text,
    download_google_drive_update_package,
    get_app_support_paths,
    get_app_update_status,
    get_update_package,
    stage_app_update_install,
)
from app.services.changes_list_service import (
    add_changes_entry,
    complete_changes_entry,
    dedupe_local_changes_list_entries,
    enrich_changes_entries_for_display,
    enrich_changes_entry_for_display,
    list_changes_entries,
    update_changes_entry,
)
from app.services.contact_service import build_contact_id_lookup_by_formatted_name
from app.services.contact_service import get_contact_row_colors
from app.services.google_sync_service import (
    can_complete_changes_list,
    can_edit_changes_list,
    get_current_changes_list_initials,
    get_current_changes_completion_initials,
    get_google_sync_summary,
    get_notice_message,
    import_changes_list_from_google_drive,
    schedule_changes_list_drive_export,
)
from app.web_templates import templates


router = APIRouter()


def _enrich_changes_entry(entry: dict | None, *, compact: bool = True) -> dict:
    if not entry:
        return {}
    return enrich_changes_entry_for_display(
        entry,
        compact=compact,
        contact_id_lookup=build_contact_id_lookup_by_formatted_name(),
    )


@router.get("/")
def home():
    return RedirectResponse(url="/presets/google-connect", status_code=302)


@router.get("/changes-list", response_class=HTMLResponse)
def changes_list(request: Request):
    # Heal CR/LF completed vs incomplete duplicates left by older Drive downloads.
    dedupe_local_changes_list_entries()
    summary = get_google_sync_summary()
    account = summary.get("account") or {}
    drive_ready = str(account.get("status") or "") == "connected" and bool(account.get("drive_scope_ready"))
    return templates.TemplateResponse(
        request=request,
        name="changes_list.html",
        context={
            "row_colors": get_contact_row_colors(),
            "entries": enrich_changes_entries_for_display(list_changes_entries(), compact=True),
            "google_sync": summary,
            "can_edit_changes": can_edit_changes_list(),
            "can_complete_changes": can_complete_changes_list(),
            "drive_ready": drive_ready,
            "notice_message": get_notice_message(request.query_params.get("notice", "")),
        },
    )


@router.post("/changes-list/rows")
def changes_list_add_row():
    if not can_edit_changes_list():
        return JSONResponse(
            {"ok": False, "error": "Sign in before editing the Changes List."},
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )
    entry = add_changes_entry()
    return JSONResponse({"ok": True, "entry": entry}, headers={"Cache-Control": "no-store"})


@router.post("/changes-list/rows/{entry_id}/save")
def changes_list_save_row(
    entry_id: int,
    name_event: str = Form(default=""),
    changes_text: str = Form(default=""),
):
    initials = get_current_changes_list_initials()
    if not initials:
        return JSONResponse(
            {"ok": False, "error": "Sign in before editing the Changes List."},
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )
    entry = update_changes_entry(
        entry_id,
        name_event=name_event,
        changes_text=changes_text,
        initials=initials,
    )
    enriched = _enrich_changes_entry(entry)
    return JSONResponse({"ok": bool(entry), "entry": enriched}, headers={"Cache-Control": "no-store"})


@router.post("/changes-list/rows/{entry_id}/complete")
def changes_list_complete_row(entry_id: int):
    initials = get_current_changes_completion_initials()
    if not initials:
        return JSONResponse(
            {"ok": False, "error": "Sign in as an Editor before completing a Changes List item."},
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )
    entry = complete_changes_entry(entry_id, initials=initials)
    if entry:
        # Push to Drive in the background so the cell turns green immediately.
        # A synchronous Drive merge used to stall the UI for seconds and rewrite
        # local row ids, which broke Completed Edit(s) on the next selected cell.
        schedule_changes_list_drive_export(merge_remote=True)
    enriched = _enrich_changes_entry(entry)
    return JSONResponse(
        {
            "ok": bool(entry),
            "entry": enriched,
            "drive_upload_scheduled": bool(entry),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/changes-list/download-from-drive")
def changes_list_download_from_drive():
    summary = get_google_sync_summary()
    account = summary.get("account") or {}
    state = summary.get("state") or {}
    if str(account.get("status") or "") != "connected" or not bool(account.get("drive_scope_ready")):
        return JSONResponse(
            {"ok": False, "error": get_notice_message("drive_not_connected")},
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )
    if not can_edit_changes_list():
        return JSONResponse(
            {"ok": False, "error": "Sign in before downloading Pending Edits from Google Drive."},
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )
    local_revision_before = str(state.get("changes_list_sync_revision") or "").strip()
    try:
        import_changes_list_from_google_drive()
    except (RuntimeError, HTTPError, URLError) as exc:
        return JSONResponse(
            {
                "ok": False,
                "error": str(exc).strip() or get_notice_message("changes_list_download_failed"),
            },
            status_code=500,
            headers={"Cache-Control": "no-store"},
        )
    summary_after = get_google_sync_summary()
    local_revision_after = str((summary_after.get("state") or {}).get("changes_list_sync_revision") or "").strip()
    updated = local_revision_after != local_revision_before
    message = get_notice_message("changes_list_download_success" if updated else "changes_list_download_current")
    entries = enrich_changes_entries_for_display(list_changes_entries(), compact=True)
    return JSONResponse(
        {
            "ok": True,
            "updated": updated,
            "message": message,
            "entries": entries,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/app-revisions", response_class=HTMLResponse)
def app_revisions(
    request: Request,
    notice: str = Query(default=""),
    update_checked: int = Query(default=0),
):
    try:
        google_summary = get_google_sync_summary()
        account = google_summary.get("account") or {}
        sync_drive = (
            str(account.get("status") or "") == "connected"
            and bool(account.get("drive_scope_ready"))
        )
        all_revisions = load_app_revisions_for_page(sync_drive=sync_drive)
    except (RuntimeError, HTTPError, URLError):
        all_revisions = load_app_revisions_for_page(sync_drive=False)
    notice_key = str(notice or "").strip()
    update_error = str(request.query_params.get("update_error") or "")
    notice_message = get_notice_message(notice_key)
    if notice_key == "app_update_install_failed":
        notice_message = app_update_failure_help_text(update_error)
    return templates.TemplateResponse(
        request=request,
        name="app_revisions.html",
        context={
            "row_colors": get_contact_row_colors(),
            "revisions": all_revisions,
            "editor_revisions": all_revisions,
            "notice_message": notice_message,
            "notice_key": notice_key,
            "update_error": update_error if notice_key != "app_update_install_failed" else "",
            "support_paths": get_app_support_paths(),
            "update_status": get_app_update_status() if update_checked else None,
        },
    )


@router.post("/app-revisions/save")
async def app_revisions_save(request: Request):
    form = await request.form()
    date_values = form.getlist("new_date_display")
    version_values = form.getlist("new_version")
    description_values = form.getlist("new_description")
    revisions = []
    for index in range(max(len(date_values), len(version_values), len(description_values))):
        revisions.append(
            {
                "date_display": str(date_values[index] if index < len(date_values) else ""),
                "version": str(version_values[index] if index < len(version_values) else ""),
                "description": str(description_values[index] if index < len(description_values) else ""),
            }
        )
    save_app_revisions(revisions)
    return RedirectResponse(url="/app-revisions", status_code=303)


@router.get("/app-revisions/check-updates")
def app_revisions_check_updates(return_to: str = Query(default="/app-revisions")):
    safe_return_to = return_to if return_to.startswith("/") else "/app-revisions"
    try:
        google_summary = get_google_sync_summary()
        account = google_summary.get("account") or {}
        if str(account.get("status") or "") != "connected":
            notice = "drive_not_connected"
        elif not bool(account.get("drive_scope_ready")):
            notice = "drive_scope_missing"
        else:
            sync_app_revisions_from_google_drive()
            notice = "drive_import_success"
    except (RuntimeError, HTTPError, URLError):
        notice = "drive_import_failed"
    separator = "&" if "?" in safe_return_to else "?"
    return RedirectResponse(url=f"{safe_return_to}{separator}notice={notice}", status_code=303)


@router.get("/app-updates/check")
def app_updates_check():
    status = get_app_update_status()
    if not status.get("ok"):
        notice = "app_update_check_failed"
    elif status.get("update_available"):
        notice = "app_update_available"
    elif status.get("installed_newer_than_latest"):
        notice = "app_update_installed_newer"
    else:
        notice = "app_update_current"
    return RedirectResponse(url=f"/app-revisions?update_checked=1&notice={notice}", status_code=303)


@router.get("/app-updates/download/{platform_key}")
def app_updates_download(platform_key: str):
    try:
        package = get_update_package(platform_key)
        download_url = str(package.get("download_url") or "").strip()
        if download_url:
            return RedirectResponse(url=download_url, status_code=303)
        downloaded = download_google_drive_update_package(platform_key)
    except (RuntimeError, HTTPError, URLError, OSError):
        return RedirectResponse(url="/app-revisions?update_checked=1&notice=app_update_download_failed", status_code=303)

    filename = str(downloaded.get("filename") or "ContactsFreeShare-update.zip").replace('"', "")
    mime_type = str(downloaded.get("mime_type") or "application/octet-stream")
    content = downloaded.get("content") or b""
    return Response(
        content=content,
        media_type=mime_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/app-updates/install/{platform_key}", response_class=HTMLResponse)
def app_updates_install(request: Request, platform_key: str):
    try:
        staged = stage_app_update_install(platform_key)
    except (RuntimeError, HTTPError, URLError, OSError, ValueError) as exc:
        update_error = quote(str(exc)[:500])
        return RedirectResponse(
            url=f"/app-revisions?update_checked=1&notice=app_update_install_failed&update_error={update_error}",
            status_code=303,
        )

    def stop_current_server() -> None:
        time.sleep(2.0)
        os._exit(0)

    threading.Thread(target=stop_current_server, daemon=True).start()
    package = staged.get("package") or {}
    version = str(get_app_update_status().get("latest_version") or "").strip()
    package_label = str(package.get("label") or platform_key).strip()
    log_path = str(staged.get("log_path") or get_app_support_paths()["update_install_log"])
    progress_help = app_update_install_progress_text().replace("\n", "<br>")
    html = f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8">
        <title>Installing ContactsFreeShare Update</title>
      </head>
      <body style="font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif; margin: 0; background: #f7f4ee; color: #20292f;">
        <main style="max-width: 42rem; margin: 4rem auto; padding: 2rem; background: white; border: 1px solid #d7d1c6; border-radius: 12px;">
          <h1 style="margin-top: 0;">Installing ContactsFreeShare {version}</h1>
          <p>The {package_label} update has been downloaded. ContactsFreeShare will close, install the update, and reopen.</p>
          <p>You can close this browser tab after the updated app window opens.</p>
          <p style="margin-top: 1.5rem; padding: 1rem; background: #f7f4ee; border-radius: 8px; font-size: 0.95rem;">
            {progress_help}<br>
            <strong style="word-break: break-all;">{log_path}</strong>
          </p>
        </main>
      </body>
    </html>
    """
    return HTMLResponse(html)
