import os
import sqlite3
from contextvars import ContextVar
from pathlib import Path

from app.config import APP_DATA_DIR, DB_PATH, ERROR_LOG_PATH, MEETING_V2_SCHEMA_PATH, PROJECT_ROOT, SCHEMA_PATH, USE_APP_DATA
from app.logging_utils import append_log_line

_current_db_path: ContextVar[Path | None] = ContextVar("current_db_path", default=None)
_current_uploads_dir: ContextVar[Path | None] = ContextVar("current_uploads_dir", default=None)


def set_request_db_path(db_path: Path | None) -> None:
    _current_db_path.set(db_path)


def set_request_uploads_dir(uploads_dir: Path | None) -> None:
    _current_uploads_dir.set(uploads_dir)


def get_active_db_path() -> Path:
    override = _current_db_path.get()
    return Path(override) if override is not None else DB_PATH


def get_active_uploads_dir() -> Path:
    from app.config import UPLOADS_DIR

    override = _current_uploads_dir.get()
    return Path(override) if override is not None else UPLOADS_DIR


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        result = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return result


def _open_sqlite_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    timeout_seconds = 120 if str(os.environ.get("DATA_ROOT") or "") == "/data" else 60
    return sqlite3.connect(db_path, timeout=timeout_seconds, factory=ClosingConnection)


def _append_database_log(message: str) -> None:
    append_log_line(ERROR_LOG_PATH, message)


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    if db_path is None:
        db_path = get_active_db_path()
    candidate_paths = [Path(db_path)]
    # Mobile keeps one SQLite file per user. A fallback file would save contacts
    # somewhere the phone never reads, which looks like a download that saved nothing.
    if not USE_APP_DATA and str(os.environ.get("DATA_ROOT") or "") != "/data":
        # Legacy/dev-only fallbacks. When USE_APP_DATA is on, never open a
        # different sqlite file — a stale project-db copy must not shadow live data.
        candidate_paths.extend(
            [
                APP_DATA_DIR / "db" / "app.sqlite3",
                PROJECT_ROOT / "db" / "app.sqlite3",
                Path.home() / "ContactsFreeShareData" / "db" / "app.sqlite3",
            ]
        )
    unique_paths: list[Path] = []
    seen_paths: set[str] = set()
    for candidate in candidate_paths:
        try:
            key = str(candidate.expanduser().resolve())
        except OSError:
            key = str(candidate.expanduser())
        if key in seen_paths:
            continue
        seen_paths.add(key)
        unique_paths.append(candidate)

    errors: list[str] = []
    for candidate in unique_paths:
        try:
            conn = _open_sqlite_connection(candidate)
            break
        except (OSError, sqlite3.OperationalError) as exc:
            errors.append(f"{candidate}: {exc}")
            _append_database_log(f"SQLite open failed for {candidate}: {exc}")
    else:
        message = "unable to open database file; tried " + " | ".join(errors)
        _append_database_log(message)
        raise sqlite3.OperationalError(message)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    busy_ms = 120000 if str(os.environ.get("DATA_ROOT") or "") == "/data" else 60000
    conn.execute(f"PRAGMA busy_timeout = {busy_ms}")
    return conn


def apply_database_migrations() -> None:
    with get_connection() as conn:
        _apply_database_migrations(conn)
        conn.commit()


def _apply_database_migrations(conn: sqlite3.Connection) -> None:
    _ensure_contact_photo_sync_columns(conn)
    _ensure_book_layout_preset_columns(conn)
    _ensure_google_sync_columns(conn)
    _ensure_contact_assignment_option_columns(conn)
    _ensure_meeting_toc_exclusion_table(conn)
    _ensure_field_end_list_table(conn)
    _ensure_meeting_row_print_new_page_column(conn)
    _ensure_contact_edit_log_table(conn)
    _ensure_meeting_edit_log_table(conn)
    _ensure_app_revisions_table(conn)
    _ensure_changes_list_table(conn)
    _ensure_field_list_tables(conn)
    _ensure_field_list_sync_columns(conn)
    _ensure_address_structured_columns(conn)
    conn.execute("INSERT OR IGNORE INTO google_sync_accounts (id) VALUES (1)")
    conn.execute("INSERT OR IGNORE INTO google_sync_state (id) VALUES (1)")
    _clear_legacy_address_book_pdf_share_defaults(conn)
    conn.execute("INSERT OR IGNORE INTO address_book_settings (id) VALUES (1)")


