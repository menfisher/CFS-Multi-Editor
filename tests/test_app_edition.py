import unittest

from app.services.app_info_service import (
    MULTI_EDITOR_EDITION,
    SINGLE_EDITOR_EDITION,
    get_app_edition,
    update_manifest_url_matches_edition,
)


class AppEditionTests(unittest.TestCase):
    def test_this_app_is_multi_editor(self) -> None:
        self.assertEqual(get_app_edition(), MULTI_EDITOR_EDITION)

    def test_single_editor_url_is_rejected_for_multi_editor(self) -> None:
        self.assertFalse(
            update_manifest_url_matches_edition(
                "https://menfisher.github.io/contactsfreeshare-se-updates/app_update_manifest.json",
                MULTI_EDITOR_EDITION,
            )
        )

    def test_multi_editor_url_is_accepted_for_multi_editor(self) -> None:
        self.assertTrue(
            update_manifest_url_matches_edition(
                "https://menfisher.github.io/contactsfreeshare-updates/app_update_manifest.json",
                MULTI_EDITOR_EDITION,
            )
        )

    def test_multi_editor_url_is_rejected_for_single_editor(self) -> None:
        self.assertFalse(
            update_manifest_url_matches_edition(
                "https://menfisher.github.io/contactsfreeshare-updates/app_update_manifest.json",
                SINGLE_EDITOR_EDITION,
            )
        )


if __name__ == "__main__":
    unittest.main()
