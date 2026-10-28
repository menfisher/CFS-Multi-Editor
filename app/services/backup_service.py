import base64
import binascii
import json
import mimetypes
from datetime import datetime
from pathlib import Path

from app.config import UPLOADS_DIR
from app.database import fetch_all, get_connection


CONTACT_BACKUP_TABLES = [
    "contacts",
    "relationships",
    "phones",
    "addresses",
    "emails",
    "custom_fields",
    "contact_assignment_options",
    "contact_edit_log_entries",
]

MEETINGDATA_BACKUP_TABLES = [
    "meeting_data_rows",
    "meeting_sections_v2",
    "meeting_section_name_options",
    "meeting_section_rows_v2",
    "meeting_row_cells_v2",
    "meeting_row_flow_items_v2",
    "meeting_layout_presets_v2",
    "meeting_section_layout_v2",
    "meeting_edit_log_entries",
]

CONTACT_RESTORE_DELETE_ORDER = [
    "contact_edit_log_entries",
    "relationships",
    "phones",
    "addresses",
    "emails",
    "custom_fields",
    "google_sync_queue",
    "contacts",
    "contact_assignment_options",
]

CONTACT_RESTORE_INSERT_ORDER = [
    "contacts",
    "relationships",
    "phones",
    "addresses",
    "emails",
    "custom_fields",
    "contact_assignment_options",
    "contact_edit_log_entries",
]

MEETINGDATA_RESTORE_DELETE_ORDER = [
    "meeting_edit_log_entries",
    "meeting_row_cells_v2",
    "meeting_row_flow_items_v2",
    "meeting_layout_presets_v2",
    "meeting_section_layout_v2",
    "meeting_section_rows_v2",
    "meeting_sections_v2",
    "meeting_section_name_options",
    "meeting_data_rows",
]

MEETINGDATA_RESTORE_INSERT_ORDER = [
    "meeting_data_rows",
    "meeting_sections_v2",
    "meeting_section_name_options",
    "meeting_section_rows_v2",
    "meeting_row_cells_v2",
    "meeting_row_flow_items_v2",
    "meeting_layout_presets_v2",
    "meeting_section_layout_v2",
    "meeting_edit_log_entries",
]

CONTACT_PHOTO_STATIC_PREFIX = "/static/uploads/contact_photos/"
CONTACT_PHOTO_STORAGE_DIR = UPLOADS_DIR / "contact_photos"


def _date_stamp() -> str:
    now = datetime.now()
    return f"{now.month}-{now.day}-{now.year}"


def backup_filename(backup_kind: str) -> str:
    normalized_kind = str(backup_kind or "").strip().lower()
    label = "MeetingData" if normalized_kind == "meetingdata" else "Contacts"
    return f"ContactsFreeShare_{label}_{_date_stamp()}.json"


def _table_rows(table_name: str) -> list[dict]:
    return [
        dict(row)
        for row in fetch_all(f"SELECT * FROM {table_name} ORDER BY id")
    ]


def _build_backup_payload(backup_kind: str, table_names: list[str]) -> dict:
    normalized_kind = str(backup_kind or "").strip().lower()
    return {
        "format": f"contactsfreeshare.{normalized_kind}.backup.v1",
        "backup_kind": normalized_kind,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "tables": {
            table_name: _table_rows(table_name)
            for table_name in table_names
        },
    }


def _contact_photo_backup_files(contact_rows: list[dict]) -> list[dict]:
    files: list[dict] = []
    seen_paths: set[str] = set()
    for row in contact_rows:
        photo_path = str(row.get("photo") or "").strip()
        if not photo_path.startswith(CONTACT_PHOTO_STATIC_PREFIX) or photo_path in seen_paths:
            continue
        seen_paths.add(photo_path)
        filename = Path(photo_path).name
        if not filename:
            continue
        source_path = CONTACT_PHOTO_STORAGE_DIR / filename
        if not source_path.is_file():
            continue
        content = source_path.read_bytes()
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        files.append(
            {
                "path": photo_path,
                "mime_type": mime_type,
                "data_base64": base64.b64encode(content).decode("ascii"),
            }
        )
    return files


def build_contacts_backup_bytes() -> tuple[str, bytes]:
    payload = _build_backup_payload("contacts", CONTACT_BACKUP_TABLES)
    payload["files"] = {
        "contact_photos": _contact_photo_backup_files(payload["tables"].get("contacts") or []),
    }
    return backup_filename("contacts"), json.dumps(payload, indent=2).encode("utf-8")


def build_contacts_shared_payload() -> dict:
    return _build_backup_payload("contacts", CONTACT_BACKUP_TABLES)


