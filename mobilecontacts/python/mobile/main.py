from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from mobile.config import APP_SECRET_KEY, APP_BASE_URL, STATIC_DIR
from mobile.access_session import mobile_is_non_editor
from mobile.routes.auth import router as auth_router
from mobile.routes.contacts import router as contacts_router
from mobile.routes.pending_edits import router as pending_edits_router
from app.services.google_sync_service import is_multi_editor_enabled
from mobile.user_db import is_cloud_data_root, mobile_user_db_session, use_user_database
from mobile.web_templates import templates


class MobileCloudUserDbMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not is_cloud_data_root():
            return await call_next(request)
        owner_email = str(request.session.get("owner_email") or "").strip()
        if not owner_email:
            return await call_next(request)
        path = request.url.path
        if (
            path.endswith("/download-status")
            or path.endswith("/refresh-from-google")
            or path.startswith("/static/")
        ):
            use_user_database(owner_email)
            return await call_next(request)
        with mobile_user_db_session(owner_email):
            return await call_next(request)


app = FastAPI(title="ContactsFreeShare Mobile")
app.add_middleware(MobileCloudUserDbMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=APP_SECRET_KEY,
    https_only=APP_BASE_URL.startswith("https://"),
    same_site="lax",
    max_age=14 * 24 * 60 * 60,
)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")


@app.middleware("http")
async def add_ngrok_skip_browser_warning(request: Request, call_next):
    response = await call_next(request)
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip().lower()
    if ".ngrok" in host:
        response.headers["ngrok-skip-browser-warning"] = "1"
    return response

def _ensure_missing_contact_photo_from_drive(safe_name: str) -> bool:
    """If SQLite points at a Drive-backed photo that is missing locally, download it."""
    from app.database import get_connection
    from app.services.google_sync_service import (
        CONTACT_PHOTO_STATIC_PREFIX,
        _download_shared_contact_photo_from_drive,
        _get_valid_access_token,
        get_contact_photo_storage_dir,
    )

    photo_url = f"{CONTACT_PHOTO_STATIC_PREFIX}{safe_name}"
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT photo_drive_file_id
            FROM contacts
            WHERE photo = ?
              AND TRIM(COALESCE(photo_drive_file_id, '')) != ''
            LIMIT 1
            """,
            (photo_url,),
        ).fetchone()
    if not row:
        return False
    drive_file_id = str(row["photo_drive_file_id"] or "").strip()
    if not drive_file_id:
        return False
    try:
        access_token, _email = _get_valid_access_token()
    except RuntimeError:
        return False
    if not _download_shared_contact_photo_from_drive(access_token, photo_url, drive_file_id):
        return False
    return (get_contact_photo_storage_dir() / safe_name).is_file()


@app.get("/static/uploads/contact_photos/{filename}")
def mobile_contact_photo(request: Request, filename: str):
    owner_email = str(request.session.get("owner_email") or "").strip()
    if not owner_email:
        raise HTTPException(status_code=404, detail="Not found")
    use_user_database(owner_email)
    from app.services.google_sync_service import get_contact_photo_storage_dir

    safe_name = Path(filename).name
    if not safe_name or safe_name != filename:
        raise HTTPException(status_code=404, detail="Not found")
    photo_path = get_contact_photo_storage_dir() / safe_name
    if not photo_path.is_file() and not _ensure_missing_contact_photo_from_drive(safe_name):
        raise HTTPException(status_code=404, detail="Not found")
    if not photo_path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(photo_path)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(auth_router)
app.include_router(contacts_router)
app.include_router(pending_edits_router)


@app.get("/")
def home(request: Request):
    owner_email = str(request.session.get("owner_email") or "").strip()
    if owner_email:
        if mobile_is_non_editor(request):
            return RedirectResponse(url="/m/pending-edits/", status_code=303)
        return RedirectResponse(url="/m/contacts/", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "error": request.query_params.get("error", ""),
            "multi_editor_enabled": is_multi_editor_enabled(),
        },
    )