def initialize_database() -> None:
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    meeting_v2_schema_sql = MEETING_V2_SCHEMA_PATH.read_text(encoding="utf-8")
    with get_connection() as conn:
        journal_mode = "DELETE" if str(os.environ.get("DATA_ROOT") or "") == "/data" else "WAL"
        conn.execute(f"PRAGMA journal_mode = {journal_mode}")
        conn.executescript(schema_sql)
        conn.executescript(meeting_v2_schema_sql)
        _apply_database_migrations(conn)
        conn.commit()


def _ensure_contact_photo_sync_columns(conn: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(contacts)").fetchall()
    }
    if "photo_drive_file_id" not in columns:
        conn.execute(
            """
            ALTER TABLE contacts
            ADD COLUMN photo_drive_file_id TEXT NOT NULL DEFAULT ''
            """
        )
    if "photo_sync_revision" not in columns:
        conn.execute(
            """
            ALTER TABLE contacts
            ADD COLUMN photo_sync_revision INTEGER NOT NULL DEFAULT 0
            """
        )
    if "photo_needs_export" not in columns:
        conn.execute(
            """
            ALTER TABLE contacts
            ADD COLUMN photo_needs_export INTEGER NOT NULL DEFAULT 0
            """
        )
    if "shared_drive_revision" not in columns:
        conn.execute(
            """
            ALTER TABLE contacts
            ADD COLUMN shared_drive_revision INTEGER NOT NULL DEFAULT 0
            """
        )
    if "shared_drive_needs_export" not in columns:
        conn.execute(
            """
            ALTER TABLE contacts
            ADD COLUMN shared_drive_needs_export INTEGER NOT NULL DEFAULT 0
            """
        )
    if "shared_drive_bootstrapped" not in columns:
        conn.execute(
            """
            ALTER TABLE contacts
            ADD COLUMN shared_drive_bootstrapped INTEGER NOT NULL DEFAULT 0
            """
        )
    if "do_not_print" not in columns:
        conn.execute(
            """
            ALTER TABLE contacts
            ADD COLUMN do_not_print INTEGER NOT NULL DEFAULT 0
            """
        )
    if "meets_in_state" not in columns:
        conn.execute(
            """
            ALTER TABLE contacts
            ADD COLUMN meets_in_state TEXT NOT NULL DEFAULT ''
            """
        )


