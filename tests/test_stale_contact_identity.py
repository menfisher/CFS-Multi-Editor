import sqlite3
import unittest

from app.services.google_sync_service import (
    _find_local_contact_id_by_identity,
    _prune_local_contacts_missing_google_ids,
    collapse_stale_identity_duplicate_contacts,
)


class StaleContactIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE contacts (
              id INTEGER PRIMARY KEY,
              given_name TEXT NOT NULL DEFAULT '',
              family_name TEXT NOT NULL DEFAULT '',
              google_contact_id TEXT NOT NULL DEFAULT '',
              last_updated TEXT NOT NULL DEFAULT '',
              shared_drive_revision INTEGER NOT NULL DEFAULT 0
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
            CREATE TABLE relationships (id INTEGER PRIMARY KEY, contact_id INTEGER);
            CREATE TABLE addresses (id INTEGER PRIMARY KEY, contact_id INTEGER);
            CREATE TABLE custom_fields (id INTEGER PRIMARY KEY, contact_id INTEGER);
            CREATE TABLE contact_edit_log_entries (id INTEGER PRIMARY KEY, contact_id INTEGER);
            """
        )

    def tearDown(self) -> None:
        self.conn.close()

    def _add_contact(
        self,
        contact_id: int,
        given_name: str,
        family_name: str,
        *,
        google_contact_id: str = "",
        last_updated: str = "",
        revision: int = 0,
        phone: str = "",
        email: str = "",
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO contacts (
              id, given_name, family_name, google_contact_id, last_updated, shared_drive_revision
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (contact_id, given_name, family_name, google_contact_id, last_updated, revision),
        )
        if phone:
            self.conn.execute(
                "INSERT INTO phones (contact_id, position, phone_type, phone_value) VALUES (?, 1, 'Mobile', ?)",
                (contact_id, phone),
            )
        if email:
            self.conn.execute(
                "INSERT INTO emails (contact_id, position, email_type, email_value) VALUES (?, 1, 'Other', ?)",
                (contact_id, email),
            )

    def test_collapse_keeps_newer_same_name_and_phone(self) -> None:
        self._add_contact(
            10,
            "John",
            "Young",
            google_contact_id="people/old",
            last_updated="2026-06-18 02:11:02",
            phone="228-342-6048",
        )
        self._add_contact(
            20,
            "John",
            "Young",
            google_contact_id="people/new",
            last_updated="2026-08-23 19:53:06",
            revision=1,
            phone="(228) 342-6048",
        )
        self.conn.commit()

        deleted = collapse_stale_identity_duplicate_contacts(self.conn)
        ids = [int(row["id"]) for row in self.conn.execute("SELECT id FROM contacts ORDER BY id")]
        self.assertEqual(deleted, [10])
        self.assertEqual(ids, [20])

    def test_collapse_leaves_same_name_with_different_phone(self) -> None:
        self._add_contact(1, "John", "Young", phone="228-342-6048", last_updated="2026-06-01")
        self._add_contact(2, "John", "Young", phone="228-555-1212", last_updated="2026-08-01")
        self.conn.commit()

        deleted = collapse_stale_identity_duplicate_contacts(self.conn)
        ids = [int(row["id"]) for row in self.conn.execute("SELECT id FROM contacts ORDER BY id")]
        self.assertEqual(deleted, [])
        self.assertEqual(ids, [1, 2])

    def test_find_identity_matches_phone(self) -> None:
        self._add_contact(5, "Cindy", "Cuevas", phone="228-342-4438")
        self.conn.commit()
        found = _find_local_contact_id_by_identity(
            self.conn,
            "Cindy",
            "Cuevas",
            [{"value": "2283424438"}],
            [],
        )
        self.assertEqual(found, 5)

    def test_prune_removes_dead_google_ids(self) -> None:
        self._add_contact(1, "Dorothy", "Turner", google_contact_id="people/dead")
        self._add_contact(2, "Live", "Person", google_contact_id="people/live")
        self.conn.commit()

        deleted = _prune_local_contacts_missing_google_ids(self.conn, {"people/live"})
        ids = [int(row["id"]) for row in self.conn.execute("SELECT id FROM contacts ORDER BY id")]
        self.assertEqual(deleted, [1])
        self.assertEqual(ids, [2])

    def test_prune_does_not_wipe_when_google_list_is_empty(self) -> None:
        self._add_contact(1, "Dorothy", "Turner", google_contact_id="people/dead")
        self.conn.commit()

        deleted = _prune_local_contacts_missing_google_ids(self.conn, set())
        count = self.conn.execute("SELECT COUNT(*) AS count FROM contacts").fetchone()["count"]
        self.assertEqual(deleted, [])
        self.assertEqual(int(count), 1)


if __name__ == "__main__":
    unittest.main()
