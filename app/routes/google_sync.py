import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.services.contact_service import get_contact_row_colors, list_contacts
from app.services.google_sync_service import (
    clear_access_session,
    clear_pending_oauth_state,
    complete_google_oauth,
    create_google_auth_url,
    disconnect_google_account,
    acquire_editor_lock,
    describe_google_http_error,
    ensure_google_drive_app_revisions_storage,
    extend_editor_lock,
    export_app_revisions_to_google_drive,
    export_editor_session_drive_backups,
    export_non_editor_access_data_to_google_drive,
    export_shared_data_to_google_drive,
    export_shared_data_after_edit,
    import_print_role_shared_data_from_google_drive,
    get_current_access_role,
    get_editor_lock_status,
    get_pending_editor_name,
    get_google_signin_sync_progress,
    get_google_upload_progress,
    get_google_picker_config,
    get_google_sync_summary,
    import_google_contacts,
    import_shared_settings_from_google_drive,
    is_multi_editor_enabled,
    is_editing_allowed,
    is_non_editor_access_role,
    is_share_contacts_nav_visible,
    list_google_drive_backup_files,
    load_google_drive_backup_json,
    mark_meetingdata_for_drive_export,
    get_notice_message,
    pending_non_editor_drive_export_work_remaining,
    record_google_import_warning,
    normalize_access_role,
    record_google_sync_error,
    release_editor_lock,
    retry_google_signin_sync,
    save_access_session,
    save_google_sync_settings,
    save_single_editor_sync_setting,
    set_pending_editor_name,
    start_google_signin_sync,
    start_google_upload_job,
    sync_google_contact_changes,
    upload_google_contacts_only,
    upload_pending_changes,
    verify_editor_secret,
    verify_google_picker_folder,
)
from app.services.backup_service import restore_contacts_backup_payload, restore_meetingdata_backup_payload
from app.web_templates import templates


router = APIRouter(prefix="/google", tags=["google-sync"])
PRINT_ACCESS_ROLES = {"non_editor", "field_list_creation", "address_book_creation"}


def _google_connect_notice_url(notice: str) -> str:
    return f"/presets/google-connect?notice={notice}&rt={int(time.time() * 1000)}"


def _editor_signed_in_redirect_url() -> str:
    # Do not check GitHub/Drive for app updates here. That used to block Editor
    # sign-in for up to 30s after the lock was already taken.
    return _google_connect_notice_url("editor_signed_in")


def _access_role_redirect_url(access_role: str) -> str:
    if normalize_access_role(access_role) == "non_editor":
        return "/presets/google-connect?notice=access_signed_in"
    return "/presets/google-connect?notice=access_signed_in"


def _import_google_contacts_for_print_role(access_role: str) -> None:
    if normalize_access_role(access_role) == "non_editor":
        return
    if access_role not in PRINT_ACCESS_ROLES:
        return
    import_google_contacts()


def _can_upload_shared_data() -> bool:
    current_role = get_current_access_role()
    if current_role != "editor":
        return False
    return is_editing_allowed()


@router.get("/connect/start")
def google_connect_start():
    editor_lock_status = get_editor_lock_status()
    if bool(editor_lock_status.get("active")):
        return RedirectResponse(
            url="/presets/google-connect?notice=editor_signout_before_disconnect",
            status_code=303,
        )
    try:
        auth_url = create_google_auth_url()
    except RuntimeError:
        return RedirectResponse(url="/presets/google-connect?notice=oauth_not_configured", status_code=303)
    return RedirectResponse(url=auth_url, status_code=303)


@router.get("/picker/config")
def google_picker_config(origin: str = Query(default="")):
    try:
        return JSONResponse(get_google_picker_config(origin), headers={"Cache-Control": "no-store"})
    except (RuntimeError, HTTPError, URLError) as exc:
        return JSONResponse(
            {"ok": False, "reason": "token_unavailable", "message": str(exc)},
            status_code=400,
            headers={"Cache-Control": "no-store"},
        )


