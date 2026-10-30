import sqlite3
import unittest

from app.services.google_sync_service import (
    CFS_DO_NOT_PRINT_FIELD,
    CFS_MEETS_IN_STATE_FIELD,
    _contact_person_payload,
    _do_not_print_from_user_defined,
    _google_person_import_fields,
    _insert_contact_from_google_person,
    _meets_in_state_from_user_defined,
    _update_contact_from_google_person,
)


class CfsAppFieldsGoogleRoundtripTests(unittest.TestCase):
    def test_reads_meets_in_state_value(self) -> None:
        self.assertEqual(
            _meets_in_state_from_user_defined(
                [{"key": CFS_MEETS_IN_STATE_FIELD, "value": "MS"}]
            ),
            "MS",
        )

    def test_reads_do_not_print_value(self) -> None:
        self.assertEqual(
            _do_not_print_from_user_defined(
                [{"key": CFS_DO_NOT_PRINT_FIELD, "value": "1"}]
            ),
            1,
        )
        self.assertEqual(_do_not_print_from_user_defined([]), 0)

    def test_upload_sends_typed_state_and_do_not_print(self) -> None:
        payload = _contact_person_payload(
            {
                "family_name": "Wiley",
                "given_name": "Linda",
                "meets_in_state": "LA",
                "do_not_print": 1,
            }
        )
        user_defined = payload.get("userDefined") or []
        self.assertIn({"key": CFS_MEETS_IN_STATE_FIELD, "value": "LA"}, user_defined)
        self.assertIn({"key": CFS_DO_NOT_PRINT_FIELD, "value": "1"}, user_defined)

    def test_upload_omits_do_not_print_when_unchecked(self) -> None:
        payload = _contact_person_payload(
            {
                "family_name": "Wiley",
                "given_name": "Linda",
                "do_not_print": 0,
            }
        )
        keys = [item.get("key") for item in payload.get("userDefined") or []]
        self.assertNotIn(CFS_DO_NOT_PRINT_FIELD, keys)

    def test_upload_does_not_duplicate_dedicated_custom_fields(self) -> None:
        payload = _contact_person_payload(
            {
                "family_name": "Wiley",
                "given_name": "Linda",
                "meets_in_state": "LA",
                "do_not_print": 1,
                "custom_fields": [
                    {"field_type": CFS_MEETS_IN_STATE_FIELD, "field_value": "TX"},
                    {"field_type": CFS_DO_NOT_PRINT_FIELD, "field_value": "1"},
                ],
            }
        )
        user_defined = payload.get("userDefined") or []
        self.assertEqual(
            [item for item in user_defined if item.get("key") == CFS_MEETS_IN_STATE_FIELD],
            [{"key": CFS_MEETS_IN_STATE_FIELD, "value": "LA"}],
        )
        self.assertEqual(
            [item for item in user_defined if item.get("key") == CFS_DO_NOT_PRINT_FIELD],
            [{"key": CFS_DO_NOT_PRINT_FIELD, "value": "1"}],
        )

    def test_import_fields_keep_state_and_do_not_print(self) -> None:
        fields = _google_person_import_fields(
            {
                "resourceName": "people/c-home",
                "etag": "etag-1",
                "names": [{"givenName": "Linda", "familyName": "Wiley"}],
                "userDefined": [
                    {"key": CFS_MEETS_IN_STATE_FIELD, "value": "LA"},
                    {"key": CFS_DO_NOT_PRINT_FIELD, "value": "1"},
                ],
            },
            {},
        )
        self.assertIsNotNone(fields)
        self.assertEqual(fields["meets_in_state"], "LA")
        self.assertEqual(fields["do_not_print"], 1)


class CfsAppFieldsGooglePersistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE contacts (
              id INTEGER PRIMARY KEY,
              selected TEXT NOT NULL DEFAULT '',
              google_contact_id TEXT NOT NULL DEFAULT '',
              etag TEXT NOT NULL DEFAULT '',
              last_updated TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT '',
              fields_text TEXT NOT NULL DEFAULT '',
              meetings_text TEXT NOT NULL DEFAULT '',
              group_membership TEXT NOT NULL DEFAULT '',
              family_name TEXT NOT NULL DEFAULT '',
              given_name TEXT NOT NULL DEFAULT '',
              photo TEXT NOT NULL DEFAULT '',
              photo_drive_file_id TEXT NOT NULL DEFAULT '',
              birthday TEXT NOT NULL DEFAULT '',
              notes TEXT NOT NULL DEFAULT '',
              mtg_home_elder_flag TEXT NOT NULL DEFAULT '',
              meets_in_state TEXT NOT NULL DEFAULT '',
              do_not_print INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE phones (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              contact_id INTEGER NOT NULL,
              position INTEGER NOT NULL DEFAULT 1,
              phone_type TEXT NOT NULL DEFAULT '',
              phone_value TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE emails (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              contact_id INTEGER NOT NULL,
              position INTEGER NOT NULL DEFAULT 1,
              email_type TEXT NOT NULL DEFAULT '',
              email_value TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE relationships (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              contact_id INTEGER NOT NULL,
              position INTEGER NOT NULL DEFAULT 1,
              relation_type TEXT NOT NULL DEFAULT '',
              relation_value TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE addresses (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              contact_id INTEGER NOT NULL,
              position INTEGER NOT NULL DEFAULT 1,
              address_type TEXT NOT NULL DEFAULT '',
              street_address TEXT NOT NULL DEFAULT '',
              extended_address TEXT NOT NULL DEFAULT '',
              city TEXT NOT NULL DEFAULT '',
              region TEXT NOT NULL DEFAULT '',
              postal_code TEXT NOT NULL DEFAULT '',
              formatted_address TEXT NOT NULL DEFAULT '',
              coordinates TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE custom_fields (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              contact_id INTEGER NOT NULL,
              position INTEGER NOT NULL DEFAULT 1,
              field_type TEXT NOT NULL DEFAULT '',
              field_value TEXT NOT NULL DEFAULT ''
            );
            """
        )

    def tearDown(self) -> None:
        self.conn.close()

    def _fields(self, *, meets: str = "LA", do_not_print: int = 1) -> dict:
        user_defined = []
        if meets:
            user_defined.append({"key": CFS_MEETS_IN_STATE_FIELD, "value": meets})
        if do_not_print:
            user_defined.append({"key": CFS_DO_NOT_PRINT_FIELD, "value": "1"})
        return {
            "resource_name": "people/c-home",
            "etag": "etag-1",
            "fields_text": "LA North",
            "meetings_text": "DeRidder (WILEY)",
            "group_names": [],
            "family_name": "Wiley",
            "given_name": "Linda",
            "photo": "",
            "birthday": "",
            "notes": "",
            "relations": [],
            "phones": [],
            "emails": [],
            "addresses": [],
            "user_defined": user_defined,
            "user_defined_coordinates": {},
            "url_coordinate_candidates": [],
            "mtg_home_elder_flag": "",
            "meets_in_state": meets,
            "do_not_print": do_not_print,
        }

    def test_insert_stores_state_and_do_not_print_from_google(self) -> None:
        contact_id = _insert_contact_from_google_person(self.conn, self._fields())
        row = self.conn.execute(
            "SELECT meets_in_state, do_not_print FROM contacts WHERE id = ?",
            (contact_id,),
        ).fetchone()
        self.assertEqual(row["meets_in_state"], "LA")
        self.assertEqual(int(row["do_not_print"]), 1)
        custom = self.conn.execute(
            "SELECT field_type FROM custom_fields WHERE contact_id = ?",
            (contact_id,),
        ).fetchall()
        self.assertEqual(custom, [])

    def test_update_keeps_local_values_when_google_omits_them(self) -> None:
        self.conn.execute(
            """
            INSERT INTO contacts (id, family_name, given_name, meets_in_state, do_not_print)
            VALUES (1, 'Wiley', 'Linda', 'LA', 1)
            """
        )
        fields = self._fields(meets="", do_not_print=0)
        fields["meets_in_state"] = ""
        fields["do_not_print"] = 0
        _update_contact_from_google_person(self.conn, 1, fields)
        row = self.conn.execute(
            "SELECT meets_in_state, do_not_print FROM contacts WHERE id = 1"
        ).fetchone()
        self.assertEqual(row["meets_in_state"], "LA")
        self.assertEqual(int(row["do_not_print"]), 1)

    def test_update_fills_empty_local_from_google(self) -> None:
        self.conn.execute(
            """
            INSERT INTO contacts (id, family_name, given_name, meets_in_state, do_not_print)
            VALUES (1, 'Wiley', 'Linda', '', 0)
            """
        )
        _update_contact_from_google_person(self.conn, 1, self._fields())
        row = self.conn.execute(
            "SELECT meets_in_state, do_not_print FROM contacts WHERE id = 1"
        ).fetchone()
        self.assertEqual(row["meets_in_state"], "LA")
        self.assertEqual(int(row["do_not_print"]), 1)


if __name__ == "__main__":
    unittest.main()
