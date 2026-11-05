import unittest

from app.services.google_sync_service import (
    _contact_person_payload,
    _google_plain_notes,
    _prefer_local_print_code_notes,
)


class GooglePlainNotesTests(unittest.TestCase):
    def test_html_break_becomes_real_newline(self) -> None:
        self.assertEqual(
            _google_plain_notes("nw - cmcjmc45<br>ac - Bitwarden"),
            "nw - cmcjmc45\nac - Bitwarden",
        )

    def test_existing_newlines_stay_newlines(self) -> None:
        self.assertEqual(
            _google_plain_notes("nw - ISSTIAUTNGr\r\ndc - bitwarden"),
            "nw - ISSTIAUTNGr\ndc - bitwarden",
        )

    def test_markup_is_stripped_for_google(self) -> None:
        self.assertEqual(
            _google_plain_notes("<strong>nw</strong> - x<br>ac - y"),
            "nw - x\nac - y",
        )

    def test_person_payload_sends_plain_notes(self) -> None:
        payload = _contact_person_payload(
            {
                "family_name": "Cuevas",
                "given_name": "Jean",
                "notes": "nw - cmcjmc45<br>ac - Bitwarden",
            }
        )
        self.assertEqual(
            payload["biographies"],
            [{"value": "nw - cmcjmc45\nac - Bitwarden", "contentType": "TEXT_PLAIN"}],
        )

    def test_print_codes_are_not_sent_to_google(self) -> None:
        self.assertEqual(
            _google_plain_notes("nw - password<br>pb/ Tom is son<br>pa/ lives nearby"),
            "nw - password\nTom is son\nlives nearby",
        )
        payload = _contact_person_payload(
            {
                "family_name": "Archer",
                "given_name": "Lindsey",
                "notes": "pb/ Tom is son\npa/ lives nearby\nnw - ISSTIAUTNGr",
            }
        )
        self.assertEqual(
            payload["biographies"][0]["value"],
            "Tom is son\nlives nearby\nnw - ISSTIAUTNGr",
        )

    def test_cfs_keeps_print_codes_when_google_notes_match(self) -> None:
        local = "pb/ Tom is son<br>nw - password"
        incoming = "Tom is son\nnw - password"
        self.assertEqual(_prefer_local_print_code_notes(local, incoming), local)

    def test_google_note_edits_still_replace_local_notes(self) -> None:
        local = "pb/ Tom is son\nnw - password"
        incoming = "changed in Google"
        self.assertEqual(_prefer_local_print_code_notes(local, incoming), incoming)


if __name__ == "__main__":
    unittest.main()
