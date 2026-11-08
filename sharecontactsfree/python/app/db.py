from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATABASE_PATH as _CONFIGURED_DATABASE_PATH

_db_lock = threading.RLock()
_active_database_path: Path | None = None


def resolve_database_path() -> Path:
    global _active_database_path
    if _active_database_path is None:
        from . import db_storage

        if db_storage.gcs_uri():
            _active_database_path = db_storage.local_db_path()
        else:
            _active_database_path = _CONFIGURED_DATABASE_PATH
    return _active_database_path


def _commit(conn: sqlite3.Connection, *, flush: bool = False) -> None:
    conn.commit()
    try:
        from .db_storage import schedule_gcs_upload

        schedule_gcs_upload(flush=flush)
    except Exception:
        pass

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_data (
    email TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shared_contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    share_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    group_name TEXT NOT NULL,
    group_resource_name TEXT NOT NULL,
    member_index INTEGER NOT NULL,
    member_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_shared_contacts_share_id ON shared_contacts(share_id);

CREATE TABLE IF NOT EXISTS sync_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    share_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    group_resource_name TEXT NOT NULL,
    recipient_email TEXT NOT NULL,
    owner_person_id TEXT NOT NULL,
    recipient_person_id TEXT,
    last_hash TEXT,
    last_photo_hash TEXT,
    last_sync TEXT,
    owner_etag TEXT,
    last_assignment_label TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS recipient_oauth (
    email TEXT PRIMARY KEY,
    token_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS owner_oauth (
    email TEXT PRIMARY KEY,
    token_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS share_push_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL,
    resource_name TEXT NOT NULL DEFAULT '',
    recipient_email TEXT NOT NULL DEFAULT '',
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    payload_json TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sync_mappings_owner_person
ON sync_mappings (owner, owner_person_id);

CREATE INDEX IF NOT EXISTS idx_sync_mappings_scope
ON sync_mappings (share_id, owner, group_resource_name, recipient_email);

CREATE INDEX IF NOT EXISTS idx_share_push_jobs_status
ON share_push_jobs (status, updated_at);
"""


def _migrate_sync_mappings(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the initial schema without dropping existing data."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(sync_mappings)").fetchall()}
    if "last_assignment_label" not in columns:
        conn.execute(
            "ALTER TABLE sync_mappings ADD COLUMN last_assignment_label TEXT DEFAULT ''"
        )


def init_db() -> None:
    from . import db_storage

    db_storage.bootstrap_database()
    path = resolve_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate_sync_mappings(conn)
        _commit(conn)


def _use_delete_journal_mode() -> bool:
    """Cloud Storage FUSE mounts reject SQLite WAL sidecar files (disk I/O error)."""
    try:
        path = str(resolve_database_path())
        return path.startswith("/data") or path.startswith("/tmp")
    except Exception:
        return False


@contextmanager
def connect():
    with _db_lock:
        conn = sqlite3.connect(resolve_database_path(), timeout=60)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 60000")
        if _use_delete_journal_mode():
            try:
                conn.execute("PRAGMA journal_mode = DELETE")
            except sqlite3.OperationalError:
                pass
        else:
            try:
                conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.OperationalError:
                pass
        try:
            yield conn
        finally:
            conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_email(email: str) -> str:
    return str(email or "").strip().lower()


def get_app_data(email: str) -> dict[str, Any] | None:
    key = normalize_email(email)
    with connect() as conn:
        row = conn.execute(
            "SELECT data_json FROM app_data WHERE email = ?", (key,)
        ).fetchone()
    if not row:
        return None
    raw = str(row["data_json"] or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def save_app_data(email: str, data: dict[str, Any], *, flush: bool = False) -> None:
    key = normalize_email(email)
    if not isinstance(data, dict) or not data:
        existing = get_app_data(email)
        if existing:
            return
    payload = json.dumps(data)
    if not payload or payload in {"{}", "null"}:
        existing = get_app_data(email)
        if existing:
            return
    last_error: sqlite3.OperationalError | None = None
    for attempt in range(3):
        try:
            with connect() as conn:
                conn.execute(
                    """
                    INSERT INTO app_data (email, data_json, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(email) DO UPDATE SET
                        data_json = excluded.data_json,
                        updated_at = excluded.updated_at
                    """,
                    (key, payload, _now()),
                )
                _commit(conn)
            if flush:
                from .db_storage import schedule_gcs_upload

                schedule_gcs_upload(flush=True)
            return
        except sqlite3.OperationalError as exc:
            last_error = exc
            if "disk I/O error" not in str(exc).lower() or attempt >= 2:
                raise
            time.sleep(0.25 * (attempt + 1))
    if last_error:
        raise last_error


def list_recipient_emails() -> list[str]:
    with connect() as conn:
        rows = conn.execute("SELECT email FROM recipient_oauth ORDER BY email").fetchall()
    return [normalize_email(str(row["email"])) for row in rows if str(row["email"] or "").strip()]


def list_owner_emails() -> list[str]:
    with connect() as conn:
        rows = conn.execute("SELECT email FROM owner_oauth ORDER BY email").fetchall()
    return [normalize_email(str(row["email"])) for row in rows if str(row["email"] or "").strip()]


def list_sync_mappings_for_recipient(recipient_email: str) -> list[dict[str, Any]]:
    recipient_key = normalize_email(recipient_email)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM sync_mappings
            WHERE recipient_email = ?
              AND recipient_person_id IS NOT NULL AND recipient_person_id != ''
            ORDER BY owner, owner_person_id, id
            """,
            (recipient_key,),
        ).fetchall()
    return [_mapping_from_row(row) for row in rows]


def list_recipients_with_mapped_contacts() -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT recipient_email FROM sync_mappings
            WHERE recipient_person_id IS NOT NULL AND recipient_person_id != ''
            ORDER BY recipient_email
            """
        ).fetchall()
    return [
        normalize_email(str(row["recipient_email"]))
        for row in rows
        if str(row["recipient_email"] or "").strip()
    ]


def count_unique_mapped_recipient_contacts(recipient_email: str | None = None) -> int:
    with connect() as conn:
        if recipient_email:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count FROM (
                    SELECT DISTINCT recipient_email, recipient_person_id
                    FROM sync_mappings
                    WHERE recipient_email = ?
                      AND recipient_person_id IS NOT NULL AND recipient_person_id != ''
                )
                """,
                (normalize_email(recipient_email),),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count FROM (
                    SELECT DISTINCT recipient_email, recipient_person_id
                    FROM sync_mappings
                    WHERE recipient_person_id IS NOT NULL AND recipient_person_id != ''
                )
                """
            ).fetchone()
    return int(row["count"] or 0) if row else 0


def has_active_job_type(job_type: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id FROM share_push_jobs
            WHERE job_type = ? AND status IN ('queued', 'running')
            ORDER BY id DESC LIMIT 1
            """,
            (str(job_type or ""),),
        ).fetchone()
    return row is not None


def list_app_data_emails() -> list[str]:
    with connect() as conn:
        rows = conn.execute("SELECT email FROM app_data ORDER BY email").fetchall()
    return [normalize_email(str(row["email"])) for row in rows if str(row["email"] or "").strip()]


def update_shared_contacts_group_name(
    owner: str, resource_name: str, new_name: str
) -> int:
    owner_key = normalize_email(owner)
    group_resource = str(resource_name or "").strip()
    group_name = str(new_name or "").strip()
    if not owner_key or not group_resource or not group_name:
        return 0
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE shared_contacts
            SET group_name = ?
            WHERE owner = ? AND group_resource_name = ?
            """,
            (group_name, owner_key, group_resource),
        )
        _commit(conn)
    return int(cursor.rowcount or 0)


def list_staged_share_groups_for_owner(owner: str) -> list[dict[str, Any]]:
    owner_key = normalize_email(owner)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT share_id, group_name, group_resource_name, COUNT(*) AS member_count
            FROM shared_contacts
            WHERE owner = ?
            GROUP BY share_id, group_name, group_resource_name
            ORDER BY group_name, share_id
            """,
            (owner_key,),
        ).fetchall()
    return [dict(row) for row in rows]


def count_sync_mappings_for_share(
    recipient_email: str,
    owner: str,
    share_id: str,
) -> int:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM sync_mappings
            WHERE recipient_email = ? AND owner = ? AND share_id = ?
              AND recipient_person_id IS NOT NULL AND recipient_person_id != ''
            """,
            (normalize_email(recipient_email), normalize_email(owner), str(share_id)),
        ).fetchone()
    return int(row["count"] or 0) if row else 0


def count_queued_push_jobs() -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM share_push_jobs WHERE status = 'queued'"
        ).fetchone()
    return int(row["count"] or 0) if row else 0


def save_owner_token(email: str, token: dict[str, Any]) -> None:
    key = normalize_email(email)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO owner_oauth (email, token_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                token_json = excluded.token_json,
                updated_at = excluded.updated_at
            """,
            (key, json.dumps(token), _now()),
        )
        _commit(conn)


