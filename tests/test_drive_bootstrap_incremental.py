import sqlite3
import unittest

from app.services.google_sync_service import (
    _count_drive_bootstrap_remaining,
    _count_unbootstrapped_contacts,
    _prepare_drive_bootstrap_run,
)


class DriveBootstrapIncrementalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE contacts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              family_name TEXT,
              given_name TEXT,
              shared_drive_needs_export INTEGER NOT NULL DEFAULT 0,
              shared_drive_bootstrapped INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE google_sync_state (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              bootstrap_status TEXT NOT NULL DEFAULT 'idle',
              bootstrap_total_contacts INTEGER NOT NULL DEFAULT 0,
              bootstrap_processed_contacts INTEGER NOT NULL DEFAULT 0,
              bootstrap_last_contact_id INTEGER NOT NULL DEFAULT 0,
              bootstrap_started_at TEXT NOT NULL DEFAULT '',
              bootstrap_updated_at TEXT NOT NULL DEFAULT '',
              bootstrap_error TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO google_sync_state (id) VALUES (1);
            """
        )

    def tearDown(self) -> None:
        self.conn.close()

    def _insert_contacts(self, bootstrapped: int, needs_export: int = 0, count: int = 1, start_id: int = 1) -> None:
        for offset in range(count):
            self.conn.execute(
                """
                INSERT INTO contacts (id, family_name, given_name, shared_drive_bootstrapped, shared_drive_needs_export)
                VALUES (?, ?, ?, ?, ?)
                """,
                (start_id + offset, f"Family{start_id + offset}", "Given", bootstrapped, needs_export),
            )
        self.conn.commit()

    def test_incremental_bootstrap_does_not_reset_existing_contacts(self) -> None:
        self._insert_contacts(bootstrapped=1, count=700, start_id=1)
        self._insert_contacts(bootstrapped=0, count=5, start_id=701)

        state = dict(self.conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone())
        work_total, work_processed = _prepare_drive_bootstrap_run(
            self.conn,
            manifest_is_v2=True,
            total_contacts=705,
            state=state,
        )
        self.conn.commit()

        self.assertEqual(work_total, 5)
        self.assertEqual(work_processed, 0)
        self.assertEqual(_count_unbootstrapped_contacts(self.conn), 5)
        bootstrapped_count = self.conn.execute(
            "SELECT COUNT(*) AS count FROM contacts WHERE shared_drive_bootstrapped = 1"
        ).fetchone()["count"]
        self.assertEqual(int(bootstrapped_count), 700)

    def test_first_time_bootstrap_still_resets_all_contacts(self) -> None:
        self._insert_contacts(bootstrapped=1, count=3)

        state = dict(self.conn.execute("SELECT * FROM google_sync_state WHERE id = 1").fetchone())
        work_total, work_processed = _prepare_drive_bootstrap_run(
            self.conn,
            manifest_is_v2=False,
            total_contacts=3,
            state=state,
        )
        self.conn.commit()

        self.assertEqual(work_total, 3)
        self.assertEqual(work_processed, 0)
        self.assertEqual(_count_unbootstrapped_contacts(self.conn), 3)

    def test_incremental_bootstrap_counts_only_unbootstrapped_contacts(self) -> None:
        self._insert_contacts(bootstrapped=1, needs_export=1, count=2, start_id=1)
        self._insert_contacts(bootstrapped=0, count=1, start_id=3)

        remaining = _count_drive_bootstrap_remaining(self.conn, manifest_is_v2=True)
        self.assertEqual(remaining, 1)


if __name__ == "__main__":
    unittest.main()