def build_meetingdata_backup_bytes() -> tuple[str, bytes]:
    payload = _build_backup_payload("meetingdata", MEETINGDATA_BACKUP_TABLES)
    return backup_filename("meetingdata"), json.dumps(payload, indent=2).encode("utf-8")


def _validate_backup_payload(payload: dict, expected_kind: str) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Backup file is not valid JSON backup data.")
    backup_kind = str(payload.get("backup_kind") or "").strip().lower()
    if backup_kind != expected_kind:
        raise ValueError(f"Selected backup is not a {expected_kind} backup.")
    tables = payload.get("tables")
    if not isinstance(tables, dict):
        raise ValueError("Backup file does not include table data.")
    return tables


def _insert_rows(conn, table_name: str, rows: list[dict]) -> int:
    table_columns = {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    inserted = 0
    for row in rows:
        if not isinstance(row, dict) or not row:
            continue
        columns = [column for column in row.keys() if column in table_columns]
        if not columns:
            continue
        placeholders = ", ".join("?" for _ in columns)
        column_sql = ", ".join(columns)
        conn.execute(
            f"INSERT INTO {table_name} ({column_sql}) VALUES ({placeholders})",
            tuple(row[column] for column in columns),
        )
        inserted += 1
    return inserted


def _validated_contact_photo_files(payload: dict) -> list[tuple[str, bytes]]:
    files = payload.get("files") or {}
    if not isinstance(files, dict):
        return []
    photo_files = files.get("contact_photos") or []
    if not isinstance(photo_files, list):
        return []

    decoded_files: list[tuple[str, bytes]] = []
    for item in photo_files:
        if not isinstance(item, dict):
            continue
        photo_path = str(item.get("path") or "").strip()
        if not photo_path.startswith(CONTACT_PHOTO_STATIC_PREFIX):
            continue
        filename = Path(photo_path).name
        if not filename:
            continue
        try:
            content = base64.b64decode(str(item.get("data_base64") or ""), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Backup file includes invalid contact photo data.") from exc
        if not content:
            continue
        decoded_files.append((filename, content))
    return decoded_files


def _restore_contact_photo_files(decoded_files: list[tuple[str, bytes]]) -> None:
    CONTACT_PHOTO_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    for existing_file in CONTACT_PHOTO_STORAGE_DIR.iterdir():
        if existing_file.is_file():
            existing_file.unlink()
    for filename, content in decoded_files:
        (CONTACT_PHOTO_STORAGE_DIR / filename).write_bytes(content)


def restore_contacts_backup_payload(payload: dict, *, restore_files: bool = True) -> dict:
    tables = _validate_backup_payload(payload, "contacts")
    if restore_files:
        photo_files = _validated_contact_photo_files(payload)
        _restore_contact_photo_files(photo_files)
    with get_connection() as conn:
        for table_name in CONTACT_RESTORE_DELETE_ORDER:
            conn.execute(f"DELETE FROM {table_name}")
        inserted_counts = {
            table_name: _insert_rows(conn, table_name, tables.get(table_name) or [])
            for table_name in CONTACT_RESTORE_INSERT_ORDER
        }
        from app.services.google_sync_service import collapse_stale_identity_duplicate_contacts

        stale_duplicate_ids = collapse_stale_identity_duplicate_contacts(conn)
        conn.execute(
            """
            UPDATE google_sync_state
            SET
              pending_upload_count = 0,
              needs_upload_reminder = 0,
              needs_drive_export = 0,
              meetingdata_needs_drive_export = 0,
              contacts_manifest_needs_drive_export = CASE
                WHEN ? THEN 1
                ELSE contacts_manifest_needs_drive_export
              END,
              last_sync_error = '',
              updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
            """,
            (1 if stale_duplicate_ids else 0,),
        )
        conn.commit()
    return inserted_counts


def restore_meetingdata_backup_payload(payload: dict) -> dict:
    from app.services.meeting_v2_service import ensure_meeting_v2_sections

    ensure_meeting_v2_sections()
    tables = _validate_backup_payload(payload, "meetingdata")
    with get_connection() as conn:
        for table_name in MEETINGDATA_RESTORE_DELETE_ORDER:
            conn.execute(f"DELETE FROM {table_name}")

        existing_preset_ids = {
            int(row["id"])
            for row in conn.execute("SELECT id FROM book_layout_presets").fetchall()
        }
        inserted_counts: dict[str, int] = {}
        for table_name in MEETINGDATA_RESTORE_INSERT_ORDER:
            rows = list(tables.get(table_name) or [])
            if table_name == "meeting_section_layout_v2":
                rows = [
                    row
                    for row in rows
                    if int(row.get("book_layout_preset_id") or 0) in existing_preset_ids
                ]
            inserted_counts[table_name] = _insert_rows(conn, table_name, rows)
        conn.commit()
    return inserted_counts
