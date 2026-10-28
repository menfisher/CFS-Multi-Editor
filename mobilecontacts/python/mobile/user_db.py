from __future__ import annotations

import shutil
import threading
from contextlib import contextmanager
from pathlib import Path

from app.database import (
    apply_database_migrations,
    get_connection,
    initialize_database,
    set_request_db_path,
    set_request_uploads_dir,
)

from mobile.config import DATA_ROOT, email_slug, user_db_path

# Cloud Run stores SQLite on GCS FUSE, which is very slow and breaks with concurrent
# writers (-journal/-shm out-of-order errors). Keep a per-user copy on local disk and
# sync to GCS while holding a process-wide lock.
# Contact photos live under uploads/ next to the DB; those files must be synced too or
# Cloud Run /tmp loss shows broken "?" avatars after the path is already in SQLite.
_USER_DB_LOCK = threading.RLock()
_USER_WRITE_LOCKS: dict[str, threading.RLock] = {}
_USER_WRITE_LOCKS_GUARD = threading.Lock()
_ACTIVE_WRITERS: dict[str, int] = {}
_JOURNAL_MODE_CONFIGURED: set[str] = set()


def _email_key(email: str) -> str:
    return str(email or "").strip().lower()


def user_db_write_lock(email: str) -> threading.RLock:
    """Serialize long contact imports for one Google account on this instance."""
    key = _email_key(email)
    with _USER_WRITE_LOCKS_GUARD:
        lock = _USER_WRITE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _USER_WRITE_LOCKS[key] = lock
        return lock


def mark_user_db_writer(email: str) -> None:
    _ACTIVE_WRITERS[_email_key(email)] = threading.get_ident()


def clear_user_db_writer(email: str) -> None:
    key = _email_key(email)
    if _ACTIVE_WRITERS.get(key) == threading.get_ident():
        _ACTIVE_WRITERS.pop(key, None)


def _other_thread_writing(email: str) -> bool:
    owner = _ACTIVE_WRITERS.get(_email_key(email))
    return owner is not None and owner != threading.get_ident()


def is_cloud_data_root() -> bool:
    return str(DATA_ROOT) == "/data"


def _local_db_dir(slug: str) -> Path:
    return Path("/tmp/cfs-mobile") / slug


def _local_db_path(slug: str) -> Path:
    return _local_db_dir(slug) / "app.sqlite3"


def _uploads_dir_for(db_path: Path) -> Path:
    return db_path.parent / "uploads"


def _uploads_tree_signature(uploads_dir: Path) -> tuple[tuple[str, int, int], ...]:
    if not uploads_dir.exists():
        return ()
    items: list[tuple[str, int, int]] = []
    for path in sorted(uploads_dir.rglob("*")):
        if not path.is_file():
            continue
        st = path.stat()
        items.append((str(path.relative_to(uploads_dir)), int(st.st_mtime_ns), int(st.st_size)))
    return tuple(items)


def _sync_uploads_tree(source_dir: Path, dest_dir: Path) -> None:
    """Copy files from source to dest when source is newer or dest is missing."""
    if not source_dir.exists():
        return
    dest_dir.mkdir(parents=True, exist_ok=True)
    for source_path in source_dir.rglob("*"):
        if not source_path.is_file():
            continue
        relative = source_path.relative_to(source_dir)
        dest_path = dest_dir / relative
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        if not dest_path.exists() or source_path.stat().st_mtime_ns > dest_path.stat().st_mtime_ns:
            shutil.copy2(source_path, dest_path)


def _ensure_sqlite_journal_mode(db_path: Path) -> None:
    key = str(db_path)
    if key in _JOURNAL_MODE_CONFIGURED:
        return
    with get_connection(db_path) as conn:
        current_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0] or "").lower()
        if current_mode == "wal":
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode = DELETE")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.commit()
    _JOURNAL_MODE_CONFIGURED.add(key)


def _activate_database(db_path: Path, *, email: str = "") -> Path:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    set_request_db_path(db_path)
    set_request_uploads_dir(_uploads_dir_for(db_path))
    other_writer = _other_thread_writing(email)
    if not db_path.exists():
        initialize_database()
    elif not other_writer:
        apply_database_migrations()
    if not other_writer:
        _ensure_sqlite_journal_mode(db_path)
    return db_path


def _pull_cloud_db_to_local(email: str) -> Path:
    slug = email_slug(email)
    cloud_path = user_db_path(email)
    local_path = _local_db_path(slug)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    cloud_path.parent.mkdir(parents=True, exist_ok=True)
    if cloud_path.exists():
        # Never replace a local file another thread is still importing into.
        if _other_thread_writing(email) and local_path.exists():
            return _activate_database(local_path, email=email)
        if not local_path.exists() or cloud_path.stat().st_mtime > local_path.stat().st_mtime:
            shutil.copy2(cloud_path, local_path)
    _sync_uploads_tree(_uploads_dir_for(cloud_path), _uploads_dir_for(local_path))
    return _activate_database(local_path, email=email)


def _push_local_db_to_cloud(email: str) -> None:
    slug = email_slug(email)
    local_path = _local_db_path(slug)
    if not local_path.exists() or _other_thread_writing(email):
        return
    cloud_path = user_db_path(email)
    cloud_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(local_path, cloud_path)
    _sync_uploads_tree(_uploads_dir_for(local_path), _uploads_dir_for(cloud_path))


@contextmanager
def mobile_user_db_session(email: str):
    normalized = str(email or "").strip()
    if not normalized or not is_cloud_data_root():
        if normalized:
            _activate_database(user_db_path(normalized), email=normalized)
        yield user_db_path(normalized) if normalized else None
        return

    with _USER_DB_LOCK:
        local_path = _pull_cloud_db_to_local(normalized)
        start_mtime_ns = local_path.stat().st_mtime_ns if local_path.exists() else 0
        start_uploads = _uploads_tree_signature(_uploads_dir_for(local_path))
    try:
        yield local_path
    finally:
        with _USER_DB_LOCK:
            if _other_thread_writing(normalized):
                return
            db_changed = local_path.exists() and local_path.stat().st_mtime_ns > start_mtime_ns
            uploads_changed = _uploads_tree_signature(_uploads_dir_for(local_path)) != start_uploads
            if db_changed or uploads_changed:
                _push_local_db_to_cloud(normalized)


def use_user_database(email: str) -> Path:
    normalized = str(email or "").strip()
    if not normalized:
        raise ValueError("owner email is required")
    if not is_cloud_data_root():
        return _activate_database(user_db_path(normalized), email=normalized)

    slug = email_slug(normalized)
    local_path = _local_db_path(slug)
    if local_path.exists():
        return _activate_database(local_path, email=normalized)
    return _pull_cloud_db_to_local(normalized)
