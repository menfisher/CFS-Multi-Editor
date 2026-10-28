import unittest
from unittest.mock import patch


class ApplyPendingPhotoGooglePushTests(unittest.TestCase):
    @patch("app.services.contact_service._push_contact_photo_to_google_and_share", return_value="ok")
    @patch("app.services.contact_service.update_contact", return_value=True)
    @patch(
        "app.services.google_sync_service.sync_pending_contact_photo_from_drive_for_editor",
        return_value=True,
    )
    @patch(
        "app.services.changes_list_service.pending_photo_path_is_available",
        return_value=True,
    )
    @patch(
        "app.services.changes_list_service.find_pending_photo_location_for_contact",
        return_value={"edited": "/static/uploads/contact_photos/contact_7_photo.jpg"},
    )
    @patch(
        "app.services.contact_service.get_contact_detail",
        return_value={
            "id": 7,
            "family_name": "Cuevas",
            "given_name": "Murray",
            "photo": "/static/uploads/contact_photos/contact_7_photo.jpg",
            "google_contact_id": "people/123",
            "fields_text": "",
            "meetings_text": "",
            "group_membership": "",
        },
    )
    def test_already_local_pending_photo_still_pushes_to_google(
        self,
        _detail_mock,
        _pending_mock,
        _available_mock,
        _drive_mock,
        update_mock,
        push_mock,
    ) -> None:
        from app.services.contact_service import apply_pending_photo_to_contact

        notice = apply_pending_photo_to_contact(7)
        self.assertEqual(notice, "contact_photo_pending_already_applied")
        update_mock.assert_called_once()
        push_mock.assert_called_once_with(7)

    @patch("app.services.contact_service._push_contact_photo_to_google_and_share", return_value="ok")
    @patch("app.services.contact_service.update_contact", return_value=True)
    @patch(
        "app.services.changes_list_service.pending_photo_path_is_available",
        side_effect=lambda path: path.endswith("new.jpg"),
    )
    @patch(
        "app.services.changes_list_service.find_pending_photo_location_for_contact",
        return_value={"edited": "/static/uploads/contact_photos/contact_7_new.jpg"},
    )
    @patch(
        "app.services.contact_service.get_contact_detail",
        side_effect=[
            {
                "id": 7,
                "family_name": "Cuevas",
                "given_name": "Murray",
                "photo": "/static/uploads/contact_photos/contact_7_old.jpg",
                "google_contact_id": "people/123",
                "fields_text": "",
                "meetings_text": "",
                "group_membership": "",
            },
            {
                "id": 7,
                "family_name": "Cuevas",
                "given_name": "Murray",
                "photo": "/static/uploads/contact_photos/contact_7_old.jpg",
                "google_contact_id": "people/123",
                "fields_text": "",
                "meetings_text": "",
                "group_membership": "",
            },
        ],
    )
    def test_new_pending_photo_queues_and_pushes(
        self,
        _detail_mock,
        _pending_mock,
        _available_mock,
        update_mock,
        push_mock,
    ) -> None:
        from app.services.contact_service import apply_pending_photo_to_contact

        notice = apply_pending_photo_to_contact(7)
        self.assertEqual(notice, "contact_photo_pending_applied")
        self.assertEqual(
            update_mock.call_args.args[1]["photo"],
            "/static/uploads/contact_photos/contact_7_new.jpg",
        )
        push_mock.assert_called_once_with(7)

    @patch(
        "app.services.changes_list_service.pending_photo_path_is_available",
        return_value=True,
    )
    @patch(
        "app.services.changes_list_service.find_pending_photo_location_for_contact",
        return_value={"edited": "/static/uploads/contact_photos/contact_7_photo.jpg"},
    )
    def test_pending_photo_panel_stays_visible_when_already_local(
        self,
        _pending_mock,
        _available_mock,
    ) -> None:
        from app.services.contact_service import get_pending_photo_change_for_contact

        change = get_pending_photo_change_for_contact(
            {
                "family_name": "Cuevas",
                "given_name": "Murray",
                "photo": "/static/uploads/contact_photos/contact_7_photo.jpg",
            }
        )
        self.assertIsNotNone(change)
        self.assertTrue(change["already_local"])
        self.assertTrue(change["available"])


if __name__ == "__main__":
    unittest.main()
