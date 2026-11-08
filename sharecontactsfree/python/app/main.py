from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from . import db
from .cfs_bridge import create_bridge_token, verify_bridge_token
from .config import APP_BASE_URL, APP_SECRET_KEY, CFS_API_KEY, FRONTEND_DIR, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, UI_BUILD_TAG
from .google_auth import (
    OAUTH_BASE_SESSION_KEY,
    _fetch_userinfo,
    owner_authorization_url,
    owner_credentials,
    owner_exchange_code,
    recipient_authorization_url,
    recipient_credentials,
    recipient_exchange_code,
    request_base_url,
)
from .people_api import get_profile, is_rate_limit_error
from .services import app_data as app_data_svc

logger = logging.getLogger(__name__)

app = FastAPI(title="Share Google Contacts")
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
app.add_middleware(SessionMiddleware, secret_key=APP_SECRET_KEY)


@app.middleware("http")
async def _persist_db_after_request(request: Request, call_next):
    """Save import progress while CPU is still allocated to this request.

    Cloud Run throttles CPU between requests, so a background upload timer is
    not reliable; an instance can die with progress only on local disk and the
    next cold start restores an older copy.
    """
    response = await call_next(request)
    try:
        from .db_storage import flush_pending_gcs_upload

        await asyncio.to_thread(flush_pending_gcs_upload)
    except Exception:
        logger.exception("Failed to persist share DB after request")
    return response

templates = Jinja2Templates(directory=str(FRONTEND_DIR))


def _owner_email(request: Request) -> str:
    email = request.session.get("owner_email")
    if not email:
        raise HTTPException(401, "Sign in required")
    return email


def _active_owner_email(request: Request) -> str | None:
    email = str(request.session.get("owner_email") or "").strip()
    if not email:
        return None
    try:
        if owner_credentials(email):
            return email
    except Exception:
        pass
    request.session.pop("owner_email", None)
    return None


def _oauth_error_page(title: str, message: str, hint: str = "") -> HTMLResponse:
    body = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font-family:system-ui;max-width:640px;margin:40px auto;padding:0 16px}}
