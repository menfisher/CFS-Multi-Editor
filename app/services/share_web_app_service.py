from __future__ import annotations

from app.config import DEFAULT_PUBLIC_WEB_URL
from app.services.google_sync_service import get_public_web_url


def share_web_app_url() -> str:
    """Public Share Google Contacts web app (owner + recipient UI), separate from CFS desktop."""
    return get_public_web_url() or DEFAULT_PUBLIC_WEB_URL


def share_web_app_owner_url() -> str:
    return share_web_app_url().rstrip("/")


def share_web_app_recipient_invite_url() -> str:
    return f"{share_web_app_url().rstrip('/')}/?mode=shared"
