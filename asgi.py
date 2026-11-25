import html
from datetime import datetime

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import FileResponse
from fastapi.responses import HTMLResponse
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.config import APP_TITLE, ERROR_LOG_PATH, STATIC_DIR, UPLOADS_DIR
from app.config import PROJECT_ROOT
from app.database import initialize_database
from app.logging_utils import append_log_block, format_error_log_block
from app.network_errors import describe_request_error, user_facing_request_error
from app.routes.address_book import router as address_book_router
from app.routes.contacts import router as contacts_router
from app.routes.field_list import router as field_list_router
from app.routes.google_sync import router as google_sync_router
from app.routes.home import router as home_router
from app.routes.meetings import router as meetings_router
from app.routes.presets import router as presets_router
from app.routes.share_link import router as share_link_router
from app.services.google_sync_service import start_orphaned_address_book_pdf_cleanup_job


app = FastAPI(title=APP_TITLE)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(home_router)
app.include_router(address_book_router)
app.include_router(contacts_router)
app.include_router(field_list_router)
app.include_router(google_sync_router)
app.include_router(meetings_router)
app.include_router(presets_router)
app.include_router(share_link_router)


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "img" / "contactsfreeshare-icon.ico")


def _error_log_display_path() -> str:
    return str(ERROR_LOG_PATH.resolve())


def _write_error_log(request: Request, exc: Exception) -> str:
    block = format_error_log_block(
        method=request.method,
        url=str(request.url),
        exc=exc,
    )
    append_log_block(ERROR_LOG_PATH, block)
    return block


def _server_error_response(request: Request, exc: Exception) -> HTMLResponse | PlainTextResponse:
    timestamp = datetime.now().isoformat(timespec="seconds")
    _write_error_log(request, exc)
    friendly = user_facing_request_error(exc)
    technical = describe_request_error(exc)
    log_path = _error_log_display_path()
    plain_body = (
        "ContactsFreeShare ran into a problem.\n\n"
        f"When: {timestamp}\n"
        f"What happened: {friendly}\n"
        f"Technical details: {technical}\n\n"
        f"Full traceback: {log_path}\n"
    )
    accept = str(request.headers.get("accept") or "")
    if "text/html" in accept and "application/json" not in accept:
        html_body = f"""
        <!doctype html>
        <html>
          <head>
            <meta charset="utf-8">
            <title>ContactsFreeShare Error</title>
          </head>
          <body style="font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif; margin: 0; background: #f7f4ee; color: #20292f;">
            <main style="max-width: 42rem; margin: 4rem auto; padding: 2rem; background: white; border: 1px solid #d7d1c6; border-radius: 12px;">
              <h1 style="margin-top: 0;">Something went wrong</h1>
              <p><strong>When:</strong> {html.escape(timestamp)}</p>
              <p><strong>What happened:</strong> {html.escape(friendly)}</p>
              <p><strong>Technical details:</strong> <code>{html.escape(technical)}</code></p>
              <p class="muted">The full traceback was saved to:</p>
              <p><code>{html.escape(log_path)}</code></p>
              <p><a href="/">Return to ContactsFreeShare</a></p>
            </main>
          </body>
        </html>
        """
        return HTMLResponse(html_body, status_code=500)
    return PlainTextResponse(plain_body, status_code=500)


@app.middleware("http")
async def log_route_exceptions(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as exc:
        return _server_error_response(request, exc)


@app.exception_handler(Exception)
async def log_unhandled_exception(request: Request, exc: Exception) -> HTMLResponse | PlainTextResponse:
    return _server_error_response(request, exc)


@app.on_event("startup")
def on_startup() -> None:
    build_info_path = PROJECT_ROOT / "BUILD_INFO.txt"
    build_info = build_info_path.read_text(encoding="utf-8").strip() if build_info_path.exists() else "development build"
    startup_lines = [
        f"[{datetime.now().isoformat(timespec='seconds')}] ContactsFreeShare startup",
        f"[{datetime.now().isoformat(timespec='seconds')}] Build info: {build_info}",
    ]
    append_log_block(ERROR_LOG_PATH, "\n".join(startup_lines))
    initialize_database()
    start_orphaned_address_book_pdf_cleanup_job()
