import unittest

from app.services.meeting_v2_service import sanitize_inline_markup


class MeetingInlineMarkupTests(unittest.TestCase):
    def test_keeps_semantic_italic_bold_and_underline(self) -> None:
        html = "A <strong>bold</strong> <em>italic</em> <u>line</u>"
        self.assertEqual(sanitize_inline_markup(html), html)

    def test_converts_styled_span_italic_to_em(self) -> None:
        html = 'Hello <span style="font-style: italic">world</span>'
        self.assertEqual(sanitize_inline_markup(html), "Hello <em>world</em>")

    def test_converts_combined_span_styles(self) -> None:
        html = '<span style="font-weight: 700; font-style: italic; text-decoration: underline">Hi</span>'
        self.assertEqual(
            sanitize_inline_markup(html),
            "<strong><em><u>Hi</u></em></strong>",
        )

    def test_keeps_i_and_em_tags(self) -> None:
        self.assertEqual(sanitize_inline_markup("<i>slant</i>"), "<i>slant</i>")
        self.assertEqual(sanitize_inline_markup("<em>slant</em>"), "<em>slant</em>")


if __name__ == "__main__":
    unittest.main()