@router.get("/picker/verify-folder")
def google_picker_verify_folder(folder_id: str = Query(default="")):
    try:
        result = verify_google_picker_folder(folder_id)
    except (RuntimeError, HTTPError, URLError) as exc:
        return JSONResponse(
            {"ok": False, "reason": "token_unavailable", "message": str(exc)},
            status_code=400,
            headers={"Cache-Control": "no-store"},
        )
    status_code = 200 if result.get("ok") else 400
    return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})


@router.get("/connect/callback")
def google_connect_callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
):
    if error:
        clear_pending_oauth_state()
        record_google_sync_error(f"Google OAuth returned an error: {error}")
        return RedirectResponse(url=f"/presets/google-connect?notice=import_failed", status_code=303)
    try:
        complete_google_oauth(code, state)
        editor_name = get_pending_editor_name()
        access_role = get_current_access_role()
        if is_multi_editor_enabled() and access_role == "editor" and editor_name:
            result = acquire_editor_lock(editor_name, settings_loaded=True)
            status = str(result.get("status") or "")
            if status == "acquired":
                save_access_session("editor", str(result.get("editor_name") or editor_name))
                # Changes List imports in the background sign-in sync job; do not
                # block the OAuth redirect on another Drive round-trip here.
                return RedirectResponse(url=_editor_signed_in_redirect_url(), status_code=303)
            if status == "blocked":
                return RedirectResponse(url=_google_connect_notice_url("editor_lock_blocked"), status_code=303)
            if status == "stale":
                return RedirectResponse(url=_google_connect_notice_url("editor_lock_stale"), status_code=303)
            record_google_sync_error(f"Editor lock was not acquired after Google sign-in. Status: {status or 'unknown'}")
            return RedirectResponse(url=_google_connect_notice_url("editor_lock_failed"), status_code=303)
        if is_multi_editor_enabled():
            current_state = (get_google_sync_summary().get("state") or {})
            try:
                if bool(current_state.get("changes_list_needs_drive_export")):
                    export_shared_data_to_google_drive()
                    return RedirectResponse(url="/presets/google-connect?notice=drive_export_success", status_code=303)
                if (
                    normalize_access_role(access_role) == "non_editor"
                    or access_role in {"field_list_creation", "address_book_creation", "changes_contributor"}
                ):
                    import_print_role_shared_data_from_google_drive()
                    if normalize_access_role(access_role) != "non_editor":
                        _import_google_contacts_for_print_role(access_role)
                    return RedirectResponse(url=_access_role_redirect_url(access_role), status_code=303)
            except (RuntimeError, HTTPError, URLError) as sync_exc:
                message = describe_google_http_error(sync_exc) if isinstance(sync_exc, HTTPError) else str(sync_exc)
                record_google_sync_error(f"Google connect shared sync failed: {message}")
                return RedirectResponse(url="/presets/google-connect?notice=upload_failed", status_code=303)
            return RedirectResponse(url="/presets/google-connect?notice=google_connected", status_code=303)
        start_google_signin_sync()
    except (RuntimeError, HTTPError, URLError) as exc:
        clear_pending_oauth_state()
        message = describe_google_http_error(exc) if isinstance(exc, HTTPError) else str(exc)
        record_google_sync_error(f"Google sign-in callback failed: {message}")
        return RedirectResponse(url="/presets/google-connect?notice=import_failed", status_code=303)
    return RedirectResponse(url="/presets/google-connect?notice=google_connected", status_code=303)


@router.post("/connect/disconnect")
def google_connect_disconnect():
    editor_lock_status = get_editor_lock_status()
    if bool(editor_lock_status.get("active")):
        return RedirectResponse(
            url="/presets/google-connect?notice=editor_signout_before_disconnect",
            status_code=303,
        )
    disconnect_google_account()
    return RedirectResponse(url="/presets/google-connect?notice=google_disconnected", status_code=303)


