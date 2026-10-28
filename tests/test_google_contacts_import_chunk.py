import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from app.database import get_connection, initialize_database, set_request_db_path
from app.services import google_sync_service as gss


def _person(resource_name: str, family_name: str) -> dict:
    return {
        "resourceName": resource_name,
        "etag": f"etag-{family_name}",
        "names": [{"displayName": family_name, "familyName": family_name, "givenName": "Pat"}],
    }


class GoogleContactsImportChunkTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        db_path = Path(self._tempdir.name) / "app.sqlite3"
        set_request_db_path(db_path)
        initialize_database()

    def tearDown(self) -> None:
        set_request_db_path(None)
        self._tempdir.cleanup()

    def test_chunked_import_saves_each_page_and_resumes(self) -> None:
        def fake_api(url: str, _access_token: str, **_kwargs) -> dict:
            page_token = parse_qs(urlparse(url).query).get("pageToken", [""])[0]
            if page_token == "page-2":
                return {
                    "connections": [_person("people/2", "Baker")],
                    "nextSyncToken": "sync-token-1",
                    "totalPeople": 2,
                }
            return {
                "connections": [_person("people/1", "Adams")],
                "nextPageToken": "page-2",
                "totalPeople": 2,
            }

        with (
            patch.object(gss, "_get_valid_access_token", return_value=("token", "mindy@example.com")),
            patch.object(gss, "_contact_groups_map", return_value={}),
            patch.object(gss, "_api_get_json", side_effect=fake_api),
        ):
            first = gss.import_google_contacts_chunk()
            self.assertFalse(first["done"])
            self.assertEqual(first["imported"], 1)
            self.assertEqual(first["total_available"], 2)
            self.assertTrue(gss.people_import_in_progress())

            with get_connection() as conn:
                names = [row["family_name"] for row in conn.execute("SELECT family_name FROM contacts")]
                state = dict(conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone())
            self.assertEqual(names, ["Adams"])
            self.assertEqual(state["signin_sync_current_item"], "page-2")
            self.assertEqual(state["last_import_at"], "")

            second = gss.import_google_contacts_chunk()
            self.assertTrue(second["done"])
            self.assertEqual(second["imported"], 2)
            self.assertFalse(gss.people_import_in_progress())

            with get_connection() as conn:
                names = [
                    row["family_name"]
                    for row in conn.execute("SELECT family_name FROM contacts ORDER BY family_name")
                ]
                state = dict(conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone())
            self.assertEqual(names, ["Adams", "Baker"])
            self.assertTrue(state["last_import_at"])
            self.assertEqual(state["google_contacts_sync_token"], "sync-token-1")

    def test_chunked_import_skips_duplicates_when_same_page_retries(self) -> None:
        calls = {"count": 0}

        def fake_api(url: str, _access_token: str, **_kwargs) -> dict:
            calls["count"] += 1
            return {
                "connections": [_person("people/1", "Adams")],
                "nextPageToken": "page-2",
                "totalPeople": 2,
            }

        with (
            patch.object(gss, "_get_valid_access_token", return_value=("token", "mindy@example.com")),
            patch.object(gss, "_contact_groups_map", return_value={}),
            patch.object(gss, "_api_get_json", side_effect=fake_api),
        ):
            first = gss.import_google_contacts_chunk()
            with get_connection() as conn:
                conn.execute(
                    """
                    UPDATE google_sync_state
                    SET signin_sync_current_item = ''
                    WHERE id = 1
                    """
                )
                conn.commit()
            retry = gss.import_google_contacts_chunk()

        self.assertEqual(first["imported"], 1)
        self.assertEqual(retry["imported"], 1)
        self.assertFalse(retry["done"])
        with get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(calls["count"], 2)

    def test_mobile_import_skips_listing_google_labels(self) -> None:
        def fake_api(url: str, _access_token: str, **_kwargs) -> dict:
            self.assertNotIn("contactGroups", url)
            self.assertNotIn("requestSyncToken", url)
            return {
                "connections": [_person("people/1", "Adams")],
                "totalPeople": 1,
                "nextSyncToken": "sync-token-1",
            }

        with (
            patch.dict("os.environ", {"DATA_ROOT": "/data"}),
            patch.object(gss, "_get_valid_access_token", return_value=("token", "mindy@example.com")),
            patch.object(gss, "_contact_groups_map", side_effect=AssertionError("labels should not be listed on mobile")),
            patch.object(gss, "_api_get_json", side_effect=fake_api),
            patch.object(gss, "_mobile_people_get_json", side_effect=fake_api),
        ):
            result = gss.import_google_contacts_chunk()

        self.assertTrue(result["done"])
        self.assertEqual(result["imported"], 1)


if __name__ == "__main__":
    unittest.main()
