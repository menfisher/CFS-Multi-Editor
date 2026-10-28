from __future__ import annotations

import threading
from urllib.error import HTTPError, URLError
from urllib.parse import quote

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.services.google_sync_service import (
    acquire_editor_lock,
    clear_access_session,
    disconnect_google_account,
    export_changes_list_to_google_drive,
    extend_editor_lock,
    format_editor_lock_blocked_message,
    get_editor_lock_status,
    get_google_sync_summary,
    import_changes_list_from_google_drive,
    import_field_list_from_google_drive,
    import_shared_settings_from_google_drive,
    is_multi_editor_enabled,
    normalize_access_role,
    probe_remote_editor_lock,
    release_editor_lock,
    record_google_sync_error,
    save_access_session,
    set_pending_editor_name,
    verify_editor_secret,
)

from mobile.access_session import mobile_is_non_editor
from mobile.google_auth import (
    authorization_url,
    begin_callback_exchange,
    cached_callback_redirect,
    exchange_code,
    finish_callback_exchange,
    save_account_tokens,
    wait_for_callback_redirect,
)
from mobile.routes.contacts import (
    clear_google_contacts_refresh_flag,
    mark_google_contacts_refreshed,
)
from mobile.sync_adapter import has_cached_contacts
from mobile.user_db import mobile_user_db_session, use_user_database, user_db_write_lock
from mobile.web_templates import templates

router = APIRouter(prefix="/auth", tags=["auth"])

PENDING_ACCESS_ROLE_KEY = "pending_mobile_access_role"
PENDING_ACCESS_NAME_KEY = "pending_mobile_access_name"
PENDING_EDITOR_SECRET_KEY = "pending_mobile_editor_secret"
MOBILE_ACCESS_ROLE_KEY = "mobile_access_role"
MOBILE_ACCESS_NAME_KEY = "mobile_access_name"


def _editor_secret_configured() -> bool:
    from app.database import fetch_one

    row = fetch_one("SELECT editor_secret_hash FROM google_sync_state WHERE id = 1")
    if not row:
        return False
    return bool(str(row["editor_secret_hash"] or "").strip())


def _clear_mobile_access_session(request: Request) -> None:
    request.session.pop(MOBILE_ACCESS_ROLE_KEY, None)
    request.session.pop(MOBILE_ACCESS_NAME_KEY, None)


def _store_mobile_access_session(request: Request, access_role: str, access_name: str) -> None:
    request.session[MOBILE_ACCESS_ROLE_KEY] = normalize_access_role(access_role)
    request.session[MOBILE_ACCESS_NAME_KEY] = str(access_name or "").strip()


def _clear_pending_sign_in(request: Request) -> None:
    request.session.pop(PENDING_ACCESS_ROLE_KEY, None)
    request.session.pop(PENDING_ACCESS_NAME_KEY, None)
    request.session.pop(PENDING_EDITOR_SECRET_KEY, None)


def _store_pending_sign_in(
    request: Request,
    *,
    access_role: str,
    access_name: str,
    editor_secret: str = "",
) -> RedirectResponse | None:
    normalized_role = normalize_access_role(access_role)
    normalized_name = str(access_name or "").strip()
    if not normalized_name:
        return RedirectResponse(url="/?error=Enter%20your%20name%20or%20initials%20before%20signing%20in.", status_code=303)
    if normalized_role == "editor" and not str(editor_secret or "").strip():
        return RedirectResponse(url="/?error=Enter%20the%20Editor%20password%20before%20signing%20in.", status_code=303)

    if normalized_role == "editor" and is_multi_editor_enabled():
        lock_status = get_editor_lock_status()
        remaining_seconds = int(lock_status.get("remaining_seconds") or 0)
        owner_name = str(lock_status.get("owner_name") or "").strip()
        if remaining_seconds > 0 and owner_name and not bool(lock_status.get("active")):
            message = format_editor_lock_blocked_message(
                owner_name=owner_name,
                remaining_seconds=remaining_seconds,
            )
            return RedirectResponse(url=f"/?error={quote(message)}", status_code=303)

    request.session[PENDING_ACCESS_ROLE_KEY] = normalized_role
    request.session[PENDING_ACCESS_NAME_KEY] = normalized_name
    request.session[PENDING_EDITOR_SECRET_KEY] = str(editor_secret or "")
    try:
        url = authorization_url(
            request,
            access_role=normalized_role,
            access_name=normalized_name,
            editor_secret=str(editor_secret or ""),
        )
    except RuntimeError as exc:
        _clear_pending_sign_in(request)
        return RedirectResponse(url=f"/?error={exc}", status_code=303)
    return RedirectResponse(url=url, status_code=303)


