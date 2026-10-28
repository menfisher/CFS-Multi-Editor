"""Invite email copy for new Share recipients."""

from __future__ import annotations

import unittest

from app.services.invite_email import (
    KEEP_PAGE_OPEN_PLAIN,
    RETRY_IMPORT_PLAIN,
    invite_display_name,
    invite_email_html,
    invite_email_plain_lines,
)


class InviteEmailTests(unittest.TestCase):
    def test_plain_body_uses_group_name_and_keep_open_retry(self) -> None:
        lines = invite_email_plain_lines(
            "owner@example.com",
            "AlMsLa (Shared)",
            [("r1@example.com", "https://share.example/?mode=shared&email=r1@example.com")],
        )
        text = "\n".join(lines)
        self.assertIn('shared the contact group "AlMsLa (Shared)" with you.', text)
        self.assertIn(KEEP_PAGE_OPEN_PLAIN, text)
        self.assertIn(RETRY_IMPORT_PLAIN, text)
        self.assertIn("Retry button", text)

    def test_html_body_bolds_keep_open_and_retry(self) -> None:
        html = invite_email_html(
            "owner@example.com",
            "r1@example.com",
            "AlMsLa (Shared)",
            "https://share.example/?mode=shared",
        )
        self.assertIn("<b>AlMsLa (Shared)</b>", html)
        self.assertIn("<strong>Keep this page open until the contacts finish downloading.</strong>", html)
        self.assertIn("<strong>Retry</strong>", html)
        self.assertIn("close the page or open another tab", html)

    def test_display_name_prefers_settings_when_resource_matches(self) -> None:
        self.assertEqual(
            invite_display_name(
                "TriState Separates (Shared)",
                settings_name="AlMsLa (Shared)",
                group_resource="contactGroups/abc",
                settings_resource="contactGroups/abc",
            ),
            "AlMsLa (Shared)",
        )


if __name__ == "__main__":
    unittest.main()