def get_owner_token(email: str) -> dict[str, Any] | None:
    key = normalize_email(email)
    with connect() as conn:
        row = conn.execute(
            "SELECT token_json FROM owner_oauth WHERE email = ?", (key,)
        ).fetchone()
    if not row:
        return None
    return json.loads(row["token_json"])


def delete_owner_token(email: str) -> None:
    key = normalize_email(email)
    with connect() as conn:
        conn.execute("DELETE FROM owner_oauth WHERE email = ?", (key,))
        _commit(conn)


def delete_recipient_token(email: str) -> None:
    key = normalize_email(email)
    with connect() as conn:
        conn.execute("DELETE FROM recipient_oauth WHERE email = ?", (key,))
        _commit(conn)


def save_recipient_token(email: str, token: dict[str, Any]) -> None:
    key = normalize_email(email)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO recipient_oauth (email, token_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                token_json = excluded.token_json,
                updated_at = excluded.updated_at
            """,
            (key, json.dumps(token), _now()),
        )
        _commit(conn)


def get_recipient_token(email: str) -> dict[str, Any] | None:
    key = normalize_email(email)
    with connect() as conn:
        row = conn.execute(
            "SELECT token_json FROM recipient_oauth WHERE email = ?", (key,)
        ).fetchone()
    if not row:
        return None
    return json.loads(row["token_json"])


def write_shared_contacts(
    share_id: str,
    owner: str,
    group_name: str,
    group_resource_name: str,
    members_data: list[dict[str, Any]],
) -> None:
    created = _now()
    with connect() as conn:
        for idx, member in enumerate(members_data):
            conn.execute(
                """
                INSERT INTO shared_contacts
                (share_id, owner, group_name, group_resource_name, member_index, member_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    share_id,
                    normalize_email(owner),
                    group_name,
                    group_resource_name,
                    idx,
                    json.dumps(member),
                    created,
                ),
            )
        _commit(conn)