@router.post("/editor/sign-in")
def google_editor_sign_in(
    editor_name: str = Form(default=""),
    editor_secret: str = Form(default=""),
    account_email: str = Form(default=""),
    remember_preferred_account: str = Form(default=""),
):
    normalized_name = str(editor_name or "").strip()
    if not normalized_name:
        return RedirectResponse(url="/presets/google-connect?notice=editor_name_required", status_code=303)
    if not str(editor_secret or "").strip():
        return RedirectResponse(url="/presets/google-connect?notice=editor_secret_required", status_code=303)
    if not verify_editor_secret(editor_secret):
        summary = get_google_sync_summary()
        account = summary.get("account") or {}
        if str(account.get("status") or "") == "connected" and bool(account.get("drive_scope_ready")):
            try:
                import_shared_settings_from_google_drive()
            except (RuntimeError, HTTPError, URLError):
                pass
        if not verify_editor_secret(editor_secret):
            return RedirectResponse(url="/presets/google-connect?notice=editor_secret_invalid", status_code=303)
    set_pending_editor_name(normalized_name)
    if str(account_email or "").strip():
        save_google_sync_settings(str(account_email or ""), True)
    summary = get_google_sync_summary()
    account = summary.get("account") or {}
    if str(account.get("status") or "") != "connected":
        return RedirectResponse(url="/google/connect/start", status_code=303)
    try:
        # Keep lock acquire fast (minimal Drive files), but start the background
        # sign-in sync so this computer loads other Editors' shared CFS data.
        result = acquire_editor_lock(
            normalized_name,
            settings_loaded=True,
            minimal_drive_storage=True,
            start_signin_sync=True,
        )
    except Exception as exc:
        message = describe_google_http_error(exc) if isinstance(exc, HTTPError) else str(exc)
        record_google_sync_error(f"Editor lock failed: {message}")
        return RedirectResponse(url=_google_connect_notice_url("editor_lock_failed"), status_code=303)
    status = str(result.get("status") or "")
    if status == "acquired":
        save_access_session("editor", str(result.get("editor_name") or normalized_name))
        return RedirectResponse(url=_editor_signed_in_redirect_url(), status_code=303)
    if status == "blocked":
        return RedirectResponse(url=_google_connect_notice_url("editor_lock_blocked"), status_code=303)
    if status == "stale":
        return RedirectResponse(url=_google_connect_notice_url("editor_lock_stale"), status_code=303)
    return RedirectResponse(url=_google_connect_notice_url("editor_lock_failed"), status_code=303)


@router.post("/editor/takeover")
def google_editor_takeover():
    editor_name = get_pending_editor_name()
    if not editor_name:
        return RedirectResponse(url="/presets/google-connect?notice=editor_name_required", status_code=303)
    try:
        result = acquire_editor_lock(editor_name, force=True)
    except (RuntimeError, HTTPError, URLError):
        return RedirectResponse(url=_google_connect_notice_url("editor_lock_failed"), status_code=303)
    if str(result.get("status") or "") == "acquired":
        save_access_session("editor", editor_name)
        return RedirectResponse(url=_editor_signed_in_redirect_url(), status_code=303)
    return RedirectResponse(url=_google_connect_notice_url("editor_lock_failed"), status_code=303)


@router.get("/editor/sign-out")
def google_editor_sign_out_get():
    # Sign-out is POST. A refresh after the long wait hits this URL with GET
    # and FastAPI would otherwise show {"detail":"Method Not Allowed"}.
    return RedirectResponse(url="/presets/google-connect", status_code=303)


@router.post("/editor/sign-out")
def google_editor_sign_out():
    # Best-effort flush of queued Google contact edits, then always release the lock.
    # Sign-out must not fail just because a background upload/export is stuck.
    try:
        upload_google_contacts_only(finish_progress=True)
    except (RuntimeError, HTTPError, URLError):
        pass
    try:
        export_editor_session_drive_backups()
    except (RuntimeError, HTTPError, URLError):
        pass
    try:
        release_editor_lock()
    except (RuntimeError, HTTPError, URLError):
        pass
    clear_access_session()
    return RedirectResponse(url="/presets/google-connect?notice=editor_signed_out", status_code=303)


