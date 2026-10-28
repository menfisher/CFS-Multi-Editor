import traceback
from datetime import datetime

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import FileResponse
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.config import APP_TITLE, ERROR_LOG_PATH, STATIC_DIR, UPLOADS_DIR
from app.config import PROJECT_ROOT
from app.database import initialize_database
from app.logging_utils import append_log_block
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


def _write_error_log(request: Request, exc: Exception) -> None:
    error_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    append_log_block(
        ERROR_LOG_PATH,
        f"[{datetime.now().isoformat(timespec='seconds')}] {request.method} {request.url}\n{error_text}",
    )


@app.middleware("http")
async def log_route_exceptions(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as exc:
        _write_error_log(request, exc)
        return PlainTextResponse(
            "Internal server error.\n\n"
            f"Error details were written to:\n{_error_log_display_path()}\n\n"
            "Send the contents of that file back for diagnosis.",
            status_code=500,
        )


@app.exception_handler(Exception)
async def log_unhandled_exception(request: Request, exc: Exception) -> PlainTextResponse:
    _write_error_log(request, exc)
    return PlainTextResponse(
        "Internal server error.\n\n"
        f"Error details were written to:\n{_error_log_display_path()}\n\n"
        "Send the contents of that file back for diagnosis.",
        status_code=500,
    )


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
