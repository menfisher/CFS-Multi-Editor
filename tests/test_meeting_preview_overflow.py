import unittest

from app.config import PROJECT_ROOT
from app.services.address_book_pdf_service import (
    _PdfWriter,
    _append_styled_meeting_pdf_lines,
    _html_styled_lines,
)
from app.services.field_list_service import _field_list_print_css


class MeetingPreviewOverflowTests(unittest.TestCase):
    def test_print_preview_css_does_not_wrap_long_flow_lines(self) -> None:
        template = (
            PROJECT_ROOT / "app" / "templates" / "meetings" / "v2_print_preview.html"
        ).read_text(encoding="utf-8")
        flow_css = template.split(".print-preview-flow-text {", 1)[1].split("}", 1)[0]
        self.assertIn("white-space: pre;", flow_css)
        self.assertNotIn("pre-line", flow_css)
        self.assertNotIn("pre-wrap", flow_css)

    def test_field_list_print_css_does_not_wrap_long_flow_lines(self) -> None:
        css = _field_list_print_css({})
        flow_css = css.split(".print-field-list-meeting-preview-flow-text {", 1)[1].split("}", 1)[0]
        self.assertIn("white-space: pre;", flow_css)
        self.assertNotIn("pre-line", flow_css)

    def test_pdf_draws_a_long_flow_line_without_wrapping(self) -> None:
        writer = _PdfWriter(400, 400, font_family="Times")
        page = writer.add_page()
        text = "J. Blades to Denham Springs"

        def tw(value: str, size: float, **kwargs) -> float:
            return len(str(value)) * 6.0

        _append_styled_meeting_pdf_lines(
            page,
            x_base=20,
            y=200,
            cell_width=40,
            align="left",
            styled_lines=_html_styled_lines(text),
            font_size=10,
            line_step=12,
            tw=tw,
            font_family="Times",
        )
        self.assertEqual([line.text for line in page.lines], [text])
        self.assertEqual({line.y for line in page.lines}, {200})
        drawn_right = page.lines[0].x + tw(page.lines[0].text, 10)
        self.assertGreater(drawn_right, 20 + 40)


if __name__ == "__main__":
    unittest.main()