def read_shared_contacts(share_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT member_json FROM shared_contacts
            WHERE share_id = ?
            ORDER BY member_index
            """,
            (share_id,),
        ).fetchall()
    out = []
    for row in rows:
        try:
            out.append(json.loads(row["member_json"]))
        except json.JSONDecodeError:
            continue
    return out


def count_shared_contacts(share_id: str) -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM shared_contacts WHERE share_id = ?",
            (share_id,),
        ).fetchone()
    return int(row["n"] if row else 0)


def delete_shared_contacts(share_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM shared_contacts WHERE share_id = ?", (share_id,))
        _commit(conn)



def patch_shared_contact_member(
    share_id: str,
    owner_person_id: str,
    member_data: dict[str, Any],
) -> bool:
    """Replace one staged member payload by owner person resourceName. Returns True if patched."""
    share_key = str(share_id or "").strip()
    person_id = str(owner_person_id or "").strip()
    if not share_key or not person_id or not isinstance(member_data, dict):
        return False
    with connect() as conn:
        meta = conn.execute(
            """
            SELECT owner, group_name, group_resource_name
            FROM shared_contacts
            WHERE share_id = ?
            ORDER BY member_index
            LIMIT 1
            """,
            (share_key,),
        ).fetchone()
        if not meta:
            return False
        rows = conn.execute(
            """
            SELECT member_index, member_json
            FROM shared_contacts
            WHERE share_id = ?
            ORDER BY member_index
            """,
            (share_key,),
        ).fetchall()
        members: list[dict[str, Any]] = []
        found = False
        for row in rows:
            try:
                member = json.loads(row["member_json"])
            except json.JSONDecodeError:
                continue
            if str(member.get("resourceName") or "").strip() == person_id:
                members.append(member_data)
                found = True
            else:
                members.append(member)
        if not found:
            return False
        conn.execute("DELETE FROM shared_contacts WHERE share_id = ?", (share_key,))
        created = _now()
        for idx, member in enumerate(members):
            conn.execute(
                """
                INSERT INTO shared_contacts
                (share_id, owner, group_name, group_resource_name, member_index, member_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    share_key,
                    str(meta["owner"] or ""),
                    str(meta["group_name"] or ""),
                    str(meta["group_resource_name"] or ""),
                    idx,
                    json.dumps(member),
                    created,
                ),
            )
        _commit(conn)
    return True


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    return dict(row)