@router.post("/prepare")
def auth_prepare_sign_in(
    request: Request,
    access_role: str = Form(default="editor"),
    access_name: str = Form(default=""),
    editor_secret: str = Form(default=""),
):
    response = _store_pending_sign_in(
        request,
        access_role=access_role,
        access_name=access_name,
        editor_secret=editor_secret,
    )
    return response or RedirectResponse(url="/", status_code=303)


@router.get("/login")
def auth_login_get(request: Request):
    try:
        url = authorization_url(request)
    except RuntimeError as exc:
        return RedirectResponse(url=f"/?error={exc}", status_code=303)
    return RedirectResponse(url=url, status_code=303)


@router.post("/login")
def auth_login_post(
    request: Request,
    access_role: str = Form(default="editor"),
    access_name: str = Form(default=""),
    editor_secret: str = Form(default=""),
):
    response = _store_pending_sign_in(
        request,
        access_role=access_role,
        access_name=access_name,
        editor_secret=editor_secret,
    )
    return response or RedirectResponse(url="/", status_code=303)


def _pending_access_name(request: Request, oauth_state: dict | None = None) -> str:
    session_name = str(request.session.get(PENDING_ACCESS_NAME_KEY) or "").strip()
    state = oauth_state if isinstance(oauth_state, dict) else {}
    return session_name or str(state.get("access_name") or "").strip()


def _pending_sign_in_values(request: Request, oauth_state: dict | None = None) -> tuple[str, str, str]:
    session_role = str(request.session.pop(PENDING_ACCESS_ROLE_KEY, "") or "").strip()
    session_name = str(request.session.pop(PENDING_ACCESS_NAME_KEY, "") or "").strip()
    session_secret = str(request.session.pop(PENDING_EDITOR_SECRET_KEY, "") or "")
    state = oauth_state if isinstance(oauth_state, dict) else {}
    pending_role = normalize_access_role(session_role or state.get("access_role"))
    pending_name = session_name or str(state.get("access_name") or "").strip()
    pending_secret = session_secret or str(state.get("editor_secret") or "")
    return pending_role, pending_name, pending_secret


def _background_non_editor_sign_in_sync(email: str) -> None:
    normalized = str(email or "").strip()
    if not normalized:
        return

    def _run() -> None:
        try:
            with user_db_write_lock(normalized), mobile_user_db_session(normalized):
                try:
                    import_shared_settings_from_google_drive(export_if_missing=False)
                except (RuntimeError, HTTPError, URLError):
                    pass
                try:
                    import_changes_list_from_google_drive()
                    import_field_list_from_google_drive(export_if_missing=False)
                except (RuntimeError, HTTPError, URLError):
                    pass
        except Exception:
            pass

    threading.Thread(
        target=_run,
        daemon=True,
        name="mobile-non-editor-signin-sync",
    ).start()


def _sync_editor_settings_after_sign_in() -> None:
    try:
        import_shared_settings_from_google_drive(export_if_missing=False)
    except (RuntimeError, HTTPError, URLError):
        pass
    try:
        from mobile.sync_adapter import ensure_field_list_print_order

        ensure_field_list_print_order()
    except Exception:
        pass


def _background_editor_sign_in_sync(email: str) -> None:
    normalized = str(email or "").strip()
    if not normalized:
        return

    def _run() -> None:
        try:
            with user_db_write_lock(normalized):
                with mobile_user_db_session(normalized):
                    _sync_editor_settings_after_sign_in()
        except Exception:
            pass

    threading.Thread(
        target=_run,
        daemon=True,
        name="mobile-editor-signin-sync",
    ).start()