def _ensure_book_layout_preset_columns(conn: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(book_layout_presets)").fetchall()
    }
    if "book_title" not in columns:
        conn.execute(
            """
            ALTER TABLE book_layout_presets
            ADD COLUMN book_title TEXT NOT NULL DEFAULT 'Address Book'
            """
        )
    if "font_family" not in columns:
        conn.execute(
            """
            ALTER TABLE book_layout_presets
            ADD COLUMN font_family TEXT NOT NULL DEFAULT 'Arial'
            """
        )
    if "meetingdata_primary_row_color" not in columns:
        conn.execute(
            """
            ALTER TABLE book_layout_presets
            ADD COLUMN meetingdata_primary_row_color TEXT NOT NULL DEFAULT '#f8f3e3'
            """
        )
    if "meetingdata_secondary_row_color" not in columns:
        conn.execute(
            """
            ALTER TABLE book_layout_presets
            ADD COLUMN meetingdata_secondary_row_color TEXT NOT NULL DEFAULT '#edf5f7'
            """
        )
    if "meetingdata_highlight_row_color" not in columns:
        conn.execute(
            """
            ALTER TABLE book_layout_presets
            ADD COLUMN meetingdata_highlight_row_color TEXT NOT NULL DEFAULT '#ffe3a1'
            """
        )
    if "meetingdata_title_bar_color" not in columns:
        conn.execute(
            """
            ALTER TABLE book_layout_presets
            ADD COLUMN meetingdata_title_bar_color TEXT NOT NULL DEFAULT '#e3eff3'
            """
        )
    if "contacts_primary_row_color" not in columns:
        conn.execute(
            """
            ALTER TABLE book_layout_presets
            ADD COLUMN contacts_primary_row_color TEXT NOT NULL DEFAULT '#f8f3e3'
            """
        )
    if "contacts_secondary_row_color" not in columns:
        conn.execute(
            """
            ALTER TABLE book_layout_presets
            ADD COLUMN contacts_secondary_row_color TEXT NOT NULL DEFAULT '#edf5f7'
            """
        )
    if "contacts_highlight_row_color" not in columns:
        conn.execute(
            """
            ALTER TABLE book_layout_presets
            ADD COLUMN contacts_highlight_row_color TEXT NOT NULL DEFAULT '#ffe3a1'
            """
        )
    if "contacts_title_bar_color" not in columns:
        conn.execute(
            """
            ALTER TABLE book_layout_presets
            ADD COLUMN contacts_title_bar_color TEXT NOT NULL DEFAULT '#e3eff3'
            """
        )
    if "shared_contacts_group_name" not in columns:
        conn.execute(
            """
            ALTER TABLE book_layout_presets
            ADD COLUMN shared_contacts_group_name TEXT NOT NULL DEFAULT 'TriState Separates (Shared)'
            """
        )
    if "title_font_size_pt" not in columns:
        conn.execute(
            """
            ALTER TABLE book_layout_presets
            ADD COLUMN title_font_size_pt REAL NOT NULL DEFAULT 12.0
            """
        )
    if "toc_font_size_pt" not in columns:
        conn.execute(
            """
            ALTER TABLE book_layout_presets
            ADD COLUMN toc_font_size_pt REAL NOT NULL DEFAULT 10.0
            """
        )
    if "index_font_size_pt" not in columns:
        conn.execute(
            """
            ALTER TABLE book_layout_presets
            ADD COLUMN index_font_size_pt REAL NOT NULL DEFAULT 0
            """
        )