# --- sync_mappings ---


def load_sync_mappings(
    share_id: str,
    owner: str,
    group_resource_name: str,
    recipient_email: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Return (by_owner_id for this group, by_owner_id_any for owner+recipient)."""
    owner_key = normalize_email(owner)
    recipient_key = normalize_email(recipient_email)
    by_owner_id: dict[str, dict[str, Any]] = {}
    by_owner_id_any: dict[str, dict[str, Any]] = {}
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM sync_mappings
            WHERE owner = ? AND recipient_email = ?
            """,
            (owner_key, recipient_key),
        ).fetchall()
    for row in rows:
        mapping = _mapping_from_row(row)
        owner_person_id = mapping["owner_person_id"]
        if not owner_person_id:
            continue
        existing_any = by_owner_id_any.get(owner_person_id)
        if not existing_any or _mapping_is_newer(mapping, existing_any):
            by_owner_id_any[owner_person_id] = mapping
        if (
            mapping["share_id"] == share_id
            and mapping["group_resource_name"] == group_resource_name
        ):
            by_owner_id[owner_person_id] = mapping
    return by_owner_id, by_owner_id_any


def _mapping_from_row(row: sqlite3.Row) -> dict[str, Any]:
    row_dict = dict(row)
    return {
        "id": row_dict["id"],
        "share_id": row_dict["share_id"],
        "owner": row_dict["owner"],
        "group_resource_name": row_dict["group_resource_name"],
        "recipient_email": row_dict["recipient_email"],
        "owner_person_id": row_dict["owner_person_id"],
        "recipient_person_id": row_dict.get("recipient_person_id") or "",
        "last_hash": row_dict.get("last_hash") or "",
        "last_photo_hash": row_dict.get("last_photo_hash") or "",
        "last_sync": row_dict.get("last_sync") or "",
        "owner_etag": row_dict.get("owner_etag") or "",
        "last_assignment_label": row_dict.get("last_assignment_label") or "",
    }


def _mapping_is_newer(a: dict[str, Any], b: dict[str, Any]) -> bool:
    try:
        from datetime import datetime

        ta = datetime.fromisoformat(str(a.get("last_sync") or "1970-01-01"))
        tb = datetime.fromisoformat(str(b.get("last_sync") or "1970-01-01"))
        return ta >= tb
    except ValueError:
        return True


