import unittest

from app.services.contact_service import (
    _append_contact_edit_entry,
    _build_contact_edit_highlight_html,
    _clean_compare_text,
)


class ContactEditHighlightTests(unittest.TestCase):
    def test_notes_html_break_does_not_mark_next_line_deleted(self) -> None:
        html = _build_contact_edit_highlight_html(
            "dc - bitwardenr\nnw - Clarence",
            "dc - bitwarden<br>nw - Clarence",
        )

        self.assertNotIn("[deleted]", html)
        self.assertNotIn("&lt;br&gt;", html)
        self.assertIn('<mark class="meeting-edit-highlight">bitwarden</mark>', html)
        self.assertIn("<br>", html)
        self.assertIn("nw - Clarence", html)
        self.assertNotIn("nw - Clarence</mark>", html)

    def test_compare_treats_html_break_as_newline(self) -> None:
        self.assertEqual(
            _clean_compare_text("dc - bitwarden\nnw - Clarence"),
            _clean_compare_text("dc - bitwarden<br>nw - Clarence"),
        )

    def test_notes_html_break_only_is_not_logged_as_an_edit(self) -> None:
        entries: list[dict] = []
        _append_contact_edit_entry(
            entries,
            "Notes",
            "dc - bitwarden\nnw - Clarence",
            "dc - bitwarden<br>nw - Clarence",
        )
        self.assertEqual(entries, [])


if __name__ == "__main__":
    unittest.main()
