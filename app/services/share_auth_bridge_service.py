from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.config import GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET
from app.services.google_sync_service import _get_valid_access_token, _load_google_account, get_share_web_api_key
from app.services.share_web_app_service import share_web_app_url


def _owner_login_url(base_url: str, email: str) -> str:
    normalized = str(email or "").strip().lower()
    if not normalized:
        return f"{base_url.rstrip('/')}/auth/owner/login"
    return f"{base_url.rstrip('/')}/auth/owner/login?email={quote(normalized)}"


def build_share_web_app_open_url() -> str:
    """Open the share web app signed in as the CFS Google account when possible."""
    base_url = share_web_app_url().rstrip("/")
    if not base_url:
        return ""

    account = _load_google_account()
    owner_email = str(account.get("account_email") or "").strip().lower()
    account_status = str(account.get("account_status") or "").strip().lower()
    if account_status != "connected" or not owner_email:
        return base_url

    api_key = get_share_web_api_key()
    if not api_key:
        return _owner_login_url(base_url, owner_email)

    try:
        access_token, verified_email = _get_valid_access_token()
        owner_email = str(verified_email or owner_email).strip().lower()
        account = _load_google_account()
        refresh_token = str(account.get("refresh_token") or "")
        scopes_raw = str(account.get("scopes") or "").strip()
        scopes = scopes_raw.split() if scopes_raw else []
        payload = json.dumps(
            {
                "owner_email": owner_email,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "scopes": scopes,
                "client_id": GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            f"{base_url}/api/cfs/provision-owner",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-CFS-API-Key": api_key,
            },
            method="POST",
        )
        with urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            data = json.loads(body) if body else {}
        bridge_token = str(data.get("bridge_token") or "").strip()
        if bridge_token:
            return f"{base_url}/auth/cfs-bridge?token={quote(bridge_token, safe='')}"
    except HTTPError as exc:
        if exc.code == 409:
            return _owner_login_url(base_url, owner_email)
        if exc.code not in {404, 405}:
            pass
    except (URLError, RuntimeError, ValueError, json.JSONDecodeError):
        pass

    return _owner_login_url(base_url, owner_email)