def upsert_sync_mapping(
    *,
    share_id: str,
    owner: str,
    group_resource_name: str,
    recipient_email: str,
    owner_person_id: str,
    recipient_person_id: str = "",
    last_hash: str = "",
    last_photo_hash: str = "",
    owner_etag: str = "",
    last_assignment_label: str = "",
) -> None:
    owner_key = normalize_email(owner)
    recipient_key = normalize_email(recipient_email)
    now = _now()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id FROM sync_mappings
            WHERE share_id = ? AND owner = ? AND group_resource_name = ?
              AND recipient_email = ? AND owner_person_id = ?
            """,
            (
                share_id,
                owner_key,
                group_resource_name,
                recipient_key,
                owner_person_id,
            ),
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE sync_mappings SET
                    recipient_person_id = ?,
                    last_hash = ?,
                    last_photo_hash = ?,
                    last_sync = ?,
                    owner_etag = ?,
                    last_assignment_label = ?
                WHERE id = ?
                """,
                (
                    recipient_person_id,
                    last_hash,
                    last_photo_hash,
                    now,
                    owner_etag,
                    last_assignment_label,
                    row["id"],
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO sync_mappings (
                    share_id, owner, group_resource_name, recipient_email,
                    owner_person_id, recipient_person_id,
                    last_hash, last_photo_hash, last_sync, owner_etag,
                    last_assignment_label
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    share_id,
                    owner_key,
                    group_resource_name,
                    recipient_key,
                    owner_person_id,
                    recipient_person_id,
                    last_hash,
                    last_photo_hash,
                    now,
                    owner_etag,
                    last_assignment_label,
                ),
            )
        _commit(conn)


def delete_sync_mapping_rows(
    share_id: str,
    owner: str,
    group_resource_name: str,
    recipient_email: str,
    owner_person_ids: list[str] | None = None,
) -> None:
    owner_key = normalize_email(owner)
    recipient_key = normalize_email(recipient_email)
    with connect() as conn:
        if owner_person_ids:
            for person_id in owner_person_ids:
                conn.execute(
                    """
                    DELETE FROM sync_mappings
                    WHERE share_id = ? AND owner = ? AND group_resource_name = ?
                      AND recipient_email = ? AND owner_person_id = ?
                    """,
                    (
                        share_id,
                        owner_key,
                        group_resource_name,
                        recipient_key,
                        person_id,
                    ),
                )
        else:
            conn.execute(
                """
                DELETE FROM sync_mappings
                WHERE share_id = ? AND owner = ? AND group_resource_name = ?
                  AND recipient_email = ?
                """,
                (share_id, owner_key, group_resource_name, recipient_key),
            )
        _commit(conn)


def find_mappings_for_owner_person(
    owner: str, owner_person_id: str
) -> list[dict[str, Any]]:
    owner_key = normalize_email(owner)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM sync_mappings
            WHERE owner = ? AND owner_person_id = ?
            ORDER BY last_sync DESC
            """,
            (owner_key, owner_person_id),
        ).fetchall()
    return [_mapping_from_row(row) for row in rows]


def list_distinct_owners_for_recipient(recipient_email: str) -> list[str]:
    recipient_key = normalize_email(recipient_email)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT owner FROM sync_mappings
            WHERE recipient_email = ?
            ORDER BY owner
            """,
            (recipient_key,),
        ).fetchall()
    return [
        str(row["owner"]).strip().lower()
        for row in rows
        if str(row["owner"] or "").strip()
    ]


