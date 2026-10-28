import tempfile
import unittest
from pathlib import Path

from app.database import initialize_database, set_request_db_path
from app.services.meeting_v2_service import _ensure_section_layout, get_meeting_v2_section_print_preview


class MeetingSectionLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        db_path = Path(self._tempdir.name) / "app.sqlite3"
        set_request_db_path(db_path)
        initialize_database()

    def tearDown(self) -> None:
        set_request_db_path(None)
        self._tempdir.cleanup()

    def test_missing_section_does_not_raise(self) -> None:
        _ensure_section_layout(0)
        _ensure_section_layout(99999)

    def test_print_preview_missing_section_returns_none(self) -> None:
        self.assertIsNone(get_meeting_v2_section_print_preview(99999, ensure=False))


if __name__ == "__main__":
    unittest.main()