def _ensure_google_sync_columns(conn: sqlite3.Connection) -> None:
    account_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(google_sync_accounts)").fetchall()
    }
    if account_columns and "google_user_id" not in account_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_accounts
            ADD COLUMN google_user_id TEXT NOT NULL DEFAULT ''
            """
        )
    if account_columns and "remember_preferred_account" not in account_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_accounts
            ADD COLUMN remember_preferred_account INTEGER NOT NULL DEFAULT 0
            """
        )
    if account_columns and "google_display_name" not in account_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_accounts
            ADD COLUMN google_display_name TEXT NOT NULL DEFAULT ''
            """
        )

    state_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(google_sync_state)").fetchall()
    }
    if state_columns and "pending_oauth_state" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN pending_oauth_state TEXT NOT NULL DEFAULT ''
            """
        )
    if state_columns and "pending_oauth_state_created_at" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN pending_oauth_state_created_at TEXT NOT NULL DEFAULT ''
            """
        )
    if state_columns and "bootstrap_status" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN bootstrap_status TEXT NOT NULL DEFAULT 'idle'
            """
        )
    if state_columns and "bootstrap_total_contacts" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN bootstrap_total_contacts INTEGER NOT NULL DEFAULT 0
            """
        )
    if state_columns and "bootstrap_processed_contacts" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN bootstrap_processed_contacts INTEGER NOT NULL DEFAULT 0
            """
        )
    if state_columns and "bootstrap_last_contact_id" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN bootstrap_last_contact_id INTEGER NOT NULL DEFAULT 0
            """
        )
    if state_columns and "bootstrap_started_at" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN bootstrap_started_at TEXT NOT NULL DEFAULT ''
            """
        )
    if state_columns and "bootstrap_updated_at" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN bootstrap_updated_at TEXT NOT NULL DEFAULT ''
            """
        )
    if state_columns and "bootstrap_error" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN bootstrap_error TEXT NOT NULL DEFAULT ''
            """
        )
    if state_columns and "upload_in_progress" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN upload_in_progress INTEGER NOT NULL DEFAULT 0
            """
        )
    if state_columns and "upload_phase" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN upload_phase TEXT NOT NULL DEFAULT ''
            """
        )
    if state_columns and "upload_total_count" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN upload_total_count INTEGER NOT NULL DEFAULT 0
            """
        )
    if state_columns and "upload_processed_count" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN upload_processed_count INTEGER NOT NULL DEFAULT 0
            """
        )
    if state_columns and "upload_current_contact" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN upload_current_contact TEXT NOT NULL DEFAULT ''
            """
        )
    if state_columns and "upload_started_at" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN upload_started_at TEXT NOT NULL DEFAULT ''
            """
        )
    if state_columns and "last_drive_export_at" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN last_drive_export_at TEXT NOT NULL DEFAULT ''
            """
        )
    if state_columns and "needs_drive_export" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN needs_drive_export INTEGER NOT NULL DEFAULT 0
            """
        )
    if state_columns and "meetingdata_needs_drive_export" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN meetingdata_needs_drive_export INTEGER NOT NULL DEFAULT 0
            """
        )
    if state_columns and "settings_sync_revision" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN settings_sync_revision INTEGER NOT NULL DEFAULT 0
            """
        )
    if state_columns and "contacts_sync_revision" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN contacts_sync_revision INTEGER NOT NULL DEFAULT 0
            """
        )
    if state_columns and "meetingdata_sync_revision" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN meetingdata_sync_revision INTEGER NOT NULL DEFAULT 0
            """
        )
    if state_columns and "signin_sync_in_progress" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN signin_sync_in_progress INTEGER NOT NULL DEFAULT 0
            """
        )
    if state_columns and "signin_sync_phase" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN signin_sync_phase TEXT NOT NULL DEFAULT ''
            """
        )
    if state_columns and "signin_sync_error" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN signin_sync_error TEXT NOT NULL DEFAULT ''
            """
        )
    if state_columns and "signin_sync_updated_at" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN signin_sync_updated_at TEXT NOT NULL DEFAULT ''
            """
        )
    if state_columns and "signin_sync_total_count" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN signin_sync_total_count INTEGER NOT NULL DEFAULT 0
            """
        )
    if state_columns and "signin_sync_processed_count" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN signin_sync_processed_count INTEGER NOT NULL DEFAULT 0
            """
        )
    if state_columns and "signin_sync_current_item" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN signin_sync_current_item TEXT NOT NULL DEFAULT ''
            """
        )
    if state_columns and "drive_root_folder_id" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN drive_root_folder_id TEXT NOT NULL DEFAULT ''
            """
        )
    if state_columns and "drive_app_revisions_folder_id" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN drive_app_revisions_folder_id TEXT NOT NULL DEFAULT ''
            """
        )
    if state_columns and "drive_app_revisions_file_id" not in state_columns:
        conn.execute(
            """
            ALTER TABLE google_sync_state
            ADD COLUMN drive_app_revisions_file_id TEXT NOT NULL DEFAULT ''
            """
        )
    for column_name, column_type, default_value in [
        ("multi_editor_enabled", "INTEGER", "0"),
        ("multi_editor_account_locked", "INTEGER", "0"),
        ("editor_lock_timeout_minutes", "INTEGER", "60"),
        ("editor_name", "TEXT", "''"),
        ("editor_session_id", "TEXT", "''"),
        ("editor_mode", "TEXT", "'normal'"),
        ("editor_lock_owner_name", "TEXT", "''"),
        ("editor_lock_expires_at", "TEXT", "''"),
        ("pending_editor_name", "TEXT", "''"),
        ("editor_secret_hash", "TEXT", "''"),
        ("editor_secret_salt", "TEXT", "''"),
        ("address_book_pdf_share_enabled", "INTEGER", "0"),
        ("address_book_pdf_share_folder_id", "TEXT", "''"),
        ("address_book_pdf_share_folder_name", "TEXT", "''"),
        ("access_role", "TEXT", "'editor'"),
        ("access_name", "TEXT", "''"),
        ("access_signed_in_at", "TEXT", "''"),
        ("changes_list_needs_drive_export", "INTEGER", "0"),
        ("changes_list_sync_revision", "INTEGER", "0"),
        ("contacts_manifest_needs_drive_export", "INTEGER", "0"),
        ("contacts_signout_backup_needed", "INTEGER", "0"),
        ("meetingdata_signout_backup_needed", "INTEGER", "0"),
        ("book_layouts_needs_drive_export", "INTEGER", "0"),
        ("book_layouts_sync_revision", "INTEGER", "0"),
        ("public_web_url", "TEXT", "''"),
        ("share_web_api_key", "TEXT", "''"),
        ("share_sync_last_message", "TEXT", "''"),
        ("pending_shared_group_rename", "TEXT", "''"),
        ("google_contacts_sync_token", "TEXT", "''"),
    ]:
        if state_columns and column_name not in state_columns:
            conn.execute(
                f"""
                ALTER TABLE google_sync_state
                ADD COLUMN {column_name} {column_type} NOT NULL DEFAULT {default_value}
                """
            )


def _clear_legacy_address_book_pdf_share_defaults(conn: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(google_sync_state)").fetchall()
    }
    legacy_columns = {
        "address_book_pdf_share_enabled",
        "address_book_pdf_share_folder_id",
        "address_book_pdf_share_folder_name",
    }
    if not legacy_columns.issubset(columns):
        return
    conn.execute(
        """
        UPDATE google_sync_state
        SET
          address_book_pdf_share_enabled = 0,
          address_book_pdf_share_folder_id = '',
          address_book_pdf_share_folder_name = ''
        WHERE LOWER(TRIM(address_book_pdf_share_folder_name)) = 'pdf & addbook files'
          AND TRIM(address_book_pdf_share_folder_id) = ''
        """
    )


def _ensure_meeting_toc_exclusion_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meeting_toc_exclusions (
          field_name TEXT NOT NULL,
          meeting_name TEXT NOT NULL,
          PRIMARY KEY (field_name, meeting_name)
        )
        """
    )


