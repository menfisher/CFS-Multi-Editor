from datetime import datetime

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import APP_TITLE, ERROR_LOG_PATH, PROJECT_ROOT, STATIC_DIR
from app.database import initialize_database
from app.logging_utils import append_log_block
from app.routes.contacts import router as contacts_router
from app.routes.field_list import router as field_list_router
from app.routes.google_sync import router as google_sync_router
from app.routes.home import router as home_router
from app.routes.meetings import router as meetings_router
from app.routes.presets import router as presets_router


app = FastAPI(title=APP_TITLE)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(home_router)
app.include_router(contacts_router)
app.include_router(field_list_router)
app.include_router(google_sync_router)
app.include_router(meetings_router)
app.include_router(presets_router)


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "img" / "contactsfreeshare-icon.ico")


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