pre{{white-space:pre-wrap;background:#f5f5f5;padding:12px;border-radius:6px}}</style>
</head><body>
<h2>{title}</h2>
<pre>{message}</pre>
<p>{hint}</p>
<p><a href="/auth/owner/login">Try sign-in again</a></p>
</body></html>"""
    return HTMLResponse(body, status_code=400)


def _oauth_redirect_hint(request: Request) -> str:
    base_url = str(request.session.get(OAUTH_BASE_SESSION_KEY) or request_base_url(request)).rstrip("/")
    if base_url.startswith("https://"):
        return (
            "Add these Authorized redirect URIs in Google Cloud Console "
            "(APIs & Services → Credentials → your OAuth client):<br>"
            f"<code>{base_url}/auth/owner/callback</code><br>"
            f"<code>{base_url}/auth/recipient/callback</code>"
        )
    return (
        "Add BOTH redirect URIs in Google Cloud (same OAuth client as CFS):<br>"
        "<code>http://127.0.0.1:8080/auth/owner/callback</code><br>"
        "<code>http://localhost:8080/auth/owner/callback</code><br>"
        "<code>http://127.0.0.1:8080/auth/recipient/callback</code><br>"
        "<code>http://localhost:8080/auth/recipient/callback</code><br>"
        "Open the app with the same host (e.g. always http://127.0.0.1:8080)."
    )


@app.on_event("startup")
def startup():
    db.init_db()
    from .services.push_worker import start_push_worker

    start_push_worker()


@app.on_event("shutdown")
def shutdown():
    try:
        from .db_storage import flush_gcs_upload, flush_pending_gcs_upload

        flush_pending_gcs_upload(force=True)
        flush_gcs_upload(force=True)
    except Exception:
        logger.exception("Failed to flush share DB to GCS on shutdown")


INVITE_RECIPIENT_SESSION_KEY = "invite_recipient_email"


def _normalize_invite_email(email: str | None) -> str:
    return db.normalize_email(str(email or ""))


def _shared_login_url(invite_email: str = "") -> str:
    invite_email = _normalize_invite_email(invite_email)
    if invite_email:
        return f"/auth/owner/login?mode=shared&email={quote(invite_email)}"
    return "/auth/owner/login?mode=shared"


@app.get("/auth/owner/login")
def owner_login(request: Request, mode: str | None = None, email: str | None = None):
    if mode == "shared":
        request.session["app_mode"] = "shared"
    login_hint = _normalize_invite_email(email) or None
    if login_hint:
        request.session[INVITE_RECIPIENT_SESSION_KEY] = login_hint
    shared_mode = request.session.get("app_mode") == "shared" or mode == "shared"
    if shared_mode:
        # select_account + consent so Google returns a refresh_token for recipients.
        prompt = "select_account consent"
    else:
        prompt = None
    return RedirectResponse(
        owner_authorization_url(request, login_hint=login_hint, prompt=prompt)
    )


@app.get("/auth/cfs-bridge")
def cfs_auth_bridge(request: Request, token: str | None = None):
    """Establish owner session from a short-lived token issued by CFS desktop."""
    login_hint = ""
    if token:
        try:
            email = verify_bridge_token(token)
            login_hint = email
            request.session["owner_email"] = email
            request.session["app_mode"] = "owner"
            return RedirectResponse("/")
        except Exception:
            pass
    if login_hint:
        return RedirectResponse(f"/auth/owner/login?email={login_hint}")
    return RedirectResponse("/auth/owner/login")


@app.get("/auth/owner/callback")
def owner_callback(request: Request, code: str | None = None, error: str | None = None):
    if error or not code:
        return _oauth_error_page("Login failed", error or "Google did not return an authorization code.")
    base_url = request.session.get(OAUTH_BASE_SESSION_KEY) or request_base_url(request)
    try:
        _, email = owner_exchange_code(code, base_url)
        invite_email = _normalize_invite_email(
            request.session.get(INVITE_RECIPIENT_SESSION_KEY)
        )
        if invite_email and email.strip().lower() != invite_email:
            request.session.pop("owner_email", None)
            return _oauth_error_page(
                "Wrong Google account",
                f"This invite is for {invite_email}, but you signed in as {email}.",
                "Use Switch account below, then open the invite link from your email again.",
            )
        request.session["owner_email"] = email
        app_mode = request.session.get("app_mode") or "owner"
        dest = "/?mode=shared"
        if invite_email:
            dest = f"{dest}&email={quote(invite_email)}"
        if app_mode != "shared":
            dest = "/"
        return RedirectResponse(dest)
    except Exception as exc:
        return _oauth_error_page("Google sign-in failed", str(exc), _oauth_redirect_hint(request))


@app.get("/auth/recipient/login")
def recipient_login(request: Request):
    email = _owner_email(request)
    invite_email = _normalize_invite_email(
        request.session.get(INVITE_RECIPIENT_SESSION_KEY)
    )
    login_hint = invite_email or email
    return RedirectResponse(
        recipient_authorization_url(request, state=email, login_hint=login_hint)
    )


@app.get("/auth/recipient/callback")
def recipient_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    if error or not code:
        return _oauth_error_page("Connect failed", error or "no code")
    base_url = request.session.get(OAUTH_BASE_SESSION_KEY) or request_base_url(request)
    try:
        session_email = str(request.session.get("owner_email") or "").strip().lower()
        _, email = recipient_exchange_code(code, base_url)
        invite_email = _normalize_invite_email(
            request.session.get(INVITE_RECIPIENT_SESSION_KEY)
        )
        if invite_email and email.strip().lower() != invite_email:
            return _oauth_error_page(
                "Connect failed",
                f"This invite is for {invite_email}, but you connected as {email}.",
                "Switch account and connect with the invited Google account.",
            )
        if session_email and session_email != email.strip().lower():
            return _oauth_error_page(
                "Connect failed",
                f"Signed in as {email}, but this browser session is {session_email}. "
                "Sign out, open the invite link again, and connect with the invited account.",
            )
        request.session["owner_email"] = email
        request.session["app_mode"] = "shared"
        app_data_svc.enqueue_pending_pushes_for_recipient(email)
        invite_email = _normalize_invite_email(
            request.session.get(INVITE_RECIPIENT_SESSION_KEY)
        )
        dest = "/?mode=shared"
        if invite_email:
            dest = f"{dest}&email={quote(invite_email)}"
        return RedirectResponse(dest)
    except Exception as exc:
        return _oauth_error_page("Connect failed", str(exc), _oauth_redirect_hint(request))


@app.get("/auth/logout")
def logout(request: Request, mode: str | None = None, email: str | None = None):
    invite_email = _normalize_invite_email(
        email or request.session.get(INVITE_RECIPIENT_SESSION_KEY)
    )
    shared_mode = mode == "shared" or request.session.get("app_mode") == "shared"
    request.session.clear()
    if shared_mode:
        if invite_email:
            request.session["app_mode"] = "shared"
            request.session[INVITE_RECIPIENT_SESSION_KEY] = invite_email
            return RedirectResponse(_shared_login_url(invite_email))
        return RedirectResponse("/auth/owner/login?mode=shared")
    return RedirectResponse("/auth/owner/login")


@app.get("/", response_class=HTMLResponse)
def index(request: Request, mode: str | None = None, email: str | None = None):
    if mode in ("owner", "shared"):
        request.session["app_mode"] = mode
    view_mode = request.session.get("app_mode") or mode or "owner"
    invite_email = _normalize_invite_email(
        email or request.session.get(INVITE_RECIPIENT_SESSION_KEY)
    )
    if mode == "shared" and email:
        invite_email = _normalize_invite_email(email)
        request.session[INVITE_RECIPIENT_SESSION_KEY] = invite_email

    active = _active_owner_email(request)
    if view_mode == "shared":
        if invite_email and active and active != invite_email:
            request.session.clear()
            request.session["app_mode"] = "shared"
            request.session[INVITE_RECIPIENT_SESSION_KEY] = invite_email
            active = None
        if not active:
            return RedirectResponse(_shared_login_url(invite_email))
    elif not active:
        return RedirectResponse("/auth/owner/login")
    if not (FRONTEND_DIR / "index.html").exists():
        return HTMLResponse(
            "<p>Frontend not built. See README in frontend/</p>", status_code=500
        )
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "viewMode": view_mode if view_mode in ("owner", "shared") else "owner",
            "uiBuildTag": UI_BUILD_TAG,
            "inviteRecipientEmail": invite_email,
        },
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@app.post("/api/cfs/provision-owner")
async def cfs_provision_owner(request: Request):
    """Store owner OAuth tokens from CFS desktop and return a browser bridge token."""
    api_key = str(request.headers.get("X-CFS-API-Key") or "").strip()
    if not CFS_API_KEY or api_key != CFS_API_KEY:
        raise HTTPException(403, "Forbidden")
    body = await request.json()
    owner_email = str(body.get("owner_email") or "").strip().lower()
    access_token = str(body.get("access_token") or "")
    refresh_token = str(body.get("refresh_token") or "")
    scopes = body.get("scopes") or []
    client_id = str(body.get("client_id") or GOOGLE_CLIENT_ID).strip()
    client_secret = str(body.get("client_secret") or GOOGLE_CLIENT_SECRET).strip()
    if not owner_email:
        raise HTTPException(400, "owner_email required")
    if not access_token and not refresh_token:
        raise HTTPException(400, "access_token or refresh_token required")
    if isinstance(scopes, str):
        scopes = [item for item in scopes.split() if item]
    if not isinstance(scopes, list):
        raise HTTPException(400, "scopes must be a list")
    if not client_id or not client_secret:
        raise HTTPException(400, "client_id and client_secret required")
    token_dict = {
        "token": access_token,
        "refresh_token": refresh_token,
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": client_id,
        "client_secret": client_secret,
        "scopes": [str(item) for item in scopes if str(item or "").strip()],
    }
    db.save_owner_token(owner_email, token_dict)
    try:
        if access_token:
            info = _fetch_userinfo(access_token)
            verified = str(info.get("email") or "").strip().lower()
            if verified and verified != owner_email:
                raise RuntimeError(
                    f"Google token email {verified} does not match CFS account {owner_email}."
                )
            if not verified:
                raise RuntimeError("Could not verify owner email from CFS access token.")
        else:
            creds = owner_credentials(owner_email)
            if not creds:
                raise RuntimeError("Could not load provisioned owner credentials.")
            profile = get_profile(creds)
            if not str(profile.get("email") or "").strip():
                raise RuntimeError("Provisioned Google token did not return owner email.")
    except Exception as exc:
        return JSONResponse(
            {
                "ok": False,
                "needs_oauth": True,
                "reason": str(exc),
            },
            status_code=409,
        )
    return JSONResponse({"ok": True, "bridge_token": create_bridge_token(owner_email)})


@app.post("/api/cfs/sync-contact-changes")
async def cfs_sync_contact_changes(request: Request):
    """Called by CFS desktop after Upload to push changed contacts to share recipients."""
    api_key = str(request.headers.get("X-CFS-API-Key") or "").strip()
    if not CFS_API_KEY or api_key != CFS_API_KEY:
        raise HTTPException(403, "Forbidden")
    body = await request.json()
    owner_email = str(body.get("owner_email") or "").strip().lower()
    person_ids = body.get("person_ids") or []
    if not owner_email:
        raise HTTPException(400, "owner_email required")
    if not isinstance(person_ids, list):
        raise HTTPException(400, "person_ids must be a list")
    result = await asyncio.to_thread(
        app_data_svc.sync_contact_changes_rpc,
        owner_email,
        [str(item) for item in person_ids if str(item or "").strip()],
    )
    return JSONResponse({"ok": True, "result": result})


@app.post("/api/cfs/rename-shared-group")
async def cfs_rename_shared_group(request: Request):
    """Called by CFS after the all-contacts shared group name changes."""
    api_key = str(request.headers.get("X-CFS-API-Key") or "").strip()
    if not CFS_API_KEY or api_key != CFS_API_KEY:
        raise HTTPException(403, "Forbidden")
    body = await request.json()
    owner_email = str(body.get("owner_email") or "").strip().lower()
    resource_name = str(body.get("resource_name") or "").strip()
    old_name = str(body.get("old_name") or "").strip()
    new_name = str(body.get("new_name") or "").strip()
    if not owner_email:
        raise HTTPException(400, "owner_email required")
    if not new_name:
        raise HTTPException(400, "new_name required")
    result = await asyncio.to_thread(
        app_data_svc.rename_shared_group_rpc,
        owner_email,
        resource_name,
        old_name,
        new_name,
    )
    return JSONResponse({"ok": bool(result.get("ok")), "result": result})


def _kick_push_queue_after_cfs_sync(owner_email: str, person_count: int) -> None:
    from .services.push_worker import kick_push_queue

    logger.info(
        "CFS contact sync for %s (%s contacts); draining share push queue",
        owner_email,
        person_count,
    )
    kick_push_queue(max_jobs=1)


@app.get("/api/cfs/card-repair-status")
async def cfs_card_repair_status(request: Request):
    api_key = str(request.headers.get("X-CFS-API-Key") or "").strip()
    if not CFS_API_KEY or api_key != CFS_API_KEY:
        raise HTTPException(403, "Forbidden")
    from .services.card_repair import card_repair_summary_line, get_card_repair_status

    status = await asyncio.to_thread(get_card_repair_status)
    return JSONResponse(
        {"ok": True, "result": status, "summary": card_repair_summary_line(status)}
    )


@app.post("/api/cfs/card-repair")
async def cfs_card_repair(request: Request):
    api_key = str(request.headers.get("X-CFS-API-Key") or "").strip()
    if not CFS_API_KEY or api_key != CFS_API_KEY:
        raise HTTPException(403, "Forbidden")
    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    resume = bool((body or {}).get("resume"))
    from .services.card_repair import card_repair_summary_line, start_card_repair

    status = await asyncio.to_thread(start_card_repair, resume=resume)
    return JSONResponse(
        {"ok": True, "result": status, "summary": card_repair_summary_line(status)}
    )


@app.post("/api/cfs/kick")
async def cfs_kick(request: Request):
    """Optional queue drain endpoint (kept for manual/ops use).

    Production uses Cloud Scheduler job `share-contacts-kick` every 12 minutes as a
    backup drain. Primary recipient updates still come from CFS Upload →
    `/api/cfs/sync-contact-changes` (which kicks the push queue) and from opening Share.

    If called, this reclaims orphaned jobs on cold start and drains pending push
    jobs inside the request for up to KICK_DRAIN_SECONDS.
    """
    api_key = str(request.headers.get("X-CFS-API-Key") or "").strip()
    if not CFS_API_KEY or api_key != CFS_API_KEY:
        raise HTTPException(403, "Forbidden")
    stats = await asyncio.to_thread(_kick_queue_for_scheduler)
    return JSONResponse({"ok": True, **stats})


def _kick_queue_for_scheduler() -> dict[str, int]:
    import time as _time

    from .config import KICK_DRAIN_SECONDS
    from .services.push_worker import (
        _resume_orphaned_imports,
        run_pending_jobs,
        start_push_worker,
    )

    # Cold start: reclaim running jobs, re-enqueue orphaned imports, start worker.
    start_push_worker()

    # Warm instances skip the startup scan, so a Shared group that still needs
    # a one-shot duplicate repair would sit forever without this.
    try:
        _resume_orphaned_imports()
    except Exception:
        logger.exception("cfs_kick: failed to resume orphaned imports")

    deadline = _time.monotonic() + max(1, KICK_DRAIN_SECONDS)
    processed = 0
    while _time.monotonic() < deadline:
        try:
            db.reclaim_stale_push_jobs()
            if db.count_queued_push_jobs() <= 0:
                break
            results = run_pending_jobs(
                max_jobs=1, wall_seconds=deadline - _time.monotonic()
            )
        except Exception:
            logger.exception("cfs_kick: error draining push queue")
            break
        if not results:
            break
        processed += len(results)

    try:
        remaining = db.count_queued_push_jobs()
    except Exception:
        logger.exception("cfs_kick: failed to count queued push jobs")
        remaining = -1
    return {"processed": processed, "queued": remaining}


@app.post("/api/app-call")
async def app_call(request: Request):
    """JSON bridge compatible with the Vue app's google.script.run calls."""
    owner_email = request.session.get("owner_email")
    if not owner_email:
        return JSONResponse({"ok": False, "error": "Sign in required"}, status_code=401)
    body = await request.json()
    method = body.get("method")
    args = body.get("args") or []

    try:
        result = await asyncio.to_thread(_dispatch, owner_email, method, args)
        return JSONResponse({"ok": True, "result": result})
    except PermissionError as e:
        message = str(e)
        if "sign in" in message.lower() or "not signed in" in message.lower():
            request.session.pop("owner_email", None)
        raise HTTPException(401, str(e)) from e
    except Exception as e:
        message = str(e)
        if "refresh the access token" in message.lower() or "not signed in" in message.lower():
            request.session.pop("owner_email", None)
            return JSONResponse(
                {"ok": False, "error": "Google sign-in expired. Please sign in again."},
                status_code=401,
            )
        if is_rate_limit_error(e):
            return JSONResponse(
                {
                    "ok": False,
                    "error": (
                        "Google is temporarily limiting requests (too many in a short "
                        "time). Please wait about a minute, then tap Refresh."
                    ),
                },
                status_code=429,
            )
        return JSONResponse({"ok": False, "error": message}, status_code=500)


def _dispatch(owner_email: str, method: str, args: list[Any]) -> Any:
    if method == "loadApp":
        view_mode = args[0] if args else "owner"
        recover = bool(args[1]) if len(args) > 1 else False
        return app_data_svc.load_app(owner_email, view_mode, recover=recover)
    if method == "shareContactGroup":
        key = args[0] if args else owner_email
        return app_data_svc.share_contact_group(key, *args[1:])
    if method == "stopShareContactGroup":
        key = args[0] if len(args) > 0 else owner_email
        return app_data_svc.stop_share_contact_group(key, *args[1:])
    if method == "getConnectUrl":
        return app_data_svc.get_connect_url(owner_email)
    if method == "disconnectRecipient":
        return app_data_svc.disconnect_recipient(owner_email)
    if method == "processSharedImportForGroup":
        return app_data_svc.process_shared_import_for_group(
            owner_email, args[0], args[1], args[2] if len(args) > 2 else 200
        )
    if method == "getRecipientSharedGroups":
        return app_data_svc.get_recipient_shared_groups(owner_email)
    if method == "syncSharedGroup":
        key = args[0] if args else owner_email
        return app_data_svc.sync_shared_group(
            key,
            args[1] if len(args) > 1 else "",
            args[2] if len(args) > 2 else 90,
            bool(args[3]) if len(args) > 3 else False,
        )
    if method == "getOwnerGroupSyncProgress":
        return app_data_svc.get_owner_group_sync_progress(owner_email, *args)
    if method == "resetGroupSyncState":
        key = args[0] if len(args) > 1 else owner_email
        resource_name = args[1] if len(args) > 1 else (args[0] if args else "")
        return app_data_svc.reset_group_sync_state(key, resource_name)
    if method == "getSharePushStatus":
        key = args[0] if args else owner_email
        resource_name = args[1] if len(args) > 1 else None
        return app_data_svc.get_share_push_status(key, resource_name)
    if method == "syncContactChanges":
        key = args[0] if args else owner_email
        person_ids = args[1] if len(args) > 1 else []
        return app_data_svc.sync_contact_changes_rpc(key, person_ids)
    if method == "retrySharePush":
        owner_key = args[0] if args else ""
        resource_name = args[1] if len(args) > 1 else ""
        return app_data_svc.retry_share_push(owner_email, owner_key, resource_name)
    if method == "dismissSharedRecipientGroup":
        owner_key = args[0] if args else ""
        resource_name = args[1] if len(args) > 1 else ""
        share_id = args[2] if len(args) > 2 else ""
        created = args[3] if len(args) > 3 else ""
        group_name = args[4] if len(args) > 4 else ""
        return app_data_svc.remove_recipient_shared_group(
            owner_email,
            owner_key,
            resource_name,
            share_id=share_id,
            created=created,
            group_name=group_name,
        )
    if method == "removeRecipientSharedGroup":
        owner_key = args[0] if args else ""
        resource_name = args[1] if len(args) > 1 else ""
        share_id = args[2] if len(args) > 2 else ""
        created = args[3] if len(args) > 3 else ""
        group_name = args[4] if len(args) > 4 else ""
        return app_data_svc.remove_recipient_shared_group(
            owner_email,
            owner_key,
            resource_name,
            share_id=share_id,
            created=created,
            group_name=group_name,
        )
    if method == "listSkippedContactsForOwnerGroup":
        return app_data_svc.list_skipped_contacts_for_owner_group(owner_email, *args)
    if method == "getCardRepairStatus":
        from .services.card_repair import card_repair_summary_line, get_card_repair_status

        status = get_card_repair_status()
        return {"status": status, "summary": card_repair_summary_line(status)}
    if method == "startCardRepair":
        from .services.card_repair import card_repair_summary_line, start_card_repair

        status = start_card_repair()
        return {"status": status, "summary": card_repair_summary_line(status)}
    raise ValueError(f"Unknown method: {method}")


@app.get("/health")
def health():
    return {"status": "ok", "baseUrl": APP_BASE_URL}


# Static assets (css, js, components) — after routes
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
