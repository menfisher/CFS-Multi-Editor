import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from app.routes import google_sync as google_sync_routes
from app.services import google_sync_service as gss


def _memory_state_conn(**flags) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE google_sync_state (
          id INTEGER PRIMARY KEY CHECK (id = 1),
          needs_drive_export INTEGER NOT NULL DEFAULT 0,
          contacts_manifest_needs_drive_export INTEGER NOT NULL DEFAULT 0,
          book_layouts_needs_drive_export INTEGER NOT NULL DEFAULT 0,
          meetingdata_needs_drive_export INTEGER NOT NULL DEFAULT 0,
          field_list_needs_drive_export INTEGER NOT NULL DEFAULT 0,
          changes_list_needs_drive_export INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        INSERT INTO google_sync_state (
          id,
          needs_drive_export,
          contacts_manifest_needs_drive_export,
          book_layouts_needs_drive_export,
          meetingdata_needs_drive_export,
          field_list_needs_drive_export,
          changes_list_needs_drive_export
        ) VALUES (1, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(bool(flags.get("needs_drive_export"))),
            int(bool(flags.get("contacts_manifest_needs_drive_export"))),
            int(bool(flags.get("book_layouts_needs_drive_export"))),
            int(bool(flags.get("meetingdata_needs_drive_export"))),
            int(bool(flags.get("field_list_needs_drive_export"))),
            int(bool(flags.get("changes_list_needs_drive_export"))),
        ),
    )
    conn.commit()
    return conn