@router.post("/access/sign-in")
def google_access_sign_in(
    access_role: str = Form(default="non_editor"),
    access_name: str = Form(default=""),
    editor_name: str = Form(default=""),
    account_email: str = Form(default=""),
    remember_preferred_account: str = Form(default=""),
):
    normalized_name = str(access_name or editor_name or "").strip()
    if not normalized_name:
        return RedirectResponse(url="/presets/google-connect?notice=access_name_required", status_code=303)
    if str(account_email or "").strip():
        save_google_sync_settings(str(account_email or ""), True)
    save_access_session(access_role, normalized_name)
    normalized_role = get_current_access_role()
    summary = get_google_sync_summary()
    account = summary.get("account") or {}
    if str(account.get("status") or "") != "connected":
        return RedirectResponse(url="/google/connect/start", status_code=303)
    if str(account.get("status") or "") == "connected" and bool(account.get("drive_scope_ready")):
        try:
            import_print_role_shared_data_from_google_drive()
        except (RuntimeError, HTTPError, URLError):
            pass
    return RedirectResponse(url=_access_role_redirect_url(normalized_role), status_code=303)


@router.get("/access/sign-out")
def google_access_sign_out_get():
    return RedirectResponse(url="/presets/google-connect", status_code=303)


@router.post("/access/sign-out")
def google_access_sign_out():
    summary = get_google_sync_summary()
    account = summary.get("account") or {}
    state = summary.get("state") or {}
    changes_list_dirty = bool(state.get("changes_list_needs_drive_export"))
    drive_ready = str(account.get("status") or "") == "connected" and bool(account.get("drive_scope_ready"))
    if changes_list_dirty and not drive_ready:
        return RedirectResponse(
            url="/presets/google-connect?notice=changes_list_connect_google_before_signout",
            status_code=303,
        )
    if drive_ready and pending_non_editor_drive_export_work_remaining():
        try:
            export_non_editor_access_data_to_google_drive()
        except (RuntimeError, HTTPError, URLError):
            return RedirectResponse(url="/presets/google-connect?notice=upload_failed", status_code=303)
    clear_access_session()
    return RedirectResponse(url="/presets/google-connect?notice=editor_signed_out", status_code=303)


