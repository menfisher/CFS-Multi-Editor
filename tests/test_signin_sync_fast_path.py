import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from app.services import google_sync_service as gss


class SharedDriveStorageEnsureTests(unittest.TestCase):
    def setUp(self) -> None:
        gss._CONTACT_ONLY_SHARED_DRIVE_STORAGE_CACHE.clear()
        gss._SHARED_DRIVE_STORAGE_CACHE.clear()

    def tearDown(self) -> None:
        gss._CONTACT_ONLY_SHARED_DRIVE_STORAGE_CACHE.clear()
        gss._SHARED_DRIVE_STORAGE_CACHE.clear()

    def test_lists_shared_children_once_instead_of_per_file_finds(self) -> None:
        children = {
            gss.GOOGLE_DRIVE_SHARED_CONTACT_RECORDS_FOLDER_NAME: {
                "id": "records",
                "name": gss.GOOGLE_DRIVE_SHARED_CONTACT_RECORDS_FOLDER_NAME,
                "mimeType": "application/vnd.google-apps.folder",
            },
            gss.GOOGLE_DRIVE_SHARED_CONTACT_PHOTOS_FOLDER_NAME: {
                "id": "photos",
                "name": gss.GOOGLE_DRIVE_SHARED_CONTACT_PHOTOS_FOLDER_NAME,
                "mimeType": "application/vnd.google-apps.folder",
            },
            gss.GOOGLE_DRIVE_CONTACTS_CURRENT_FILE_NAME: {
                "id": "contacts",
                "name": gss.GOOGLE_DRIVE_CONTACTS_CURRENT_FILE_NAME,
                "mimeType": "application/json",
            },
            gss.GOOGLE_DRIVE_SHARED_SYNC_STATE_FILE_NAME: {
                "id": "sync-state",
                "name": gss.GOOGLE_DRIVE_SHARED_SYNC_STATE_FILE_NAME,
                "mimeType": "application/json",
            },
            gss.GOOGLE_DRIVE_EDITOR_LOCK_FILE_NAME: {
                "id": "lock",
                "name": gss.GOOGLE_DRIVE_EDITOR_LOCK_FILE_NAME,
                "mimeType": "application/json",
            },
            gss.GOOGLE_DRIVE_CONTACTS_FULL_FILE_NAME: {
                "id": "full",
                "name": gss.GOOGLE_DRIVE_CONTACTS_FULL_FILE_NAME,
                "mimeType": "application/json",
            },
            gss.GOOGLE_DRIVE_MEETINGDATA_CURRENT_FILE_NAME: {
                "id": "meeting",
                "name": gss.GOOGLE_DRIVE_MEETINGDATA_CURRENT_FILE_NAME,
                "mimeType": "application/json",
            },
            gss.GOOGLE_DRIVE_FIELD_LIST_CURRENT_FILE_NAME: {
                "id": "fields",
                "name": gss.GOOGLE_DRIVE_FIELD_LIST_CURRENT_FILE_NAME,
                "mimeType": "application/json",
            },
            gss.GOOGLE_DRIVE_SETTINGS_CURRENT_FILE_NAME: {
                "id": "settings",
                "name": gss.GOOGLE_DRIVE_SETTINGS_CURRENT_FILE_NAME,
                "mimeType": "application/json",
            },
            gss.GOOGLE_DRIVE_BOOK_LAYOUTS_CURRENT_FILE_NAME: {
                "id": "layouts",
                "name": gss.GOOGLE_DRIVE_BOOK_LAYOUTS_CURRENT_FILE_NAME,
                "mimeType": "application/json",
            },
            gss.GOOGLE_DRIVE_CHANGES_LIST_CURRENT_FILE_NAME: {
                "id": "changes",
                "name": gss.GOOGLE_DRIVE_CHANGES_LIST_CURRENT_FILE_NAME,
                "mimeType": "application/json",
            },
        }

        with (
            patch.object(gss, "ensure_google_drive_root_storage", return_value={"root_folder_id": "root"}),
            patch.object(
                gss,
                "_drive_find_child",
                return_value={
                    "id": "shared",
                    "name": gss.GOOGLE_DRIVE_SHARED_FOLDER_NAME,
                    "mimeType": "application/vnd.google-apps.folder",
                },
            ) as find_child,
            patch.object(gss, "_drive_children_by_name", return_value=children) as list_children,
            patch.object(gss, "_drive_create_folder") as create_folder,
            patch.object(gss, "_drive_create_json_file") as create_file,
        ):
            result = gss._ensure_shared_drive_storage("token")
            again = gss._ensure_shared_drive_storage("token")

        self.assertEqual(result["contacts_current_file_id"], "contacts")
        self.assertEqual(result["changes_list_current_file_id"], "changes")
        self.assertEqual(again["settings_current_file_id"], "settings")
        self.assertEqual(find_child.call_count, 1)
        self.assertEqual(list_children.call_count, 1)
        create_folder.assert_not_called()
        create_file.assert_not_called()

    def test_signin_job_reuses_passed_storage(self) -> None:
        storage = {
            "contacts_current_file_id": "contacts",
            "shared_sync_state_file_id": "sync",
        }
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE google_sync_state (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              signin_sync_in_progress INTEGER NOT NULL DEFAULT 0,
              signin_sync_phase TEXT NOT NULL DEFAULT '',
              signin_sync_error TEXT NOT NULL DEFAULT '',
              signin_sync_total_count INTEGER NOT NULL DEFAULT 0,
              signin_sync_processed_count INTEGER NOT NULL DEFAULT 0,
              signin_sync_current_item TEXT NOT NULL DEFAULT '',
              signin_sync_updated_at TEXT NOT NULL DEFAULT '',
              last_import_at TEXT NOT NULL DEFAULT '',
              last_import_account_email TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO google_sync_state (id) VALUES (1);
            """
        )
        conn.commit()

        @contextmanager
        def fake_get_connection():
            yield conn

        with (
            patch.object(gss, "get_connection", fake_get_connection),
            patch.object(gss, "_touch_signin_sync"),
            patch.object(gss, "_get_valid_access_token", return_value=("token", "user@example.com")),
            patch.object(gss, "_ensure_shared_drive_storage") as ensure_storage,
            patch.object(gss, "_load_shared_sync_state", return_value={}),
            patch.object(gss, "import_shared_contacts_from_google_drive"),
            patch.object(gss, "import_book_layouts_from_google_drive"),
            patch.object(gss, "import_shared_meetingdata_from_google_drive"),
            patch.object(gss, "import_field_list_from_google_drive"),
            patch.object(gss, "import_changes_list_from_google_drive"),
        ):
            gss._run_signin_sync_job(skip_settings=True, storage=storage)

        ensure_storage.assert_not_called()
        conn.close()


class DriveListChildrenPaginationTests(unittest.TestCase):
    def test_drive_list_children_follows_next_page_token(self) -> None:
        responses = [
            {
                "files": [{"id": "a", "name": "a.json"}],
                "nextPageToken": "page-2",
            },
            {
                "files": [{"id": "b", "name": "b.json"}],
            },
        ]

        with patch.object(gss, "_drive_json_request", side_effect=responses) as mock_request:
            files = gss._drive_list_children("token", parent_id="folder-1")

        self.assertEqual([item["id"] for item in files], ["a", "b"])
        self.assertEqual(mock_request.call_count, 2)
        first_url = mock_request.call_args_list[0].args[0]
        second_url = mock_request.call_args_list[1].args[0]
        self.assertIn("pageSize=1000", first_url)
        self.assertNotIn("pageToken=", first_url)
        self.assertIn("pageToken=page-2", second_url)


class SharedContactsImportFastPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE contacts (
              id INTEGER PRIMARY KEY,
              family_name TEXT NOT NULL DEFAULT '',
              given_name TEXT NOT NULL DEFAULT '',
              photo TEXT NOT NULL DEFAULT '',
              photo_drive_file_id TEXT NOT NULL DEFAULT '',
              photo_sync_revision INTEGER NOT NULL DEFAULT 0,
              shared_drive_revision INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE google_sync_state (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              contacts_sync_revision INTEGER NOT NULL DEFAULT 0,
              pending_upload_count INTEGER NOT NULL DEFAULT 0,
              needs_upload_reminder INTEGER NOT NULL DEFAULT 0,
              needs_drive_export INTEGER NOT NULL DEFAULT 0,
              last_sync_error TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO google_sync_state (id, contacts_sync_revision) VALUES (1, 4);
            INSERT INTO contacts (
              id, family_name, given_name, shared_drive_revision
            ) VALUES (1, 'Smith', 'Jane', 2);
            """
        )
        self.conn.commit()

        @contextmanager
        def fake_get_connection():
            yield self.conn

        self.connection_patcher = patch.object(gss, "get_connection", fake_get_connection)
        self.connection_patcher.start()

    def tearDown(self) -> None:
        self.connection_patcher.stop()
        self.conn.close()

    def test_skips_all_drive_work_when_dataset_revision_matches(self) -> None:
        with (
            patch.object(
                gss,
                "get_google_sync_summary",
                return_value={"state": {"contacts_sync_revision": 10}},
            ),
            patch.object(gss, "_drive_load_json_file_content") as load_json,
            patch.object(gss, "_drive_children_by_name") as list_children,
        ):
            result = gss.import_shared_contacts_from_google_drive(
                access_token="token",
                storage={"contacts_current_file_id": "current"},
                remote_sync_state={"contacts_sync_revision": 10},
            )

        self.assertTrue(result)
        load_json.assert_not_called()
        list_children.assert_not_called()

    def test_fast_path_adopts_revision_without_listing_or_downloading(self) -> None:
        manifest = {
            "format": "contactsfreeshare.shared_contacts.v2",
            "contacts": [
                {
                    "id": 1,
                    "family_name": "Smith",
                    "given_name": "Jane",
                    "shared_drive_revision": 2,
                    "file_name": "contact_1.json",
                    "content_digest": "digest",
                }
            ],
            "assignment_options": [{"label": "A", "value": "a"}],
        }

        with (
            patch.object(
                gss,
                "get_google_sync_summary",
                return_value={"state": {"contacts_sync_revision": 4}},
            ),
            patch.object(gss, "_drive_load_json_file_content", return_value=manifest),
            patch.object(gss, "_drive_children_by_name") as list_children,
            patch.object(gss, "_backfill_shared_contacts_manifest_digests") as backfill,
            patch.object(gss, "_store_local_sync_revisions") as store_revisions,
            patch.object(gss, "_replace_contact_assignment_options") as replace_options,
            patch.object(gss, "_import_changed_contact_photos_from_google_drive") as import_photos,
        ):
            result = gss.import_shared_contacts_from_google_drive(
                access_token="token",
                storage={
                    "contacts_current_file_id": "current",
                    "contact_records_folder_id": "records",
                },
                remote_sync_state={"contacts_sync_revision": 5},
                backfill_manifest_digests=False,
            )

        self.assertTrue(result)
        list_children.assert_not_called()
        backfill.assert_not_called()
        import_photos.assert_not_called()
        replace_options.assert_called_once()
        store_revisions.assert_called_once_with(contacts_sync_revision=5)

    def test_peek_only_path_skips_folder_listing(self) -> None:
        manifest = {
            "format": "contactsfreeshare.shared_contacts.v2",
            "contacts": [
                {
                    "id": 1,
                    "family_name": "Smith",
                    "given_name": "Jane",
                    "shared_drive_revision": 3,
                    "file_name": "contact_1.json",
                    "content_digest": "same-digest",
                }
            ],
            "assignment_options": [],
        }

        with (
            patch.object(
                gss,
                "get_google_sync_summary",
                return_value={"state": {"contacts_sync_revision": 4}},
            ),
            patch.object(gss, "_drive_load_json_file_content", return_value=manifest),
            patch.object(gss, "_peek_shared_contact_import_action", return_value="revision_only"),
            patch.object(gss, "_sync_shared_contact_revision_only") as sync_revision,
            patch.object(gss, "_drive_children_by_name") as list_children,
            patch.object(gss, "_backfill_shared_contacts_manifest_digests") as backfill,
            patch.object(gss, "_store_local_sync_revisions"),
            patch.object(gss, "_replace_contact_assignment_options"),
            patch.object(gss, "_import_changed_contact_photos_from_google_drive"),
        ):
            result = gss.import_shared_contacts_from_google_drive(
                access_token="token",
                storage={
                    "contacts_current_file_id": "current",
                    "contact_records_folder_id": "records",
                },
                remote_sync_state={"contacts_sync_revision": 5},
                backfill_manifest_digests=False,
            )

        self.assertTrue(result)
        sync_revision.assert_called_once()
        list_children.assert_not_called()
        backfill.assert_not_called()


class SigninSyncUnlockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE google_sync_state (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              signin_sync_in_progress INTEGER NOT NULL DEFAULT 0,
              signin_sync_phase TEXT NOT NULL DEFAULT '',
              signin_sync_error TEXT NOT NULL DEFAULT '',
              signin_sync_total_count INTEGER NOT NULL DEFAULT 0,
              signin_sync_processed_count INTEGER NOT NULL DEFAULT 0,
              signin_sync_current_item TEXT NOT NULL DEFAULT '',
              signin_sync_updated_at TEXT NOT NULL DEFAULT '',
              last_import_at TEXT NOT NULL DEFAULT '',
              last_import_account_email TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO google_sync_state (id, signin_sync_in_progress) VALUES (1, 1);
            """
        )
        self.conn.commit()

        @contextmanager
        def fake_get_connection():
            yield self.conn

        self.connection_patcher = patch.object(gss, "get_connection", fake_get_connection)
        self.connection_patcher.start()

    def tearDown(self) -> None:
        self.connection_patcher.stop()
        self.conn.close()

    def test_unlocks_before_deferred_phases_and_skips_digest_backfill(self) -> None:
        call_order: list[str] = []
        unlocked_before_deferred = {"value": False}

        def mark(name: str, *args, **kwargs):
            if name.startswith("deferred:"):
                row = self.conn.execute(
                    "SELECT signin_sync_in_progress FROM google_sync_state WHERE id = 1"
                ).fetchone()
                if int(row["signin_sync_in_progress"] or 0) == 0:
                    unlocked_before_deferred["value"] = True
            call_order.append(name)
            return True

        with (
            patch.object(gss, "_touch_signin_sync"),
            patch.object(gss, "_get_valid_access_token", return_value=("token", "user@example.com")),
            patch.object(gss, "_ensure_shared_drive_storage", return_value={"root": "x"}),
            patch.object(gss, "_load_shared_sync_state", return_value={}),
            patch.object(
                gss,
                "import_shared_settings_from_google_drive",
                side_effect=lambda **kwargs: mark("settings"),
            ),
            patch.object(
                gss,
                "import_shared_contacts_from_google_drive",
                side_effect=lambda **kwargs: mark(
                    f"contacts:backfill={kwargs.get('backfill_manifest_digests')}"
                ),
            ),
            patch.object(
                gss,
                "import_book_layouts_from_google_drive",
                side_effect=lambda **kwargs: mark("deferred:book_layouts"),
            ),
            patch.object(
                gss,
                "import_shared_meetingdata_from_google_drive",
                side_effect=lambda **kwargs: mark("deferred:meetingdata"),
            ),
            patch.object(
                gss,
                "import_field_list_from_google_drive",
                side_effect=lambda **kwargs: mark("deferred:field_list"),
            ),
            patch.object(
                gss,
                "import_changes_list_from_google_drive",
                side_effect=lambda **kwargs: mark("deferred:changes_list"),
            ),
        ):
            gss._run_signin_sync_job(skip_settings=False)

        self.assertEqual(
            call_order,
            [
                "settings",
                "contacts:backfill=False",
                "deferred:book_layouts",
                "deferred:meetingdata",
                "deferred:field_list",
                "deferred:changes_list",
            ],
        )
        self.assertTrue(unlocked_before_deferred["value"])
        state = dict(self.conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone())
        self.assertEqual(int(state["signin_sync_in_progress"] or 0), 0)
        self.assertEqual(state["last_import_account_email"], "user@example.com")

    def test_deferred_phase_failure_does_not_block_ui(self) -> None:
        with (
            patch.object(gss, "_touch_signin_sync"),
            patch.object(gss, "_get_valid_access_token", return_value=("token", "user@example.com")),
            patch.object(gss, "_ensure_shared_drive_storage", return_value={"root": "x"}),
            patch.object(gss, "_load_shared_sync_state", return_value={}),
            patch.object(gss, "import_shared_settings_from_google_drive"),
            patch.object(gss, "import_shared_contacts_from_google_drive"),
            patch.object(gss, "import_book_layouts_from_google_drive"),
            patch.object(
                gss,
                "import_shared_meetingdata_from_google_drive",
                side_effect=RuntimeError("meetingdata boom"),
            ),
            patch.object(gss, "import_field_list_from_google_drive") as field_list,
            patch.object(gss, "import_changes_list_from_google_drive") as changes_list,
        ):
            gss._run_signin_sync_job(skip_settings=False)

        field_list.assert_called_once()
        changes_list.assert_called_once()
        state = dict(self.conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone())
        self.assertEqual(int(state["signin_sync_in_progress"] or 0), 0)
        self.assertEqual(state["signin_sync_error"], "")


class SharedContactParallelDownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE contacts (
              id INTEGER PRIMARY KEY,
              family_name TEXT NOT NULL DEFAULT '',
              given_name TEXT NOT NULL DEFAULT '',
              photo TEXT NOT NULL DEFAULT '',
              photo_drive_file_id TEXT NOT NULL DEFAULT '',
              photo_sync_revision INTEGER NOT NULL DEFAULT 0,
              shared_drive_revision INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE google_sync_state (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              contacts_sync_revision INTEGER NOT NULL DEFAULT 0,
              pending_upload_count INTEGER NOT NULL DEFAULT 0,
              needs_upload_reminder INTEGER NOT NULL DEFAULT 0,
              needs_drive_export INTEGER NOT NULL DEFAULT 0,
              last_sync_error TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO google_sync_state (id, contacts_sync_revision) VALUES (1, 1);
            INSERT INTO contacts (id, family_name, given_name, shared_drive_revision)
            VALUES (1, 'A', 'One', 1), (2, 'B', 'Two', 1);
            """
        )
        self.conn.commit()

        @contextmanager
        def fake_get_connection():
            yield self.conn

        self.connection_patcher = patch.object(gss, "get_connection", fake_get_connection)
        self.connection_patcher.start()

    def tearDown(self) -> None:
        self.connection_patcher.stop()
        self.conn.close()

    def test_downloads_needed_contacts_via_thread_pool(self) -> None:
        manifest = {
            "format": "contactsfreeshare.shared_contacts.v2",
            "contacts": [
                {
                    "id": 1,
                    "family_name": "A",
                    "given_name": "One",
                    "shared_drive_revision": 2,
                    "file_name": "contact_1.json",
                },
                {
                    "id": 2,
                    "family_name": "B",
                    "given_name": "Two",
                    "shared_drive_revision": 2,
                    "file_name": "contact_2.json",
                },
            ],
            "assignment_options": [],
        }
        payloads = {
            "file-1": {
                "format": "contactsfreeshare.shared_contact.v1",
                "contact": {"id": 1, "shared_drive_revision": 2},
            },
            "file-2": {
                "format": "contactsfreeshare.shared_contact.v1",
                "contact": {"id": 2, "shared_drive_revision": 2},
            },
        }

        def load_json(_token, file_id):
            if file_id == "current":
                return manifest
            return payloads[file_id]

        with (
            patch.object(
                gss,
                "get_google_sync_summary",
                return_value={"state": {"contacts_sync_revision": 1}},
            ),
            patch.object(gss, "_drive_load_json_file_content", side_effect=load_json),
            patch.object(gss, "_peek_shared_contact_import_action", return_value=None),
            patch.object(
                gss,
                "_drive_children_by_name",
                return_value={
                    "contact_1.json": {"id": "file-1"},
                    "contact_2.json": {"id": "file-2"},
                },
            ),
            patch.object(gss, "_resolve_shared_contact_import_action", return_value="import"),
            patch.object(gss, "_merge_shared_contact_record_from_drive") as merge,
            patch.object(gss, "_backfill_shared_contacts_manifest_digests") as backfill,
            patch.object(gss, "_store_local_sync_revisions"),
            patch.object(gss, "_replace_contact_assignment_options"),
            patch.object(gss, "_import_changed_contact_photos_from_google_drive"),
            patch.object(gss, "ThreadPoolExecutor", wraps=gss.ThreadPoolExecutor) as executor_cls,
        ):
            result = gss.import_shared_contacts_from_google_drive(
                access_token="token",
                storage={
                    "contacts_current_file_id": "current",
                    "contact_records_folder_id": "records",
                },
                remote_sync_state={"contacts_sync_revision": 2},
                backfill_manifest_digests=False,
            )

        self.assertTrue(result)
        self.assertEqual(merge.call_count, 2)
        backfill.assert_not_called()
        executor_cls.assert_called()
        self.assertEqual(executor_cls.call_args.kwargs.get("max_workers"), 2)


if __name__ == "__main__":
    unittest.main()