class NonEditorAccessSignOutTests(unittest.TestCase):
    def test_leftover_editor_contact_flags_are_not_non_editor_work(self) -> None:
        conn = _memory_state_conn(
            needs_drive_export=1,
            contacts_manifest_needs_drive_export=1,
            meetingdata_needs_drive_export=1,
        )
        conn.execute(
            """
            CREATE TABLE contacts (
              id INTEGER PRIMARY KEY,
              shared_drive_needs_export INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute("INSERT INTO contacts (id, shared_drive_needs_export) VALUES (1, 1)")
        conn.commit()
        self.assertTrue(gss._state_has_pending_drive_export(conn))
        self.assertFalse(gss._state_has_pending_non_editor_drive_export(conn))

    def test_export_skips_contact_settings_and_meetingdata(self) -> None:
        conn = _memory_state_conn(
            needs_drive_export=1,
            contacts_manifest_needs_drive_export=1,
            meetingdata_needs_drive_export=1,
            field_list_needs_drive_export=1,
        )

        @contextmanager
        def fake_connection():
            yield conn

        with (
            patch.object(gss, "get_connection", fake_connection),
            patch.object(gss, "export_book_layouts_to_google_drive") as export_layouts,
            patch.object(gss, "export_field_list_to_google_drive") as export_field_list,
            patch.object(gss, "export_changes_list_to_google_drive") as export_changes,
            patch.object(gss, "export_shared_settings_to_google_drive") as export_settings,
            patch.object(gss, "export_shared_contacts_to_google_drive") as export_contacts,
            patch.object(gss, "export_shared_meetingdata_to_google_drive") as export_meetingdata,
        ):
            gss.export_non_editor_access_data_to_google_drive()

        export_field_list.assert_called_once()
        export_layouts.assert_not_called()
        export_changes.assert_not_called()
        export_settings.assert_not_called()
        export_contacts.assert_not_called()
        export_meetingdata.assert_not_called()
        row = dict(conn.execute("SELECT field_list_needs_drive_export FROM google_sync_state WHERE id = 1").fetchone())
        self.assertEqual(int(row["field_list_needs_drive_export"] or 0), 0)

    def test_sign_out_does_not_flush_shared_editor_work(self) -> None:
        summary = {
            "account": {"status": "connected", "drive_scope_ready": 1},
            "state": {"changes_list_needs_drive_export": 0},
        }
        with (
            patch.object(google_sync_routes, "get_google_sync_summary", return_value=summary),
            patch.object(google_sync_routes, "pending_non_editor_drive_export_work_remaining", return_value=False),
            patch.object(google_sync_routes, "export_non_editor_access_data_to_google_drive") as export_access,
            patch.object(google_sync_routes, "export_shared_data_to_google_drive") as export_shared,
            patch.object(google_sync_routes, "clear_access_session") as clear_session,
        ):
            response = google_sync_routes.google_access_sign_out()

        export_shared.assert_not_called()
        export_access.assert_not_called()
        clear_session.assert_called_once()
        self.assertEqual(response.status_code, 303)

    def test_sign_out_saves_pending_edits_without_shared_contacts(self) -> None:
        summary = {
            "account": {"status": "connected", "drive_scope_ready": 1},
            "state": {"changes_list_needs_drive_export": 1},
        }
        with (
            patch.object(google_sync_routes, "get_google_sync_summary", return_value=summary),
            patch.object(google_sync_routes, "pending_non_editor_drive_export_work_remaining", return_value=True),
            patch.object(google_sync_routes, "export_non_editor_access_data_to_google_drive") as export_access,
            patch.object(google_sync_routes, "export_shared_data_to_google_drive") as export_shared,
            patch.object(google_sync_routes, "clear_access_session") as clear_session,
        ):
            response = google_sync_routes.google_access_sign_out()

        export_shared.assert_not_called()
        export_access.assert_called_once()
        clear_session.assert_called_once()
        self.assertEqual(response.status_code, 303)

    def test_sign_in_imports_meetingdata_for_print(self) -> None:
        summary = {
            "account": {"status": "connected", "drive_scope_ready": 1},
            "state": {},
        }
        with (
            patch.object(google_sync_routes, "save_access_session"),
            patch.object(google_sync_routes, "get_current_access_role", return_value="non_editor"),
            patch.object(google_sync_routes, "get_google_sync_summary", return_value=summary),
            patch.object(google_sync_routes, "import_print_role_shared_data_from_google_drive") as import_print,
        ):
            response = google_sync_routes.google_access_sign_in(
                access_role="non_editor",
                access_name="CV",
            )

        import_print.assert_called_once()
        self.assertEqual(response.status_code, 303)

    def test_print_role_import_pulls_meetingdata_without_writing_back(self) -> None:
        with (
            patch.object(gss, "import_book_layouts_from_google_drive") as import_layouts,
            patch.object(gss, "import_shared_meetingdata_from_google_drive") as import_meetingdata,
            patch.object(gss, "import_field_list_from_google_drive") as import_field_list,
            patch.object(gss, "import_changes_list_from_google_drive") as import_changes,
        ):
            gss.import_print_role_shared_data_from_google_drive()

        import_layouts.assert_called_once_with(export_if_missing=False)
        import_meetingdata.assert_called_once_with(export_if_missing=False)
        import_field_list.assert_called_once_with(export_if_missing=False)
        import_changes.assert_called_once()


class MeetingPreviewImportTests(unittest.TestCase):
    def tearDown(self) -> None:
        from app.services import field_list_service as fls

        fls._MEETINGDATA_PRINT_IMPORT_ATTEMPTED = False

    def test_empty_meeting_preview_imports_from_drive(self) -> None:
        from app.services import field_list_service as fls

        fls._MEETINGDATA_PRINT_IMPORT_ATTEMPTED = False
        with (
            patch.object(
                fls,
                "_field_list_meeting_preview_payload_from_meetingdata",
                side_effect=[{"sections": []}, {"sections": [{"field": "AL", "meeting": "Home", "rows": [{}]}]}],
            ),
            patch.object(fls, "_try_import_meetingdata_for_print", return_value=True) as import_meetingdata,
        ):
            payload = fls._field_list_meeting_preview_payload()

        import_meetingdata.assert_called_once()
        self.assertEqual(len(payload.get("sections") or []), 1)
