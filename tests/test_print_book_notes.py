import unittest

from app.services.field_list_service import (
    _parse_print_book_notes,
    contact_notes_html,
    print_book_note_display,
    print_book_note_runs,
)


class PrintBookNoteFormattingTests(unittest.TestCase):
    def test_plain_pb_and_pa_still_extract(self) -> None:
        before, after = _parse_print_book_notes("pb/ Tom is son\npa/ lives nearby")
        self.assertEqual(print_book_note_display(before[0]), "Tom is son")
        self.assertEqual(print_book_note_display(after[0]), "lives nearby")

    def test_pb_and_pa_keep_bold_italic_and_underline(self) -> None:
        before, after = _parse_print_book_notes(
            "pb/ <strong>Tom</strong> is <em>son</em>\npa/ <u>lives</u> nearby"
        )
        before_runs = print_book_note_runs(before[0])
        after_runs = print_book_note_runs(after[0])
        self.assertEqual(print_book_note_display(before[0]), "Tom is son")
        self.assertEqual(
            [(run["text"], run["bold"], run["italic"], run["underline"]) for run in before_runs],
            [("Tom", True, False, False), (" is ", False, False, False), ("son", False, True, False)],
        )
        self.assertEqual(
            [(run["text"], run["underline"]) for run in after_runs if run["text"].strip()],
            [("lives", True), (" nearby", False)],
        )

    def test_pb_and_pa_keep_line_size_delta(self) -> None:
        before, after = _parse_print_book_notes(
            '<span data-note-size-delta="1.5">pb/ Tom is son</span>\n'
            '<span data-note-size-delta="-0.5">pa/ lives nearby</span>'
        )
        self.assertIn('data-note-size-delta="1.5"', before[0])
        self.assertIn("Tom is son", before[0])
        self.assertIn('data-note-size-delta="-0.5"', after[0])
        before_runs = print_book_note_runs(before[0])
        after_runs = print_book_note_runs(after[0])
        self.assertEqual(
            [(run["text"], run["size_delta"]) for run in before_runs if str(run["text"]).strip()],
            [("Tom is son", 1.5)],
        )
        self.assertEqual(
            [(run["text"], run["size_delta"]) for run in after_runs if str(run["text"]).strip()],
            [("lives nearby", -0.5)],
        )

    def test_note_size_delta_is_clamped(self) -> None:
        huge = print_book_note_runs('<span data-note-size-delta="20">Huge</span>')
        tiny = print_book_note_runs('<span data-note-size-delta="-20">Tiny</span>')
        self.assertEqual(huge[0]["size_delta"], 8.0)
        self.assertEqual(tiny[0]["size_delta"], -6.0)

    def test_contact_notes_html_keeps_safe_markup(self) -> None:
        html = str(contact_notes_html('<strong>Tom</strong> and <script>alert(1)</script>'))
        self.assertIn("<strong>Tom</strong>", html)
        self.assertNotIn("<script>", html)
        self.assertIn("alert(1)", html)


if __name__ == "__main__":
    unittest.main()