def _ensure_meeting_row_print_new_page_column(conn: sqlite3.Connection) -> None:
    try:
        row_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(meeting_section_rows_v2)").fetchall()
        }
    except sqlite3.OperationalError:
        return
    if row_columns and "print_new_page" not in row_columns:
        conn.execute(
            """
            ALTER TABLE meeting_section_rows_v2
            ADD COLUMN print_new_page INTEGER NOT NULL DEFAULT 0
            """
        )


def _ensure_field_end_list_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS field_end_lists (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          field_name TEXT NOT NULL,
          list_name TEXT NOT NULL,
          sort_order INTEGER NOT NULL DEFAULT 0,
          UNIQUE (field_name, list_name)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_field_end_lists_field_sort
        ON field_end_lists (field_name, sort_order, id)
        """
    )


def _ensure_contact_assignment_option_columns(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS contact_assignment_options (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          kind TEXT NOT NULL,
          value TEXT NOT NULL,
          parent_value TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE (kind, value)
        )
        """
    )
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(contact_assignment_options)").fetchall()
    }
    if "parent_value" not in columns:
        conn.execute(
            """
            ALTER TABLE contact_assignment_options
            ADD COLUMN parent_value TEXT NOT NULL DEFAULT ''
            """
        )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_contact_assignment_options_kind_value
        ON contact_assignment_options (kind, value)
        """
    )


def _ensure_field_list_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS address_book_settings (
          id INTEGER PRIMARY KEY CHECK (id = 1),
          margin_left_in REAL NOT NULL DEFAULT 0.35,
          margin_right_in REAL NOT NULL DEFAULT 0.35,
          margin_top_in REAL NOT NULL DEFAULT 0.35,
          margin_bottom_in REAL NOT NULL DEFAULT 0.35,
          font_family TEXT NOT NULL DEFAULT 'Arial',
          base_font_size_pt REAL NOT NULL DEFAULT 10,
          base_font_bold INTEGER NOT NULL DEFAULT 0,
          base_font_italic INTEGER NOT NULL DEFAULT 0,
          base_font_underline INTEGER NOT NULL DEFAULT 0,
          line_height REAL NOT NULL DEFAULT 1.2,
          preview_scale REAL NOT NULL DEFAULT 0.7,
          page_column_count INTEGER NOT NULL DEFAULT 1,
          include_bible_study_union_info INTEGER NOT NULL DEFAULT 1,
          two_column_gap_ch REAL NOT NULL DEFAULT 6,
          column_width_in REAL NOT NULL DEFAULT 4,
          address_align TEXT NOT NULL DEFAULT 'right',
          address_map_provider TEXT NOT NULL DEFAULT 'google',
          address_italic INTEGER NOT NULL DEFAULT 1,
          title_font_family TEXT NOT NULL DEFAULT 'Arial',
          title_font_size_pt REAL NOT NULL DEFAULT 13,
          title_font_bold INTEGER NOT NULL DEFAULT 1,
          title_font_italic INTEGER NOT NULL DEFAULT 0,
          title_font_underline INTEGER NOT NULL DEFAULT 0,
          title_align TEXT NOT NULL DEFAULT 'center',
          meeting_name_font_family TEXT NOT NULL DEFAULT 'Arial',
          meeting_name_font_size_pt REAL NOT NULL DEFAULT 11,
          meeting_name_font_bold INTEGER NOT NULL DEFAULT 1,
          meeting_name_font_italic INTEGER NOT NULL DEFAULT 0,
          meeting_name_font_underline INTEGER NOT NULL DEFAULT 0,
          meeting_name_align TEXT NOT NULL DEFAULT 'center',
          bible_study_font_size_pt REAL NOT NULL DEFAULT 8.5,
          bible_study_union_align TEXT NOT NULL DEFAULT 'center',
          separator_lines INTEGER NOT NULL DEFAULT 1,
          separator_vertical_lines INTEGER NOT NULL DEFAULT 1,
          manual_palette_items INTEGER NOT NULL DEFAULT 0,
          print_order_mode TEXT NOT NULL DEFAULT 'alphabetical',
          print_order_json TEXT NOT NULL DEFAULT '',
          meeting_line_height_json TEXT NOT NULL DEFAULT '',
          meeting_font_size_json TEXT NOT NULL DEFAULT '',
          meeting_page_mode TEXT NOT NULL DEFAULT 'one_meeting',
          field_separator_pages INTEGER NOT NULL DEFAULT 0,
          field_separator_font_size_pt REAL NOT NULL DEFAULT 0,
          double_column_index INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    settings_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(address_book_settings)").fetchall()
    }
    for column_name, column_type, default_value in [
        ("page_column_count", "INTEGER", "1"),
        ("base_font_bold", "INTEGER", "0"),
        ("base_font_italic", "INTEGER", "0"),
        ("base_font_underline", "INTEGER", "0"),
        ("include_bible_study_union_info", "INTEGER", "1"),
        ("two_column_gap_ch", "REAL", "6"),
        ("column_width_in", "REAL", "4"),
        ("address_align", "TEXT", "'right'"),
        ("address_map_provider", "TEXT", "'google'"),
        ("address_italic", "INTEGER", "1"),
        ("title_font_family", "TEXT", "'Arial'"),
        ("title_font_size_pt", "REAL", "13"),
        ("title_font_bold", "INTEGER", "1"),
        ("title_font_italic", "INTEGER", "0"),
        ("title_font_underline", "INTEGER", "0"),
        ("title_align", "TEXT", "'center'"),
        ("meeting_name_font_family", "TEXT", "'Arial'"),
        ("meeting_name_font_size_pt", "REAL", "11"),
        ("meeting_name_font_bold", "INTEGER", "1"),
        ("meeting_name_font_italic", "INTEGER", "0"),
        ("meeting_name_font_underline", "INTEGER", "0"),
        ("meeting_name_align", "TEXT", "'center'"),
        ("bible_study_font_size_pt", "REAL", "8.5"),
        ("bible_study_union_align", "TEXT", "'center'"),
        ("separator_lines", "INTEGER", "1"),
        ("separator_vertical_lines", "INTEGER", "1"),
        ("manual_palette_items", "INTEGER", "0"),
        ("print_order_mode", "TEXT", "'alphabetical'"),
        ("print_order_json", "TEXT", "''"),
        ("meeting_line_height_json", "TEXT", "''"),
        ("meeting_font_size_json", "TEXT", "''"),
        ("meeting_page_mode", "TEXT", "'one_meeting'"),
        ("field_separator_pages", "INTEGER", "0"),
        ("field_separator_font_size_pt", "REAL", "0"),
        ("double_column_index", "INTEGER", "0"),
    ]:
        if settings_columns and column_name not in settings_columns:
            conn.execute(
                f"""
                ALTER TABLE address_book_settings
                ADD COLUMN {column_name} {column_type} NOT NULL DEFAULT {default_value}
                """
            )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS field_list_templates (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          is_default INTEGER NOT NULL DEFAULT 0,
          settings_json TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    template_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(field_list_templates)").fetchall()
    }
    if template_columns and "settings_json" not in template_columns:
        conn.execute(
            """
            ALTER TABLE field_list_templates
            ADD COLUMN settings_json TEXT NOT NULL DEFAULT ''
            """
        )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS field_list_template_items (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          template_id INTEGER NOT NULL,
          item_type TEXT NOT NULL,
          label_number INTEGER NOT NULL DEFAULT 0,
          x_in REAL NOT NULL DEFAULT 0,
          y_in REAL NOT NULL DEFAULT 0,
          width_in REAL NOT NULL DEFAULT 1,
          height_in REAL NOT NULL DEFAULT 0.4,
          font_size_pt REAL NOT NULL DEFAULT 11,
          sort_order INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY (template_id) REFERENCES field_list_templates(id) ON DELETE CASCADE
        )
        """
    )
    item_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(field_list_template_items)").fetchall()
    }
    for column_name, column_type, default_value in [
        ("template_id", "INTEGER", "0"),
        ("item_type", "TEXT", "''"),
        ("label_number", "INTEGER", "0"),
        ("x_in", "REAL", "0"),
        ("y_in", "REAL", "0"),
        ("width_in", "REAL", "1"),
        ("height_in", "REAL", "0.4"),
        ("font_size_pt", "REAL", "11"),
        ("sort_order", "INTEGER", "0"),
        ("created_at", "TEXT", "''"),
        ("updated_at", "TEXT", "''"),
    ]:
        if item_columns and column_name not in item_columns:
            conn.execute(
                f"""
                ALTER TABLE field_list_template_items
                ADD COLUMN {column_name} {column_type} NOT NULL DEFAULT {default_value}
                """
            )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_field_list_templates_name
        ON field_list_templates (name)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_field_list_template_items_template
        ON field_list_template_items (template_id, sort_order, id)
        """
    )


def _ensure_share_recipients_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS share_recipients (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          display_name TEXT NOT NULL DEFAULT '',
          email TEXT NOT NULL,
          invite_sent_at TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(email)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_share_recipients_email
        ON share_recipients (email)
        """
    )


