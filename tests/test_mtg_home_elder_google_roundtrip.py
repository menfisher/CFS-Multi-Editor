import sqlite3
import unittest

from app.services.google_sync_service import (
    CFS_MTG_HOME_ELDER_FIELD,
    _contact_person_payload,
    _google_person_import_fields,
    _insert_contact_from_google_person,
    _mtg_home_elder_flag_from_user_defined,
    _update_contact_from_google_person,
)


class MtgHomeElderGoogleRoundtripTests(unittest.TestCase):
    def test_reads_value_not_the_label_that_starts_with_1(self) -> None:
        self.assertEqual(
            _mtg_home_elder_flag_from_user_defined(
                [{"key": CFS_MTG_HOME_ELDER_FIELD, "value": "2"}]
            ),
            "2",
        )
        self.assertEqual(
            _mtg_home_elder_flag_from_user_defined(
                [{"key": "Meeting/Home/Elder", "value": "1"}]
            ),
            "1",
        )
        self.assertEqual(_mtg_home_elder_flag_from_user_defined([]), "")

    def test_upload_sends_typed_1_or_2_as_the_custom_field_value(self) -> None:
        payload = _contact_person_payload(
            {
                "family_name": "Bearden",
                "given_name": "Galen",
                "mtg_home_elder_flag": "2",
            }
        )
        self.assertIn(
            {"key": CFS_MTG_HOME_ELDER_FIELD, "value": "2"},
            payload.get("userDefined") or [],
        )

    def test_upload_does_not_duplicate_the_dedicated_custom_field(self) -> None:
        payload = _contact_person_payload(
            {
                "family_name": "Wiley",
                "given_name": "Linda",
                "mtg_home_elder_flag": "1",
                "custom_fields": [
                    {"field_type": CFS_MTG_HOME_ELDER_FIELD, "field_value": "2"},
                ],
            }
        )
        mtg_fields = [
            item
            for item in payload.get("userDefined") or []
            if item.get("key") == CFS_MTG_HOME_ELDER_FIELD
        ]
        self.assertEqual(mtg_fields, [{"key": CFS_MTG_HOME_ELDER_FIELD, "value": "1"}])

    def test_import_fields_keep_elder_2_when_label_starts_with_1(self) -> None:
        fields = _google_person_import_fields(
            {
                "resourceName": "people/c-elder",
                "etag": "etag-1",
                "names": [{"givenName": "Galen", "familyName": "Bearden"}],
                "userDefined": [{"key": CFS_MTG_HOME_ELDER_FIELD, "value": "2"}],
            },
            {},
        )
        self.assertIsNotNone(fields)
        self.assertEqual(fields["mtg_home_elder_flag"], "2")


class MtgHomeElderGooglePersistTests(unittest.TestCase):
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

    def _fields(self, *, flag: str = "2", resource_name: str = "people/c-elder") -> dict:
        return {
            "resource_name": resource_name,
            "etag": "etag-1",
            "fields_text": "LA North",
            "meetings_text": "DeRidder (WILEY)",
            "group_names": [],
            "family_name": "Bearden",
            "given_name": "Galen",
            "photo": "",
            "birthday": "",
            "notes": "",
            "relations": [],
            "phones": [],
            "emails": [],
            "addresses": [],
            "user_defined": [{"key": CFS_MTG_HOME_ELDER_FIELD, "value": flag}] if flag else [],
            "user_defined_coordinates": {},
            "url_coordinate_candidates": [],
            "mtg_home_elder_flag": flag,
        }

    def test_insert_stores_elder_2_from_google_custom_field_value(self) -> None:
        contact_id = _insert_contact_from_google_person(self.conn, self._fields(flag="2"))
        row = self.conn.execute(
            "SELECT mtg_home_elder_flag FROM contacts WHERE id = ?",
            (contact_id,),
        ).fetchone()
        self.assertEqual(row["mtg_home_elder_flag"], "2")
        custom = self.conn.execute(
            "SELECT field_type, field_value FROM custom_fields WHERE contact_id = ?",
            (contact_id,),
        ).fetchall()
        self.assertEqual(custom, [])

    def test_update_keeps_local_elder_2_when_google_omits_the_field(self) -> None:
        self.conn.execute(
            """
            INSERT INTO contacts (id, family_name, given_name, mtg_home_elder_flag)
            VALUES (1, 'Bearden', 'Galen', '2')
            """
        )
        fields = self._fields(flag="")
        fields["mtg_home_elder_flag"] = ""
        _update_contact_from_google_person(self.conn, 1, fields)
        row = self.conn.execute(
            "SELECT mtg_home_elder_flag FROM contacts WHERE id = 1"
        ).fetchone()
        self.assertEqual(row["mtg_home_elder_flag"], "2")

    def test_update_fills_empty_local_from_google_elder_2(self) -> None:
        self.conn.execute(
            """
            INSERT INTO contacts (id, family_name, given_name, mtg_home_elder_flag)
            VALUES (1, 'Bearden', 'Galen', '')
            """
        )
        _update_contact_from_google_person(self.conn, 1, self._fields(flag="2"))
        row = self.conn.execute(
            "SELECT mtg_home_elder_flag FROM contacts WHERE id = 1"
        ).fetchone()
        self.assertEqual(row["mtg_home_elder_flag"], "2")


if __name__ == "__main__":
    unittest.main()
