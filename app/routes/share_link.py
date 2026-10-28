from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from app.services.google_sync_service import is_share_contacts_nav_visible
from app.services.share_auth_bridge_service import build_share_web_app_open_url
from app.services.share_web_app_service import share_web_app_owner_url


router = APIRouter(tags=["share-link"])


@router.get("/share")
def open_share_web_app():
    """Open the share web app using the CFS Google account when possible."""
    if not is_share_contacts_nav_visible():
        return RedirectResponse("/presets/google-connect", status_code=302)
    target = build_share_web_app_open_url() or share_web_app_owner_url()
    return RedirectResponse(target, status_code=302)
