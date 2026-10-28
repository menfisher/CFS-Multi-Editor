"""Persist SQLite on local disk; sync to GCS (Cloud Storage FUSE breaks SQLite)."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_upload_timer: threading.Timer | None = None
_upload_lock = threading.Lock()
_bootstrapped = False
_last_gcs_upload_at = 0.0
_pending_upload = False
GCS_UPLOAD_DEBOUNCE_SECONDS = 15.0
GCS_UPLOAD_MIN_INTERVAL_SECONDS = 15.0


def gcs_uri() -> str:
    return str(os.getenv("SHARE_DB_GCS_URI", "") or "").strip()


def local_db_path() -> Path:
    configured = str(os.getenv("SHARE_DB_LOCAL_PATH", "") or "").strip()
    if configured:
        return Path(configured)
    return Path("/tmp/sharecontacts.db")


def _parse_gs_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "gs" or not parsed.netloc or not parsed.path.strip("/"):
        raise ValueError(f"Invalid GCS URI: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def verify_sqlite_database(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size <= 0:
        return False
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        row = conn.execute("PRAGMA quick_check").fetchone()
    except sqlite3.DatabaseError:
        return False
    finally:
        conn.close()
    return row is not None and str(row[0]).strip().lower() == "ok"


def snapshot_sqlite_database(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    source_uri = f"file:{source.resolve()}?mode=ro"
    source_conn = sqlite3.connect(source_uri, uri=True)
    dest_conn = sqlite3.connect(dest)
    try:
        source_conn.backup(dest_conn)
    finally:
        source_conn.close()
        dest_conn.close()


def upload_verified_sqlite_snapshot(snapshot_path: Path, uri: str) -> None:
    if not verify_sqlite_database(snapshot_path):
        raise RuntimeError(f"Refusing to upload share DB snapshot that failed quick_check: {snapshot_path}")

    from google.cloud import storage

    bucket_name, blob_name = _parse_gs_uri(uri)
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    temp_blob_name = f"{blob_name}.upload-{uuid.uuid4().hex}"
    temp_blob = bucket.blob(temp_blob_name)
    temp_blob.upload_from_filename(str(snapshot_path))
    try:
        bucket.copy_blob(temp_blob, bucket, blob_name)
    finally:
        try:
            temp_blob.delete()
        except Exception:
            logger.exception("Failed to delete temporary GCS upload blob %s", temp_blob_name)


def bootstrap_database() -> Path:
    """Copy remote DB to local disk once at process start."""
    global _bootstrapped
    path = local_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    uri = gcs_uri()
    if uri and not _bootstrapped:
        try:
            from google.cloud import storage

            bucket_name, blob_name = _parse_gs_uri(uri)
            client = storage.Client()
            blob = client.bucket(bucket_name).blob(blob_name)
            if blob.exists():
                blob.download_to_filename(str(path))
                if not verify_sqlite_database(path):
                    raise RuntimeError(
                        f"Share DB at {uri} failed integrity check after download."
                    )
                logger.info("Restored share DB from %s", uri)
            elif not path.exists():
                logger.info("No remote share DB yet at %s", uri)
        except Exception:
            logger.exception("Failed to restore share DB from %s", uri)
            raise
        _bootstrapped = True
    return path


def _upload_local_db_to_gcs() -> bool:
    uri = gcs_uri()
    if not uri:
        return False
    path = local_db_path()
    if not path.exists():
        return False
    snapshot_path = path.with_suffix(f".snapshot-{uuid.uuid4().hex}.db")
    try:
        snapshot_sqlite_database(path, snapshot_path)
        upload_verified_sqlite_snapshot(snapshot_path, uri)
        logger.debug("Uploaded share DB to %s", uri)
        return True
    except Exception:
        logger.exception("Failed to upload share DB to %s", uri)
        return False
    finally:
        snapshot_path.unlink(missing_ok=True)


def flush_gcs_upload(*, force: bool = False) -> None:
    """Upload local SQLite to GCS, rate-limited to avoid GCS object mutation 429s."""
    global _upload_timer, _last_gcs_upload_at, _pending_upload
    if not gcs_uri():
        return
    now = time.monotonic()
    if not force and now - _last_gcs_upload_at < GCS_UPLOAD_MIN_INTERVAL_SECONDS:
        schedule_gcs_upload()
        return
    with _upload_lock:
        if _upload_timer is not None:
            _upload_timer.cancel()
            _upload_timer = None
        if _upload_local_db_to_gcs():
            _last_gcs_upload_at = time.monotonic()
            _pending_upload = False


def flush_pending_gcs_upload(*, force: bool = False) -> bool:
    """Upload unsaved writes while the caller still holds CPU.

    Cloud Run throttles CPU between requests, so a debounce timer often never
    fires and the instance dies with progress only on local disk. The next cold
    start then restores an older copy and the import appears to run backwards.
    Call this at the end of a request and on shutdown.
    """
    global _last_gcs_upload_at, _pending_upload
    if not gcs_uri() or not _pending_upload:
        return False
    if (
        not force
        and time.monotonic() - _last_gcs_upload_at < GCS_UPLOAD_MIN_INTERVAL_SECONDS
    ):
        return False
    with _upload_lock:
        if not _pending_upload:
            return False
        if _upload_local_db_to_gcs():
            _last_gcs_upload_at = time.monotonic()
            _pending_upload = False
            return True
    return False


def schedule_gcs_upload(*, flush: bool = False) -> None:
    """Mark local SQLite as needing an upload (best-effort debounce timer too)."""
    global _upload_timer, _pending_upload
    if flush:
        flush_gcs_upload(force=True)
        return
    if not gcs_uri():
        return
    _pending_upload = True

    def _upload() -> None:
        flush_gcs_upload(force=False)

    with _upload_lock:
        if _upload_timer is not None:
            _upload_timer.cancel()
        _upload_timer = threading.Timer(GCS_UPLOAD_DEBOUNCE_SECONDS, _upload)
        _upload_timer.daemon = True
        _upload_timer.start()
