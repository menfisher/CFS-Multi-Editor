from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from starlette.requests import Request

from mobile.config import APP_BASE_URL, APP_SECRET_KEY, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, OWNER_SCOPES

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
OAUTH_STATE_SESSION_KEY = "mobile_oauth_state"
OAUTH_REDIRECT_PATH = "/auth/callback"
OAUTH_STATE_MAX_AGE_SECONDS = 15 * 60
OAUTH_EXCHANGE_CACHE_TTL_SECONDS = 10 * 60
_NGROK_HOST_SUFFIXES = (
    ".ngrok-free.dev",
    ".ngrok-free.app",
    ".ngrok.app",
    ".ngrok.io",
    ".ngrok.dev",
)

_OAUTH_EXCHANGE_LOCK = threading.Lock()
_OAUTH_EXCHANGE_CACHE: dict[str, tuple[dict, float]] = {}
_OAUTH_CALLBACK_REDIRECT_CACHE: dict[str, tuple[str, float]] = {}
_OAUTH_CALLBACK_INFLIGHT: dict[str, threading.Event] = {}


def _host_header(request: Request) -> str:
    return (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").strip().split(",")[0].strip()


def _is_public_tunnel_host(host: str) -> bool:
    host_name = host.split(":")[0].lower()
    return any(host_name.endswith(suffix) for suffix in _NGROK_HOST_SUFFIXES)


def request_base_url(request: Request) -> str:
    configured = APP_BASE_URL.rstrip("/")
    if configured.startswith("https://"):
        return configured

    host = _host_header(request)
    host_name = host.split(":")[0].lower() if host else ""
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()

    if host and (_is_public_tunnel_host(host) or forwarded_proto == "https"):
        scheme = "https"
        return f"{scheme}://{host}".rstrip("/")

    return str(request.base_url).rstrip("/")


def redirect_uri(request: Request) -> str:
    return f"{request_base_url(request)}{OAUTH_REDIRECT_PATH}"


def redirect_uri_candidates(request: Request, preferred: str = "") -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    host = _host_header(request)
    host_base = f"https://{host}".rstrip("/") if host and _is_public_tunnel_host(host) else ""
    for value in (
        preferred,
        redirect_uri(request),
        f"{host_base}{OAUTH_REDIRECT_PATH}" if host_base else "",
        f"{APP_BASE_URL.rstrip('/')}{OAUTH_REDIRECT_PATH}",
    ):
        cleaned = str(value or "").strip()
        if cleaned and cleaned not in seen:
            candidates.append(cleaned)
            seen.add(cleaned)
    return candidates


def _code_cache_key(code: str) -> str:
    return hashlib.sha256(str(code or "").encode("utf-8")).hexdigest()


def _prune_oauth_caches(now: float | None = None) -> None:
    current = now or time.time()
    for cache in (_OAUTH_EXCHANGE_CACHE, _OAUTH_CALLBACK_REDIRECT_CACHE):
        expired_keys = [key for key, (_, cached_at) in cache.items() if current - cached_at > OAUTH_EXCHANGE_CACHE_TTL_SECONDS]
        for key in expired_keys:
            cache.pop(key, None)


def cached_callback_redirect(code: str) -> str | None:
    cache_key = _code_cache_key(code)
    with _OAUTH_EXCHANGE_LOCK:
        _prune_oauth_caches()
        cached = _OAUTH_CALLBACK_REDIRECT_CACHE.get(cache_key)
        if cached and time.time() - cached[1] <= OAUTH_EXCHANGE_CACHE_TTL_SECONDS:
            return cached[0]
    return None


def wait_for_callback_redirect(code: str, *, timeout_seconds: float = 180.0) -> str | None:
    cache_key = _code_cache_key(code)
    deadline = time.time() + max(float(timeout_seconds or 0), 1.0)
    while time.time() < deadline:
        cached = cached_callback_redirect(code)
        if cached:
            return cached
        with _OAUTH_EXCHANGE_LOCK:
            event = _OAUTH_CALLBACK_INFLIGHT.get(cache_key)
        if event is None:
            return cached_callback_redirect(code)
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        event.wait(timeout=min(1.0, remaining))
    return cached_callback_redirect(code)


def begin_callback_exchange(code: str) -> tuple[bool, threading.Event | None]:
    """Return (is_owner, wait_event_for_duplicate_requests)."""
    cache_key = _code_cache_key(code)
    with _OAUTH_EXCHANGE_LOCK:
        _prune_oauth_caches()
        cached = _OAUTH_CALLBACK_REDIRECT_CACHE.get(cache_key)
        if cached and time.time() - cached[1] <= OAUTH_EXCHANGE_CACHE_TTL_SECONDS:
            return False, None
        existing = _OAUTH_CALLBACK_INFLIGHT.get(cache_key)
        if existing is not None:
            return False, existing
        event = threading.Event()
        _OAUTH_CALLBACK_INFLIGHT[cache_key] = event
        return True, event


def finish_callback_exchange(code: str, redirect_url: str | None, event: threading.Event | None) -> None:
    cache_key = _code_cache_key(code)
    with _OAUTH_EXCHANGE_LOCK:
        if redirect_url:
            _OAUTH_CALLBACK_REDIRECT_CACHE[cache_key] = (redirect_url, time.time())
        _OAUTH_CALLBACK_INFLIGHT.pop(cache_key, None)
    if event is not None:
        event.set()


def _encode_oauth_state(payload: dict) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")
    signature = hmac.new(APP_SECRET_KEY.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    return f"{raw}.{signature}"


def decode_oauth_state(state: str) -> dict:
    cleaned = str(state or "").strip()
    if not cleaned or "." not in cleaned:
        return {}
    raw, signature = cleaned.rsplit(".", 1)
    expected = hmac.new(APP_SECRET_KEY.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    if not hmac.compare_digest(signature, expected):
        return {}
    padded = raw + ("=" * ((4 - len(raw) % 4) % 4))
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _validate_oauth_state(request: Request, state: str) -> dict:
    """Validate signed OAuth state.

    Mobile Safari often drops session cookies on the Google OAuth round trip, so
    the HMAC on the state parameter is the primary CSRF check. A stale session
    nonce from an earlier attempt is ignored when the signed state is valid.
    """
    state_payload = decode_oauth_state(state)
    nonce = str(state_payload.get("nonce") or "").strip()
    if not nonce:
        raise RuntimeError(
            "Google OAuth state was invalid. Go back to the home page and tap Sign in with role again."
        )

    session_nonce = str(request.session.pop(OAUTH_STATE_SESSION_KEY, "") or "").strip()
    if session_nonce and not hmac.compare_digest(session_nonce, nonce):
        pass

    issued_at = state_payload.get("iat")
    if issued_at is not None:
        age_seconds = time.time() - int(issued_at)
        if age_seconds > OAUTH_STATE_MAX_AGE_SECONDS:
            raise RuntimeError(
                "Google sign-in timed out. Go back to the home page and tap Sign in with role again."
            )

    return state_payload


def authorization_url(
    request: Request,
    *,
    access_role: str = "",
    access_name: str = "",
    editor_secret: str = "",
) -> str:
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise RuntimeError("Google OAuth is not configured for the mobile app.")
    nonce = secrets.token_urlsafe(16)
    state_payload = {"nonce": nonce, "iat": int(time.time())}
    normalized_role = str(access_role or "").strip()
    normalized_name = str(access_name or "").strip()
    oauth_redirect_uri = redirect_uri(request)
    if normalized_role and normalized_name:
        state_payload["access_role"] = normalized_role
        state_payload["access_name"] = normalized_name
    if normalized_role == "editor" and str(editor_secret or "").strip():
        state_payload["editor_secret"] = str(editor_secret).strip()
    state_payload["redirect_uri"] = oauth_redirect_uri
    state = _encode_oauth_state(state_payload)
    request.session[OAUTH_STATE_SESSION_KEY] = nonce
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": oauth_redirect_uri,
        "response_type": "code",
        "scope": " ".join(OWNER_SCOPES),
        "access_type": "offline",
        "include_granted_scopes": "false",
        "prompt": "select_account consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def _utcnow_text(expires_in: int) -> str:
    expiry = datetime.now(timezone.utc) + timedelta(seconds=max(int(expires_in) - 60, 60))
    return expiry.isoformat()


def _oauth_redirect_uri_for_exchange(request: Request, state_payload: dict) -> str:
    stored_redirect_uri = str(state_payload.get("redirect_uri") or "").strip()
    if stored_redirect_uri:
        return stored_redirect_uri
    return redirect_uri(request)


def _exchange_authorization_code(request: Request, code: str, oauth_redirect_uri: str) -> dict:
    response = httpx.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": oauth_redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    if response.status_code >= 400:
        detail = response.text[:500]
        lowered = detail.lower()
        if "redirect_uri_mismatch" in lowered:
            raise RuntimeError(
                "Google redirect URI mismatch. In Google Cloud Console → OAuth client → "
                f"Authorized redirect URIs, add this exact URL: {oauth_redirect_uri}"
            )
        if "invalid_grant" in lowered:
            raise RuntimeError(
                "That Google sign-in link expired or was already used. "
                "Go back to the home page and tap Sign in with role again. "
                "Do not refresh the Google callback page or use your browser back button."
            )
        raise RuntimeError(
            "Google token exchange failed. "
            f"Redirect URI used: {oauth_redirect_uri}. Details: {detail}"
        )
    payload = response.json()
    access_token = str(payload.get("access_token") or "")
    if not access_token:
        raise RuntimeError("Google token exchange did not return an access token.")
    return payload


def exchange_code(request: Request, code: str, state: str) -> dict:
    state_payload = _validate_oauth_state(request, state)
    cache_key = _code_cache_key(code)
    with _OAUTH_EXCHANGE_LOCK:
        _prune_oauth_caches()
        cached = _OAUTH_EXCHANGE_CACHE.get(cache_key)
        if cached and time.time() - cached[1] <= OAUTH_EXCHANGE_CACHE_TTL_SECONDS:
            return dict(cached[0])

    oauth_redirect_uri = _oauth_redirect_uri_for_exchange(request, state_payload)
    token_data = _exchange_authorization_code(request, code, oauth_redirect_uri)
    access_token = str(token_data.get("access_token") or "")
    userinfo = httpx.get(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    userinfo.raise_for_status()
    profile = userinfo.json()
    email = str(profile.get("email") or "").strip()
    if not email:
        raise RuntimeError("Google account email was not returned.")
    result = {
        "email": email,
        "google_user_id": str(profile.get("sub") or "").strip(),
        "access_token": access_token,
        "refresh_token": str(token_data.get("refresh_token") or ""),
        "token_expiry": _utcnow_text(int(token_data.get("expires_in") or 3600)),
        "scopes": str(token_data.get("scope") or ""),
        "oauth_state": state_payload,
    }
    with _OAUTH_EXCHANGE_LOCK:
        _OAUTH_EXCHANGE_CACHE[cache_key] = (result, time.time())
    return result


def save_account_tokens(email: str, token_payload: dict) -> None:
    from app.database import get_connection
    from app.services.google_sync_service import _now_text, ensure_google_sync_records

    ensure_google_sync_records()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE google_sync_accounts
            SET
              account_email = ?,
              remember_preferred_account = 1,
              account_status = 'connected',
              google_user_id = ?,
              google_display_name = '',
              access_token = ?,
              refresh_token = ?,
              token_expiry = ?,
              scopes = ?,
              updated_at = ?
            WHERE id = 1
            """,
            (
                email,
                token_payload.get("google_user_id") or "",
                token_payload.get("access_token") or "",
                token_payload.get("refresh_token") or "",
                token_payload.get("token_expiry") or "",
                token_payload.get("scopes") or "",
                _now_text(),
            ),
        )
        conn.execute(
            """
            UPDATE google_sync_state
            SET
              pending_oauth_state = '',
              pending_oauth_state_created_at = '',
              last_sync_error = '',
              sync_enabled = CASE WHEN multi_editor_enabled = 1 THEN sync_enabled ELSE 1 END,
              editor_mode = CASE WHEN multi_editor_enabled = 1 THEN 'no_edit' ELSE 'normal' END,
              updated_at = ?
            WHERE id = 1
            """,
            (_now_text(),),
        )
        conn.commit()
