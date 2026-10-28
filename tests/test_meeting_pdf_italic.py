import unittest

from app.services.address_book_pdf_service import (
    _PdfWriter,
    _append_styled_meeting_pdf_lines,
    _html_styled_lines,
)


class MeetingPdfItalicTests(unittest.TestCase):
    def test_em_and_i_tags_are_italic(self) -> None:
        lines = _html_styled_lines("Hello <em>world</em> and <i>more</i>")
        self.assertEqual(
            [(run["text"], run["italic"], run["bold"]) for run in lines[0]],
            [
                ("Hello ", False, False),
                ("world", True, False),
                (" and ", False, False),
                ("more", True, False),
            ],
        )

    def test_italic_span_style_is_preserved(self) -> None:
        lines = _html_styled_lines('<span style="font-style:italic">Daytime</span> meeting')
        self.assertEqual(lines[0][0]["text"], "Daytime")
        self.assertTrue(lines[0][0]["italic"])
        self.assertEqual(lines[0][1]["text"], " meeting")
        self.assertFalse(lines[0][1]["italic"])

    def test_pdf_lines_use_italic_font(self) -> None:
        writer = _PdfWriter(400, 400, font_family="Times")
        page = writer.add_page()
        _append_styled_meeting_pdf_lines(
            page,
            x_base=20,
            y=200,
            cell_width=200,
            align="left",
            styled_lines=_html_styled_lines("<em>Daytime</em> meeting"),
            font_size=10,
            line_step=12,
            tw=lambda text, size, **kwargs: 10,
            font_family="Times",
        )
        drawn = [(line.text, line.italic, line.bold) for line in page.lines]
        self.assertEqual(drawn, [("Daytime", True, False), (" meeting", False, False)])

    def test_cell_italic_applies_to_plain_text(self) -> None:
        writer = _PdfWriter(400, 400, font_family="Times")
        page = writer.add_page()
        _append_styled_meeting_pdf_lines(
            page,
            x_base=20,
            y=200,
            cell_width=200,
            align="left",
            styled_lines=_html_styled_lines("All italic"),
            font_size=10,
            line_step=12,
            tw=lambda text, size, **kwargs: 10,
            font_family="Times",
            cell_italic=True,
        )
        self.assertEqual(len(page.lines), 1)
        self.assertEqual(page.lines[0].text, "All italic")
        self.assertTrue(page.lines[0].italic)
