import tempfile
import unittest
from pathlib import Path

from app.database import initialize_database, set_request_db_path
from app.services.google_sync_service import _import_book_layout_presets_payload
from app.services.preset_service import get_shared_contacts_group_name, save_book_layout_preset


class SharedGroupNameTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        set_request_db_path(Path(self._tempdir.name) / "app.sqlite3")
        initialize_database()

    def tearDown(self) -> None:
        set_request_db_path(None)
        self._tempdir.cleanup()

    def test_layout_save_does_not_restore_tristate_name(self) -> None:
        preset_id = save_book_layout_preset(
            None,
            {
                "name": "Pocket",
                "shared_contacts_group_name": "AlMsLa (Shared)",
                "is_default": "1",
            },
        )
        save_book_layout_preset(preset_id, {"name": "Pocket", "is_default": "1"})
        self.assertEqual(get_shared_contacts_group_name(), "AlMsLa (Shared)")

    def test_drive_layout_import_keeps_renamed_group(self) -> None:
        preset_id = save_book_layout_preset(
            None,
            {
                "name": "Pocket",
                "shared_contacts_group_name": "AlMsLa (Shared)",
                "is_default": "1",
            },
        )
        imported = _import_book_layout_presets_payload(
            [
                {
                    "id": preset_id,
                    "name": "Pocket",
                    "is_default": 1,
                    "shared_contacts_group_name": "TriState Separates (Shared)",
                }
            ]
        )
        self.assertTrue(imported)
        self.assertEqual(get_shared_contacts_group_name(), "AlMsLa (Shared)")

    def test_drive_layout_import_accepts_a_new_explicit_name(self) -> None:
        preset_id = save_book_layout_preset(
            None,
            {
                "name": "Pocket",
                "shared_contacts_group_name": "AlMsLa (Shared)",
                "is_default": "1",
            },
        )
        _import_book_layout_presets_payload(
            [
                {
                    "id": preset_id,
                    "name": "Pocket",
                    "is_default": 1,
                    "shared_contacts_group_name": "New Group (Shared)",
                }
            ]
        )
        self.assertEqual(get_shared_contacts_group_name(), "New Group (Shared)")


if __name__ == "__main__":
    unittest.main()