def find_sibling_mappings(
    owner: str,
    recipient_email: str,
    owner_person_id: str,
    exclude_id: int | None = None,
) -> list[dict[str, Any]]:
    owner_key = normalize_email(owner)
    recipient_key = normalize_email(recipient_email)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM sync_mappings
            WHERE owner = ? AND recipient_email = ? AND owner_person_id = ?
              AND recipient_person_id IS NOT NULL AND recipient_person_id != ''
            ORDER BY id ASC
            """,
            (owner_key, recipient_key, owner_person_id),
        ).fetchall()
    out = []
    for row in rows:
        mapping = _mapping_from_row(row)
        if exclude_id and mapping.get("id") == exclude_id:
            continue
        out.append(mapping)
    return out


# --- share_push_jobs ---


def enqueue_push_job(
    *,
    owner: str,
    resource_name: str = "",
    recipient_email: str = "",
    job_type: str,
    payload: dict[str, Any] | None = None,
) -> int:
    now = _now()
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO share_push_jobs (
                owner, resource_name, recipient_email, job_type,
                status, payload_json, attempts, last_error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'queued', ?, 0, '', ?, ?)
            """,
            (
                normalize_email(owner),
                resource_name,
                normalize_email(recipient_email),
                job_type,
                json.dumps(payload or {}),
                now,
                now,
            ),
        )
        _commit(conn, flush=True)
        return int(cur.lastrowid)


def reclaim_all_running_push_jobs_on_startup() -> int:
    """Cloud Run cold starts kill the worker thread; running jobs cannot still be active."""
    now = _now()
    with connect() as conn:
        cur = conn.execute(
            """
            UPDATE share_push_jobs
            SET status = 'queued',
                last_error = COALESCE(NULLIF(last_error, ''), 'reclaimed on cold start'),
                updated_at = ?
            WHERE status = 'running'
            """,
            (now,),
        )
        _commit(conn)
        return int(cur.rowcount)


def reclaim_stale_push_jobs(stale_seconds: int = 90) -> int:
    """Re-queue jobs left in running after a worker crash or instance recycle."""
    now_dt = datetime.now(timezone.utc)
    cutoff = datetime.fromtimestamp(
        now_dt.timestamp() - max(30, stale_seconds), tz=timezone.utc
    ).isoformat()
    with connect() as conn:
        conn.execute(
            """
            UPDATE share_push_jobs
            SET status = 'error',
                last_error = COALESCE(NULLIF(last_error, ''), 'max attempts exceeded'),
                updated_at = ?
            WHERE status = 'running' AND attempts >= 25
            """,
            (_now(),),
        )
        cur = conn.execute(
            """
            UPDATE share_push_jobs
            SET status = 'queued',
                attempts = 0,
                last_error = COALESCE(NULLIF(last_error, ''), 'reclaimed stale running job'),
                updated_at = ?
            WHERE status = 'running' AND updated_at < ? AND attempts < 25
            """,
            (_now(), cutoff),
        )
        _commit(conn)
        return int(cur.rowcount)