def _complete_editor_sign_in(email: str, pending_name: str, pending_secret: str) -> tuple[str | None, bool]:
    try:
        lock_probe = probe_remote_editor_lock(
            pending_editor_name=pending_name,
            apply_blocked_state=True,
        )
        if lock_probe.get("blocked"):
            return (
                format_editor_lock_blocked_message(
                    owner_name=str(lock_probe.get("owner_name") or ""),
                    expires_at=str(lock_probe.get("expires_at") or ""),
                ),
                True,
            )
        if not _editor_secret_configured():
            import_shared_settings_from_google_drive(export_if_missing=False)
        if not verify_editor_secret(pending_secret):
            return "Editor password is not correct.", False
        set_pending_editor_name(pending_name)
        result = acquire_editor_lock(
            pending_name,
            settings_loaded=_editor_secret_configured(),
            start_signin_sync=False,
            minimal_drive_storage=True,
        )
    except (RuntimeError, HTTPError, URLError) as exc:
        message = str(exc).strip() or "Editor sign-in failed."
        record_google_sync_error(message)
        return message, False
    status = str(result.get("status") or "")
    if status != "acquired":
        if status == "blocked":
            return (
                format_editor_lock_blocked_message(
                    owner_name=str(result.get("owner_name") or ""),
                    expires_at=str(result.get("expires_at") or ""),
                ),
                True,
            )
        if status == "stale":
            return (
                "The previous Editor session is stale. Sign in on desktop to confirm takeover.",
                False,
            )
        return "Editor sign-in failed.", False
    save_access_session("editor", pending_name)
    record_google_sync_error("")
    return None, False


def _apply_pending_access_session_values(
    request: Request,
    pending_role: str,
    pending_name: str,
    pending_secret: str,
    email: str,
) -> tuple[str | None, bool]:
    if not pending_name:
        return None, False

    if pending_role == "non_editor":
        save_access_session("non_editor", pending_name)
        _store_mobile_access_session(request, "non_editor", pending_name)
        return None, False

    if not is_multi_editor_enabled():
        return None, False

    if pending_role == "editor":
        return _complete_editor_sign_in(email, pending_name, pending_secret)

    return None, False


def _apply_pending_access_session(
    request: Request,
    oauth_state: dict | None = None,
) -> tuple[str | None, bool]:
    pending_role, pending_name, pending_secret = _pending_sign_in_values(request, oauth_state)
    email = str(request.session.get("owner_email") or "").strip()
    return _apply_pending_access_session_values(
        request,
        pending_role,
        pending_name,
        pending_secret,
        email,
    )


def _post_sign_in_redirect(is_non_editor: bool) -> str:
    if is_non_editor:
        return "/m/pending-edits/"
    return "/m/contacts/"


MULTI_EDITOR_SIGN_IN_REQUIRED_MESSAGE = (
    "This account uses Multi-Editor mode. Choose Editor or Pending Edits below, "
    "enter your name, and use Sign in with role."
)


def _abort_mobile_sign_in(request: Request) -> None:
    clear_access_session()
    _clear_mobile_access_session(request)
    _clear_pending_sign_in(request)
    request.session.pop("owner_email", None)


def _abort_blocked_editor_sign_in(request: Request) -> None:
    disconnect_google_account(preserve_editor_lock_notice=True)
    _clear_mobile_access_session(request)
    _clear_pending_sign_in(request)
    request.session.pop("owner_email", None)


def _finish_sign_in(request: Request, token_payload: dict) -> str:
    email = token_payload["email"]
    oauth_state = token_payload.get("oauth_state") if isinstance(token_payload.get("oauth_state"), dict) else {}
    pending_role, pending_name, pending_secret = _pending_sign_in_values(request, oauth_state)
    role_based_sign_in = bool(pending_name)
    save_account_tokens(email, token_payload)
    request.session["owner_email"] = email
    if is_multi_editor_enabled() and not role_based_sign_in:
        _abort_mobile_sign_in(request)
        raise RuntimeError(MULTI_EDITOR_SIGN_IN_REQUIRED_MESSAGE)

    access_error, editor_lock_blocked = _apply_pending_access_session_values(
        request,
        pending_role,
        pending_name,
        pending_secret,
        email,
    )

    if access_error:
        if editor_lock_blocked:
            _abort_blocked_editor_sign_in(request)
        else:
            _abort_mobile_sign_in(request)
        raise RuntimeError(access_error)
    is_non_editor = mobile_is_non_editor(request)
    if is_non_editor:
        _background_non_editor_sign_in_sync(email)
    else:
        _clear_mobile_access_session(request)
        if is_multi_editor_enabled():
            _background_editor_sign_in_sync(email)
    if has_cached_contacts():
        mark_google_contacts_refreshed(request)
    else:
        clear_google_contacts_refresh_flag(request)
    return _post_sign_in_redirect(is_non_editor)


@router.get("/callback")
def auth_callback(
    request: Request,
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    error_description: str = Query(default=""),
):
    if error:
        _clear_pending_sign_in(request)
        detail = str(error_description or error).strip() or "Google sign-in was cancelled."
        return RedirectResponse(url=f"/?error={quote(detail)}", status_code=303)
    if not code:
        return RedirectResponse(
            url="/?error="
            + quote(
                "Google sign-in did not finish. If ngrok shows a warning page, tap Visit Site, then try again."
            ),
            status_code=303,
        )
    return templates.TemplateResponse(
        request=request,
        name="auth/callback.html",
        context={},
    )


