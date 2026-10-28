import unittest

from app.services.address_book_pdf_service import _meeting_row_line_height
from app.services.meeting_v2_service import _coerce_meeting_line_height


class MeetingLineHeightTests(unittest.TestCase):
    def test_coerce_clamps_and_rounds(self) -> None:
        self.assertEqual(_coerce_meeting_line_height("1.10"), 1.1)
        self.assertEqual(_coerce_meeting_line_height("0.2"), 0.8)
        self.assertEqual(_coerce_meeting_line_height("9"), 3.0)
        self.assertEqual(_coerce_meeting_line_height("", 1.2), 1.2)

    def test_pdf_row_uses_row_line_height(self) -> None:
        self.assertEqual(_meeting_row_line_height({"line_height": 1.1}, 1.2), 1.1)
        self.assertEqual(_meeting_row_line_height({}, 1.2), 1.2)