def claim_push_jobs(limit: int = 1) -> list[dict[str, Any]]:
    reclaim_stale_push_jobs()
    now = _now()
    claimed: list[dict[str, Any]] = []
    priority_email = str(os.getenv("SHARE_JOB_PRIORITY_EMAIL", "") or "").strip().lower()
    with connect() as conn:
        # Newest real imports first. Resume/orphan leftovers (Emmy Fosters
        # scan, already-Shared groups) must not block a live Photos pass.
        order_sql = """
                ORDER BY CASE WHEN lower(recipient_email) = ? THEN 0 ELSE 1 END,
                         CASE WHEN COALESCE(json_extract(payload_json, '$.resume'), 0) = 1
                              THEN 1 ELSE 0 END,
                         created_at DESC, id DESC
        """
        if priority_email:
            rows = conn.execute(
                f"""
                SELECT * FROM share_push_jobs
                WHERE status = 'queued'
                {order_sql}
                LIMIT ?
                """,
                (priority_email, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                f"""
                SELECT * FROM share_push_jobs
                WHERE status = 'queued'
                {order_sql}
                LIMIT ?
                """,
                ("", limit),
            ).fetchall()
        for row in rows:
            conn.execute(
                """
                UPDATE share_push_jobs
                SET status = 'running', attempts = attempts + 1, updated_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (now, row["id"]),
            )
            fresh = conn.execute(
                "SELECT * FROM share_push_jobs WHERE id = ?", (row["id"],)
            ).fetchone()
            if fresh and fresh["status"] == "running":
                claimed.append(_push_job_from_row(fresh))
        _commit(conn)
    return claimed


def _push_job_from_row(row: sqlite3.Row) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except json.JSONDecodeError:
        payload = {}
    return {
        "id": row["id"],
        "owner": row["owner"],
        "resource_name": row["resource_name"],
        "recipient_email": row["recipient_email"],
        "job_type": row["job_type"],
        "status": row["status"],
        "payload": payload,
        "attempts": row["attempts"],
        "last_error": row["last_error"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_push_job(job_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM share_push_jobs WHERE id = ?", (int(job_id),)
        ).fetchone()
    return _push_job_from_row(row) if row else None


def update_push_job(
    job_id: int,
    *,
    status: str | None = None,
    last_error: str | None = None,
    requeue: bool = False,
    touch: bool = False,
    reset_attempts: bool = False,
) -> None:
    now = _now()
    with connect() as conn:
        if touch and status is None and last_error is None and not requeue:
            conn.execute(
                "UPDATE share_push_jobs SET updated_at = ? WHERE id = ?",
                (now, job_id),
            )
            _commit(conn)
            return
        if requeue:
            # A job that yielded its time slice is still healthy. Counting those
            # yields as attempts retires long imports as 'max attempts exceeded'.
            attempts_sql = "attempts = 0," if reset_attempts else ""
            conn.execute(
                f"""
                UPDATE share_push_jobs
                SET status = 'queued', {attempts_sql} updated_at = ?
                WHERE id = ?
                """,
                (now, job_id),
            )
        else:
            conn.execute(
                """
                UPDATE share_push_jobs
                SET status = COALESCE(?, status),
                    last_error = COALESCE(?, last_error),
                    updated_at = ?
                WHERE id = ?
                """,
                (status, last_error, now, job_id),
            )
        _commit(conn)


def has_active_push_job(
    owner: str,
    resource_name: str,
    recipient_email: str,
) -> bool:
    owner_key = normalize_email(owner)
    recipient_key = normalize_email(recipient_email)
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id FROM share_push_jobs
            WHERE owner = ? AND resource_name = ? AND recipient_email = ?
              AND status IN ('queued', 'running')
            ORDER BY id DESC LIMIT 1
            """,
            (owner_key, resource_name, recipient_key),
        ).fetchone()
    return row is not None


def cancel_push_jobs(owner: str, resource_name: str, recipient_email: str) -> None:
    now = _now()
    owner_key = normalize_email(owner)
    recipient_key = normalize_email(recipient_email)
    with connect() as conn:
        conn.execute(
            """
            UPDATE share_push_jobs
            SET status = 'cancelled', updated_at = ?
            WHERE owner = ? AND resource_name = ? AND recipient_email = ?
              AND status IN ('queued', 'running')
            """,
            (now, owner_key, str(resource_name or ""), recipient_key),
        )
        _commit(conn)


def list_push_jobs(
    owner: str,
    resource_name: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    owner_key = normalize_email(owner)
    with connect() as conn:
        if resource_name:
            rows = conn.execute(
                """
                SELECT * FROM share_push_jobs
                WHERE owner = ? AND resource_name = ?
                ORDER BY id DESC LIMIT ?
                """,
                (owner_key, resource_name, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM share_push_jobs
                WHERE owner = ?
                ORDER BY id DESC LIMIT ?
                """,
                (owner_key, limit),
            ).fetchall()
    return [_push_job_from_row(row) for row in rows]