def _ensure_field_list_sync_columns(conn: sqlite3.Connection) -> None:
    state_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(google_sync_state)").fetchall()
    }
    for column_name, column_type, default_value in [
        ("field_list_needs_drive_export", "INTEGER", "0"),
        ("field_list_sync_revision", "INTEGER", "0"),
    ]:
        if state_columns and column_name not in state_columns:
            conn.execute(
                f"""
                ALTER TABLE google_sync_state
                ADD COLUMN {column_name} {column_type} NOT NULL DEFAULT {default_value}
                """
            )




def _ensure_address_structured_columns(conn: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(addresses)").fetchall()
    }
    if not columns:
        return
    for column_name in (
        "street_address",
        "extended_address",
        "city",
        "region",
        "postal_code",
    ):
        if column_name not in columns:
            conn.execute(
                f"""
                ALTER TABLE addresses
                ADD COLUMN {column_name} TEXT NOT NULL DEFAULT ''
                """
            )
    from app.services.address_format import enrich_address_parts

    rows = conn.execute(
        """
        SELECT id, street_address, extended_address, city, region, postal_code, formatted_address
        FROM addresses
        """
    ).fetchall()
    for row in rows:
        street = str(row["street_address"] or "").strip()
        extended = str(row["extended_address"] or "").strip()
        city = str(row["city"] or "").strip()
        region = str(row["region"] or "").strip()
        postal = str(row["postal_code"] or "").strip()
        formatted = str(row["formatted_address"] or "").strip()
        if not any((street, extended, city, region, postal, formatted)):
            continue
        enriched = enrich_address_parts(
            street_address=street,
            extended_address=extended,
            city=city,
            region=region,
            postal_code=postal,
            formatted_address=formatted,
        )
        next_values = (
            enriched["street_address"],
            enriched["extended_address"],
            enriched["city"],
            enriched["region"],
            enriched["postal_code"],
            enriched["formatted_address"] or formatted,
        )
        current_values = (street, extended, city, region, postal, formatted)
        if next_values == current_values:
            continue
        conn.execute(
            """
            UPDATE addresses
            SET
              street_address = ?,
              extended_address = ?,
              city = ?,
              region = ?,
              postal_code = ?,
              formatted_address = ?
            WHERE id = ?
            """,
            (*next_values, int(row["id"])),
        )

