from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from starlette.requests import Request

from . import db
from .config import (
    APP_BASE_URL,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    OWNER_SCOPES,
    RECIPIENT_SCOPES,
)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

OAUTH_BASE_SESSION_KEY = "oauth_base_url"


def _utcnow_naive() -> datetime:
    """google-auth compares expiry against naive UTC datetimes."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalize_expiry(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def request_base_url(request: Request) -> str:
    """Use the host the browser actually used (localhost vs 127.0.0.1 matters for Google OAuth).

    Cloud Run terminates TLS at the load balancer, so request.base_url is often http://
    even when the user opened https://. Prefer APP_BASE_URL when it is configured with https.
    """
    configured = APP_BASE_URL.rstrip("/")
    if configured.startswith("https://"):
        return configured
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    if forwarded_proto == "https":
        host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").strip()
        if host:
            return f"https://{host.split(',', 1)[0].strip()}".rstrip("/")
    return str(request.base_url).rstrip("/")


def _redirect_uri(base_url: str, redirect_path: str) -> str:
    return f"{base_url.rstrip('/')}{redirect_path}"


def _redirect_uri_candidates(base_url: str, redirect_path: str) -> list[str]:
    primary = _redirect_uri(base_url, redirect_path)
    seen = {primary}
    out = [primary]
    if "127.0.0.1" in primary:
        alt = primary.replace("127.0.0.1", "localhost")
    elif "localhost" in primary:
        alt = primary.replace("localhost", "127.0.0.1")
    else:
        alt = ""
    if alt and alt not in seen:
        out.append(alt)
        seen.add(alt)
    fallback = _redirect_uri(APP_BASE_URL, redirect_path)
    if fallback not in seen:
        out.append(fallback)
    return out


def _auth_url(
    scopes: list[str],
    redirect_path: str,
    base_url: str,
    state: str | None = None,
    login_hint: str | None = None,
    prompt: str | None = None,
) -> str:
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise RuntimeError(
            "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in python/.env "
            "(copy from ContactsFreeShare runtime .env)."
        )
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": _redirect_uri(base_url, redirect_path),
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "include_granted_scopes": "false",
        "prompt": prompt or ("select_account" if login_hint else "consent"),
        "state": state or secrets.token_urlsafe(16),
    }
    hinted_email = str(login_hint or "").strip()
    if hinted_email:
        params["login_hint"] = hinted_email
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def _exchange_code(code: str, redirect_path: str, base_url: str) -> dict[str, Any]:
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise RuntimeError("Google OAuth is not configured.")

    errors: list[str] = []
    for redirect_uri in _redirect_uri_candidates(base_url, redirect_path):
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(
                    GOOGLE_TOKEN_URL,
                    data={
                        "code": code,
                        "client_id": GOOGLE_CLIENT_ID,
                        "client_secret": GOOGLE_CLIENT_SECRET,
                        "redirect_uri": redirect_uri,
                        "grant_type": "authorization_code",
                    },
                )
        except httpx.HTTPError as exc:
            errors.append(f"{redirect_uri}: network error {exc}")
            continue

        if resp.status_code < 400:
            data = resp.json()
            if data.get("access_token"):
                return data
            errors.append(f"{redirect_uri}: missing access_token in response")
            continue

        detail = resp.text[:300]
        errors.append(f"{redirect_uri}: {resp.status_code} {detail}")
        if "redirect_uri_mismatch" not in detail and "invalid_grant" not in detail:
            break

    tried = "; ".join(_redirect_uri_candidates(base_url, redirect_path))
    raise RuntimeError(
        "Google token exchange failed. Tried redirect URIs: "
        f"{tried}. Details: {' | '.join(errors)}"
    )


def _fetch_userinfo(access_token: str) -> dict[str, Any]:
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Could not read Google profile ({resp.status_code}).")
    return resp.json()


def _token_response_to_creds(token_data: dict[str, Any], scopes: list[str]) -> Credentials:
    scope_text = str(token_data.get("scope") or "")
    granted = scope_text.split() if scope_text else scopes
    expiry = None
    expires_in = token_data.get("expires_in")
    if expires_in is not None:
        try:
            expiry = _utcnow_naive() + timedelta(seconds=int(expires_in))
        except (TypeError, ValueError):
            expiry = None
    return Credentials(
        token=token_data.get("access_token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=GOOGLE_TOKEN_URL,
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=granted,
        expiry=expiry,
    )


def _creds_to_dict(creds: Credentials) -> dict[str, Any]:
    payload = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or []),
    }
    if creds.expiry:
        payload["expiry"] = _normalize_expiry(creds.expiry).isoformat()
    return payload


def _parse_expiry(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _dict_to_creds(data: dict[str, Any]) -> Credentials:
    return Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri", GOOGLE_TOKEN_URL),
        client_id=data.get("client_id", GOOGLE_CLIENT_ID),
        client_secret=data.get("client_secret", GOOGLE_CLIENT_SECRET),
        scopes=data.get("scopes"),
        expiry=_parse_expiry(data.get("expiry")),
    )


def _normalize_stored_creds(data: dict[str, Any]) -> Credentials:
    creds = _dict_to_creds(data)
    if not creds.client_id:
        creds.client_id = GOOGLE_CLIENT_ID
    if not creds.client_secret:
        creds.client_secret = GOOGLE_CLIENT_SECRET
    if not creds.token_uri:
        creds.token_uri = GOOGLE_TOKEN_URL
    creds.expiry = _normalize_expiry(creds.expiry)
    return creds


def is_oauth_refresh_error(exc: BaseException) -> bool:
    message = str(exc or "").lower()
    return (
        "refresh the access token" in message
        or "invalid_grant" in message
        or "token has been expired or revoked" in message
    )


def clear_recipient_connection(email: str) -> None:
    db.delete_recipient_token(email)


def _credentials_usable(creds: Credentials, *, require_refresh_token: bool) -> bool:
    if require_refresh_token and not creds.refresh_token:
        return False
    if not creds.token and not creds.refresh_token:
        return False
    if creds.expired and not creds.refresh_token:
        return False
    if creds.refresh_token and not (
        creds.token_uri and creds.client_id and creds.client_secret
    ):
        return False
    return True


def _refresh_if_needed(
    creds: Credentials, *, require_refresh_token: bool
) -> Credentials | None:
    if not _credentials_usable(creds, require_refresh_token=require_refresh_token):
        return None
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleAuthRequest())
        except Exception:
            return None
    return creds


def _load_credentials(
    email: str,
    *,
    get_token,
    save_token,
    delete_token,
    require_refresh_token: bool = False,
) -> Credentials | None:
    raw = get_token(email)
    if not raw:
        return None
    creds = _refresh_if_needed(
        _normalize_stored_creds(raw),
        require_refresh_token=require_refresh_token,
    )
    if creds is None:
        delete_token(email)
        return None
    save_token(email, _creds_to_dict(creds))
    return creds


def _email_from_token_data(token_data: dict[str, Any]) -> str:
    info = _fetch_userinfo(str(token_data["access_token"]))
    email = info.get("email")
    if not email:
        raise RuntimeError("Could not read Google account email.")
    return str(email).strip().lower()


def owner_authorization_url(
    request: Request,
    login_hint: str | None = None,
    prompt: str | None = None,
) -> str:
    base = request_base_url(request)
    request.session[OAUTH_BASE_SESSION_KEY] = base
    return _auth_url(
        OWNER_SCOPES,
        "/auth/owner/callback",
        base,
        login_hint=login_hint,
        prompt=prompt,
    )


def owner_exchange_code(code: str, base_url: str) -> tuple[Credentials, str]:
    token_data = _exchange_code(code, "/auth/owner/callback", base_url)
    email = _email_from_token_data(token_data)
    creds = _token_response_to_creds(token_data, OWNER_SCOPES)
    creds.expiry = _normalize_expiry(creds.expiry)
    existing = db.get_owner_token(email) or {}
    if not creds.refresh_token and existing.get("refresh_token"):
        creds.refresh_token = existing.get("refresh_token")
    db.save_owner_token(email, _creds_to_dict(creds))
    return creds, email


def recipient_authorization_url(
    request: Request, state: str | None = None, login_hint: str | None = None
) -> str:
    base = request_base_url(request)
    request.session[OAUTH_BASE_SESSION_KEY] = base
    return _auth_url(
        RECIPIENT_SCOPES,
        "/auth/recipient/callback",
        base,
        state=state,
        login_hint=login_hint,
        prompt="consent",
    )


def recipient_exchange_code(code: str, base_url: str) -> tuple[Credentials, str]:
    token_data = _exchange_code(code, "/auth/recipient/callback", base_url)
    email = _email_from_token_data(token_data)
    creds = _token_response_to_creds(token_data, RECIPIENT_SCOPES)
    creds.expiry = _normalize_expiry(creds.expiry)
    existing = db.get_recipient_token(email) or {}
    if not creds.refresh_token and existing.get("refresh_token"):
        creds.refresh_token = existing.get("refresh_token")
    db.save_recipient_token(email, _creds_to_dict(creds))
    return creds, email


def owner_credentials(email: str) -> Credentials | None:
    return _load_credentials(
        email,
        get_token=db.get_owner_token,
        save_token=db.save_owner_token,
        delete_token=db.delete_owner_token,
    )


def recipient_credentials(email: str) -> Credentials | None:
    return _load_credentials(
        email,
        get_token=db.get_recipient_token,
        save_token=db.save_recipient_token,
        delete_token=db.delete_recipient_token,
        require_refresh_token=True,
    )