def _resolve_oauth_callback_redirect(request: Request, code: str, state: str) -> str:
    cached_redirect = cached_callback_redirect(code)
    if cached_redirect:
        return cached_redirect

    is_owner, inflight_event = begin_callback_exchange(code)
    if not is_owner:
        if inflight_event is not None:
            wait_for_callback_redirect(code)
        cached_redirect = cached_callback_redirect(code)
        if cached_redirect:
            return cached_redirect
        raise RuntimeError(
            "Google sign-in did not finish. Go back to the home page and try again."
        )

    redirect_url = "/"
    try:
        token_payload = exchange_code(request, code, state)
        with mobile_user_db_session(token_payload["email"]):
            redirect_url = _finish_sign_in(request, token_payload)
    except Exception as exc:
        _clear_pending_sign_in(request)
        redirect_url = f"/?error={quote(str(exc))}"
    finish_callback_exchange(code, redirect_url, inflight_event)
    return redirect_url


def _exchange_and_finish_sign_in(request: Request, code: str, state: str) -> RedirectResponse:
    try:
        redirect_url = _resolve_oauth_callback_redirect(request, code, state)
    except RuntimeError as exc:
        redirect_url = f"/?error={quote(str(exc))}"
    return RedirectResponse(url=redirect_url, status_code=303)


@router.get("/sign-in-status")
def auth_sign_in_status(request: Request):
    return JSONResponse(
        {"ok": True, "signed_in": bool(request.session.get("owner_email")), "complete": True},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/editor/status")
def auth_editor_status(request: Request):
    if not request.session.get("owner_email"):
        return JSONResponse(
            {"ok": False, "active": False, "remaining_seconds": 0},
            status_code=401,
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(
        {"ok": True, **get_editor_lock_status()},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/editor/extend")
def auth_editor_extend(request: Request):
    if not request.session.get("owner_email"):
        return JSONResponse(
            {"ok": False, "status": "not_active", "active": False, "remaining_seconds": 0},
            status_code=401,
            headers={"Cache-Control": "no-store"},
        )
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


@router.post("/exchange")
async def auth_exchange(
    request: Request,
    code: str = Form(default=""),
    state: str = Form(default=""),
):
    if not code:
        return JSONResponse(
            {"ok": False, "error": "Google sign-in did not return an authorization code."},
            status_code=400,
        )
    try:
        redirect_url = _resolve_oauth_callback_redirect(request, code, state)
    except RuntimeError as exc:
        _clear_pending_sign_in(request)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse({"ok": True, "redirect": redirect_url})

def _clear_shared_sign_in_state() -> None:
    clear_access_session()
    try:
        release_editor_lock()
    except (RuntimeError, HTTPError, URLError):
        pass


def _background_logout_cleanup(email: str) -> None:
    if not email:
        return
    with mobile_user_db_session(email):
        try:
            if is_multi_editor_enabled():
                _clear_shared_sign_in_state()
            else:
                clear_access_session()
        except (RuntimeError, HTTPError, URLError):
            try:
                clear_access_session()
            except (RuntimeError, HTTPError, URLError):
                pass

        try:
            summary = get_google_sync_summary()
            account = summary.get("account") or {}
            state = summary.get("state") or {}
            if not bool(state.get("multi_editor_enabled")):
                return
            drive_ready = str(account.get("status") or "") == "connected" and bool(account.get("drive_scope_ready"))
            if drive_ready and bool(state.get("changes_list_needs_drive_export")):
                try:
                    export_pending_edits_support_to_google_drive()
                except (RuntimeError, HTTPError, URLError):
                    pass
                export_changes_list_to_google_drive(merge_remote=True)
        except (RuntimeError, HTTPError, URLError):
            pass


@router.get("/logout")
@router.post("/logout")
def auth_logout(request: Request):
    email = str(request.session.get("owner_email") or "").strip()

    # Clear the browser session first so Log out always returns quickly.
    _clear_pending_sign_in(request)
    _clear_mobile_access_session(request)
    clear_google_contacts_refresh_flag(request)
    request.session.pop("owner_email", None)

    if email:
        threading.Thread(
            target=_background_logout_cleanup,
            args=(email,),
            daemon=True,
            name="mobile-logout-cleanup",
        ).start()

    return RedirectResponse(url="/", status_code=303)