def _ensure_contact_edit_log_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS contact_edit_log_entries (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          batch_id TEXT NOT NULL,
          batch_created_at TEXT NOT NULL,
          batch_row_index INTEGER NOT NULL,
          editor_initials TEXT NOT NULL,
          contact_id INTEGER NOT NULL,
          contact_name TEXT NOT NULL,
          label_name TEXT NOT NULL,
          original_text TEXT NOT NULL DEFAULT '',
          edited_text TEXT NOT NULL DEFAULT '',
          FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_contact_edit_log_entries_batch
        ON contact_edit_log_entries (batch_created_at DESC, batch_row_index ASC, id ASC)
        """
    )


def _ensure_meeting_edit_log_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meeting_edit_log_entries (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          batch_id TEXT NOT NULL,
          batch_created_at TEXT NOT NULL,
          batch_row_index INTEGER NOT NULL,
          editor_initials TEXT NOT NULL,
          section_id INTEGER NOT NULL,
          field_name TEXT NOT NULL,
          meeting_name TEXT NOT NULL,
          row_id INTEGER NOT NULL,
          row_number INTEGER NOT NULL,
          original_text TEXT NOT NULL DEFAULT '',
          edited_text TEXT NOT NULL DEFAULT '',
          FOREIGN KEY (section_id) REFERENCES meeting_sections_v2(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_meeting_edit_log_entries_batch
        ON meeting_edit_log_entries (batch_created_at DESC, batch_row_index ASC, id ASC)
        """
    )