@router.get("/editor/status")
def google_editor_status():
    return JSONResponse(
        {
            "ok": True,
            **get_editor_lock_status(),
            "share_contacts_visible": is_share_contacts_nav_visible(),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/editor/extend")
def google_editor_extend():
    try:
        result = extend_editor_lock()
    except (RuntimeError, HTTPError, URLError):
        result = {"status": "failed", **get_editor_lock_status()}
    return JSONResponse(
        {
            "ok": result.get("status") in {"extended", "extended_local"},
            **result,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/import", response_class=HTMLResponse)
def google_import_confirm_page(
    request: Request,
    return_to: str = Query(default="/contacts/"),
    notice: str = Query(default=""),
):
    safe_return_to = return_to if return_to.startswith("/contacts") else "/contacts/"
    summary = get_google_sync_summary()
    backup_files = {"contacts": [], "meetingdata": []}
    backup_error_message = ""
    account = summary.get("account") or {}
    if str(account.get("status") or "") == "connected" and bool(account.get("drive_scope_ready")):
        try:
            backup_files = list_google_drive_backup_files()
        except (RuntimeError, HTTPError, URLError):
            backup_error_message = "Backup files could not be loaded from Google Drive."
    context = {
        "return_to": safe_return_to,
        "contact_count": len(list_contacts()),
        "row_colors": get_contact_row_colors(),
        "backup_files": backup_files,
        "backup_error_message": backup_error_message,
        "notice_message": get_notice_message(notice),
        **summary,
    }
    return templates.TemplateResponse(
        request=request,
        name="google_sync/import_confirm.html",
        context=context,
    )


@router.post("/sync-changes")
def google_sync_changes_run(
    return_to: str = Form(default="/contacts/"),
):
    safe_return_to = return_to if return_to.startswith("/contacts") else "/contacts/"
    if not is_editing_allowed():
        return RedirectResponse(url=f"{safe_return_to}?google_notice=editor_lock_blocked", status_code=303)
    try:
        result = sync_google_contact_changes(force_full_fetch=True)
    except (RuntimeError, HTTPError, URLError):
        return RedirectResponse(url=f"{safe_return_to}?google_notice=google_sync_changes_failed", status_code=303)
    changed_count = int(result.get("changed_count") or 0)
    skipped_conflict_count = int(result.get("skipped_conflict_count") or 0)
    if changed_count <= 0 and skipped_conflict_count <= 0:
        notice = "google_sync_changes_none"
    elif skipped_conflict_count > 0:
        notice = "google_sync_changes_conflicts"
    else:
        notice = "google_sync_changes_success"
    params = {"google_notice": notice}
    if notice == "google_sync_changes_success":
        params["google_sync_changed"] = str(changed_count)
    return RedirectResponse(url=f"{safe_return_to}?{urlencode(params)}", status_code=303)


@router.post("/import")
def google_import_run(
    return_to: str = Form(default="/contacts/"),
):
    safe_return_to = return_to if return_to.startswith("/contacts") else "/contacts/"
    current_access_role = get_current_access_role()
    print_access_import = is_non_editor_access_role(current_access_role) or current_access_role in PRINT_ACCESS_ROLES
    if not is_editing_allowed() and not print_access_import:
        return RedirectResponse(url=f"{safe_return_to}?google_notice=editor_lock_blocked", status_code=303)
    record_google_import_warning()
    previous_contact_count = len(list_contacts())
    try:
        imported_count = import_google_contacts()
    except (RuntimeError, HTTPError, URLError):
        return RedirectResponse(url=f"{safe_return_to}?google_notice=import_failed", status_code=303)
    summary = get_google_sync_summary()
    state = summary.get("state") or {}
    account = summary.get("account") or {}
    if not bool(state.get("multi_editor_enabled")) and str(account.get("status") or "") == "connected":
        save_single_editor_sync_setting(True)
    if not print_access_import and (imported_count > 0 or previous_contact_count > 0):
        try:
            export_shared_data_after_edit()
        except (RuntimeError, HTTPError, URLError):
            pass
    return RedirectResponse(url=f"{safe_return_to}?google_notice=import_success", status_code=303)


@router.post("/import/backup")
def google_backup_import_run(
    backup_kind: str = Form(default=""),
    backup_file_id: str = Form(default=""),
):
    normalized_kind = str(backup_kind or "").strip().lower()
    normalized_file_id = str(backup_file_id or "").strip()
    if not is_editing_allowed():
        return RedirectResponse(url="/google/import?notice=editor_lock_blocked", status_code=303)
    if normalized_kind not in {"contacts", "meetingdata"} or not normalized_file_id:
        return RedirectResponse(url="/google/import?notice=backup_import_missing_selection", status_code=303)

    try:
        payload = load_google_drive_backup_json(normalized_file_id)
        if normalized_kind == "contacts":
            restore_contacts_backup_payload(payload)
            try:
                export_shared_data_after_edit()
            except (RuntimeError, HTTPError, URLError):
                pass
            return RedirectResponse(url="/contacts/?google_notice=backup_import_success", status_code=303)
        restore_meetingdata_backup_payload(payload)
        mark_meetingdata_for_drive_export()
        return RedirectResponse(url="/meetings/v2/sections?notice=backup_import_success", status_code=303)
    except (RuntimeError, ValueError, HTTPError, URLError):
        return RedirectResponse(url="/google/import?notice=backup_import_failed", status_code=303)


@router.post("/upload")
def google_upload_run(
    return_to: str = Form(default="/contacts/"),
):
    safe_return_to = return_to if return_to.startswith("/contacts") else "/contacts/"
    if not _can_upload_shared_data():
        return RedirectResponse(url=f"{safe_return_to}?google_notice=editor_lock_blocked", status_code=303)
    try:
        result = upload_pending_changes()
    except RuntimeError as exc:
        message = str(exc).lower()
        if "connect" in message or "refresh token" in message or "expired" in message or "revoked" in message:
            notice = "upload_not_connected"
        else:
            notice = "upload_failed"
        return RedirectResponse(url=f"{safe_return_to}?google_notice={notice}", status_code=303)
    except (HTTPError, URLError):
        return RedirectResponse(url=f"{safe_return_to}?google_notice=upload_failed", status_code=303)

    notice = "upload_success" if int(result.get("processed_count") or 0) > 0 else "upload_queue_empty"
    return RedirectResponse(url=f"{safe_return_to}?google_notice={notice}", status_code=303)


@router.post("/upload/start")
def google_upload_start():
    if not _can_upload_shared_data():
        return JSONResponse(
            {
                "ok": False,
                "started": False,
                "in_progress": False,
                "notice": "editor_lock_blocked",
                "message": "Editor sign-in is required before uploading shared app data.",
            },
            status_code=403,
        )
    try:
        result = start_google_upload_job()
    except RuntimeError as exc:
        message = str(exc).lower()
        if "connect" in message or "refresh token" in message or "expired" in message or "revoked" in message:
            notice = "upload_not_connected"
        else:
            notice = "upload_failed"
        return JSONResponse(
            {
                "ok": False,
                "notice": notice,
                "message": get_notice_message(notice),
            },
            status_code=400,
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(
        {
            "ok": True,
            "notice": "upload_in_progress" if result.get("in_progress") else "upload_queue_empty",
            **result,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/upload/status")
def google_upload_status():
    return JSONResponse({"ok": True, **get_google_upload_progress()}, headers={"Cache-Control": "no-store"})


@router.get("/signin-sync/status")
def google_signin_sync_status():
    return JSONResponse({"ok": True, **get_google_signin_sync_progress()}, headers={"Cache-Control": "no-store"})


@router.post("/signin-sync/retry")
def google_signin_sync_retry():
    if retry_google_signin_sync():
        return RedirectResponse(url=_google_connect_notice_url("signin_sync_retry_started"), status_code=303)
    return RedirectResponse(url=_google_connect_notice_url("signin_sync_failed_blocked"), status_code=303)


@router.post("/drive/app-revisions/setup")
def google_drive_app_revisions_setup():
    try:
        ensure_google_drive_app_revisions_storage()
    except RuntimeError as exc:
        message = str(exc)
        if "Drive access has not been granted" in message:
            notice = "drive_scope_missing"
        elif "not connected" in message.lower() or "refresh token" in message.lower():
            notice = "drive_not_connected"
        else:
            notice = "drive_export_failed"
        return RedirectResponse(url=f"/presets/google-connect?notice={notice}", status_code=303)
    except (HTTPError, URLError):
        return RedirectResponse(url="/presets/google-connect?notice=drive_export_failed", status_code=303)
    return RedirectResponse(url="/presets/google-connect?notice=drive_storage_ready", status_code=303)


@router.post("/drive/app-revisions/export")
def google_drive_app_revisions_export():
    try:
        export_app_revisions_to_google_drive()
    except RuntimeError as exc:
        message = str(exc)
        if "Drive access has not been granted" in message:
            notice = "drive_scope_missing"
        elif "not connected" in message.lower() or "refresh token" in message.lower():
            notice = "drive_not_connected"
        else:
            notice = "drive_export_failed"
        return RedirectResponse(url=f"/presets/google-connect?notice={notice}", status_code=303)
    except (HTTPError, URLError):
        return RedirectResponse(url="/presets/google-connect?notice=drive_export_failed", status_code=303)
    return RedirectResponse(url="/presets/google-connect?notice=drive_export_success", status_code=303)
