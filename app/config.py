from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = PROJECT_ROOT / "app"
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"
SCHEMA_DIR = PROJECT_ROOT / "db"
SCHEMA_PATH = SCHEMA_DIR / "schema.sql"
MEETING_V2_SCHEMA_PATH = SCHEMA_DIR / "meeting_layout_v2.sql"

APP_TITLE = "Contacts Website"
HOST = "127.0.0.1"
PORT = 8000


def _raise_open_file_limit() -> None:
    if os.name == "nt":
        return
    try:
        import resource

        soft_limit, hard_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
        target = min(max(soft_limit, 4096), hard_limit)
        if target > soft_limit:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard_limit))
    except (ImportError, OSError, ValueError):
        pass


_raise_open_file_limit()


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _default_app_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ContactsFreeShare"
    if os.name == "nt":
        root = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(root) / "ContactsFreeShare"
    return Path(os.getenv("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "ContactsFreeShare"


def _is_portable_build() -> bool:
    return (PROJECT_ROOT / "BUILD_INFO.txt").exists()


def _candidate_app_data_dirs() -> list[Path]:
    candidates: list[Path] = []
    configured = os.getenv("CONTACTSFREESHARE_DATA_DIR")
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.append(_default_app_data_dir())
    candidates.append(Path.home() / "ContactsFreeShareData")
    if _is_portable_build():
        candidates.append(PROJECT_ROOT / "runtime")
    candidates.append(Path(tempfile.gettempdir()) / "ContactsFreeShare")

    unique_candidates: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique_candidates.append(candidate)
    return unique_candidates


def _select_writable_app_data_dir() -> Path:
    configured = os.getenv("CONTACTSFREESHARE_DATA_DIR")
    if configured:
        candidate = Path(configured).expanduser()
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except OSError as exc:
            raise RuntimeError(f"Configured ContactsFreeShare data directory is not writable: {candidate}") from exc
        return candidate
    for candidate in _candidate_app_data_dirs():
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return candidate
        except OSError:
            continue
    return _default_app_data_dir()


def _sqlite_value(db_path: Path, sql: str) -> int:
    if not db_path.is_file():
        return 0
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(sql).fetchone()
            return int(row[0] or 0) if row else 0
    except sqlite3.Error:
        return 0


def _runtime_db_score(runtime_dir: Path) -> int:
    db_path = runtime_dir / "db" / "app.sqlite3"
    contact_count = _sqlite_value(db_path, "SELECT COUNT(*) FROM contacts")
    contacts_revision = _sqlite_value(db_path, "SELECT contacts_sync_revision FROM google_sync_state WHERE id = 1")
    return contacts_revision * 100000 + contact_count


def _copy_runtime_subtree(source_dir: Path, target_dir: Path, name: str) -> None:
    source = source_dir / name
    if not source.exists():
        return
    target = target_dir / name
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _migrate_legacy_portable_runtime(app_data_dir: Path) -> None:
    legacy_runtime_dir = PROJECT_ROOT / "runtime"
    try:
        if (
            not _is_portable_build()
            or legacy_runtime_dir.resolve() == app_data_dir.resolve()
            or not (legacy_runtime_dir / "db" / "app.sqlite3").is_file()
        ):
            return
    except OSError:
        return

    if _runtime_db_score(legacy_runtime_dir) <= _runtime_db_score(app_data_dir):
        return

    for name in ("db", "imports", "uploads", "logs"):
        _copy_runtime_subtree(legacy_runtime_dir, app_data_dir, name)


USE_APP_DATA = _truthy(os.getenv("CONTACTSFREESHARE_USE_APP_DATA")) or _is_portable_build()
APP_DATA_DIR = _select_writable_app_data_dir() if USE_APP_DATA else Path(os.getenv("CONTACTSFREESHARE_DATA_DIR") or _default_app_data_dir()).expanduser()
if USE_APP_DATA:
    _migrate_legacy_portable_runtime(APP_DATA_DIR)
RUNTIME_ROOT = APP_DATA_DIR if USE_APP_DATA else PROJECT_ROOT
DB_DIR = RUNTIME_ROOT / "db"
IMPORTS_DIR = RUNTIME_ROOT / "imports"
UPLOADS_DIR = RUNTIME_ROOT / "uploads"
DB_PATH = DB_DIR / "app.sqlite3"
ERROR_LOG_PATH = RUNTIME_ROOT / "logs" / "errors.log"


def _load_dotenv() -> None:
    dotenv_paths = [PROJECT_ROOT / ".env"]
    if USE_APP_DATA:
        dotenv_paths.append(APP_DATA_DIR / ".env")

    for dotenv_path in dotenv_paths:
        if not dotenv_path.exists():
            continue
        for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()

# Mobile/share Cloud Run use GOOGLE_CLIENT_*; desktop uses GOOGLE_OAUTH_CLIENT_*.
# Accept either so token refresh in shared google_sync_service works in all hosts.
GOOGLE_OAUTH_CLIENT_ID = (
    os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip() or os.getenv("GOOGLE_CLIENT_ID", "").strip()
)
GOOGLE_OAUTH_CLIENT_SECRET = (
    os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip() or os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
)
GOOGLE_OAUTH_REDIRECT_URI = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", f"http://{HOST}:{PORT}/google/connect/callback").strip()
GOOGLE_PICKER_API_KEY = os.getenv("GOOGLE_PICKER_API_KEY", "").strip()
GOOGLE_PICKER_APP_ID = os.getenv("GOOGLE_PICKER_APP_ID", "").strip()
APP_BASE_URL = os.getenv("APP_BASE_URL", f"http://{HOST}:{PORT}").strip().rstrip("/")
# Default URL for the separate Share Google Contacts web app (opened from CFS nav).
# share.contactsfreeshare.org is optional; use the live Cloud Run URL until DNS is mapped.
DEFAULT_PUBLIC_WEB_URL = "https://share-contacts-7frzetx7sa-uc.a.run.app"
LEGACY_PLACEHOLDER_SHARE_WEB_URL = "https://share.contactsfreeshare.org"
PUBLIC_WEB_URL = os.getenv("PUBLIC_WEB_URL", DEFAULT_PUBLIC_WEB_URL).strip().rstrip("/")
SHARE_RECIPIENT_WEB_PORT = int(os.getenv("SHARE_RECIPIENT_WEB_PORT", "8080"))
# Same value as share app APP_SECRET_KEY / CFS_API_KEY (enables post-upload recipient sync).
SHARE_WEB_API_KEY = os.getenv("SHARE_WEB_API_KEY", os.getenv("CFS_API_KEY", "")).strip()


def is_local_web_url(url: str) -> bool:
    value = str(url or "").strip().lower()
    if not value:
        return True
    return value.startswith(("http://127.0.0.1", "http://localhost", "https://127.0.0.1", "https://localhost"))


def is_placeholder_share_web_url(url: str) -> bool:
    """True when the URL is empty, local-only, or the unmapped legacy placeholder domain."""
    value = str(url or "").strip().rstrip("/")
    if not value or is_local_web_url(value):
        return True
    from urllib.parse import urlparse

    host = (urlparse(value).hostname or "").lower()
    return host == "share.contactsfreeshare.org"