def _ensure_app_revisions_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_revisions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          sort_order INTEGER NOT NULL,
          date_display TEXT NOT NULL DEFAULT '',
          version_text TEXT NOT NULL DEFAULT '',
          description TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_app_revisions_sort_order
        ON app_revisions (sort_order, id)
        """
    )


def _ensure_changes_list_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS changes_list_entries (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          sort_order INTEGER NOT NULL,
          automatic_date TEXT NOT NULL DEFAULT '',
          name_event TEXT NOT NULL DEFAULT '',
          changes_text TEXT NOT NULL DEFAULT '',
          completed_by TEXT NOT NULL DEFAULT '',
          completed_at TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(changes_list_entries)").fetchall()
    }
    if "completed_by" not in columns:
        conn.execute("ALTER TABLE changes_list_entries ADD COLUMN completed_by TEXT NOT NULL DEFAULT ''")
    if "completed_at" not in columns:
        conn.execute("ALTER TABLE changes_list_entries ADD COLUMN completed_at TEXT NOT NULL DEFAULT ''")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_changes_list_entries_sort_order
        ON changes_list_entries (sort_order, id)
        """
    )
    existing = conn.execute("SELECT COUNT(*) AS count FROM changes_list_entries").fetchone()
    if int(existing["count"] or 0) < 10:
        max_sort_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), 0) AS max_sort_order FROM changes_list_entries"
        ).fetchone()
        next_sort_order = int(max_sort_order["max_sort_order"] or 0) + 1
        for sort_order in range(next_sort_order, next_sort_order + (10 - int(existing["count"] or 0))):
            conn.execute(
                """
                INSERT INTO changes_list_entries (sort_order)
                VALUES (?)
                """,
                (sort_order,),
            )


def fetch_one(query: str, params: tuple = ()):
    with get_connection() as conn:
        row = conn.execute(query, params).fetchone()
        return dict(row) if row is not None else None


def fetch_all(query: str, params: tuple = ()):
    with get_connection() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def execute(query: str, params: tuple = ()) -> int:
    with get_connection() as conn:
        cursor = conn.execute(query, params)
        conn.commit()
        return cursor.rowcount
