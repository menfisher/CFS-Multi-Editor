import unittest

from app.services.changes_list_service import (
    _entry_has_content,
    changes_list_entries_equal,
    merge_changes_list_entry_lists,
    update_changes_entry,
)


class ChangesListDriveMergeTests(unittest.TestCase):
    def test_entry_has_content_ignores_date_only_rows(self) -> None:
        self.assertFalse(
            _entry_has_content(
                {
                    "automatic_date": "Ck, 07/14/2026, 10:56",
                    "name_event": "",
                    "changes_text": "",
                    "completed_by": "",
                }
            )
        )
        self.assertTrue(
            _entry_has_content(
                {
                    "automatic_date": "Ck, 07/14/2026, 10:57",
                    "name_event": "Cuevas, Cindy",
                    "changes_text": "Photo Location: old → new",
                    "completed_by": "",
                }
            )
        )

    def test_merge_changes_list_entry_lists_keeps_all_editors(self) -> None:
        remote = [
            {
                "sort_order": 2,
                "automatic_date": "Ck, 07/14/2026, 10:57",
                "name_event": "Cuevas, Cindy",
                "changes_text": "Photo Location: old → new",
                "completed_by": "",
                "completed_at": "",
            }
        ]
        local = [
            {
                "sort_order": 2,
                "automatic_date": "AB, 07/14/2026, 09:10",
                "name_event": "Smith, John",
                "changes_text": "Phone: 555-0100 → 555-0101",
                "completed_by": "",
                "completed_at": "",
            }
        ]

        merged = merge_changes_list_entry_lists(remote, local)

        self.assertEqual(len(merged), 2)
        self.assertEqual(
            {entry["name_event"] for entry in merged},
            {"Cuevas, Cindy", "Smith, John"},
        )
        # Newest automatic_date first so mobile uploads appear at the top of the list.
        self.assertEqual(merged[0]["name_event"], "Cuevas, Cindy")
        self.assertEqual(merged[0]["sort_order"], 2)
        self.assertEqual(merged[1]["name_event"], "Smith, John")
        self.assertEqual(merged[1]["sort_order"], 3)

    def test_merge_prefers_completed_row_over_incomplete_copy(self) -> None:
        incomplete = {
            "sort_order": 2,
            "automatic_date": "Cv, 07/16/2026, 10:35",
            "name_event": "Test, Joe",
            "changes_text": "Photo Location: a → b",
            "completed_by": "",
            "completed_at": "",
        }
        completed = {
            "sort_order": 3,
            "automatic_date": "Cv, 07/16/2026, 10:35",
            "name_event": "Test, Joe",
            "changes_text": "Cv - Photo Location: a → b",
            "completed_by": "Cv",
            "completed_at": "2026-07-16T10:46:03",
        }

        merged = merge_changes_list_entry_lists([incomplete], [completed])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["completed_by"], "Cv")
        self.assertTrue(merged[0]["changes_text"].startswith("Cv - "))

    def test_merge_treats_crlf_and_lf_as_same_edit(self) -> None:
        # Reproduces Graham, Mark: numbered body "1 - Meeting..." was mistaken for a
        # completion prefix, and Drive kept \\r while Completed Edit(s) used \\n,
        # so Download left a white incomplete duplicate beside the green row.
        incomplete = {
            "sort_order": 2,
            "automatic_date": "Cv, 07/16/2026, 11:42",
            "name_event": "Graham, Mark",
            "changes_text": (
                "1 - Meeting home, 2 - Elder: removed 1\n"
                "Notes: nw - 5as9h42sgn68 → nw - 5as9h42sgn68\r\\nTest"
            ),
            "completed_by": "",
            "completed_at": "",
        }
        completed = {
            "sort_order": 3,
            "automatic_date": "Cv, 07/16/2026, 11:42",
            "name_event": "Graham, Mark",
            "changes_text": (
                "Cv - 1 - Meeting home, 2 - Elder: removed 1\n"
                "Notes: nw - 5as9h42sgn68 → nw - 5as9h42sgn68\n\\nTest"
            ),
            "completed_by": "Cv",
            "completed_at": "2026-07-21T09:43:36",
        }

        merged = merge_changes_list_entry_lists([incomplete], [completed])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["completed_by"], "Cv")
        self.assertTrue(merged[0]["changes_text"].startswith("Cv - "))

    def test_merge_changes_list_entry_lists_puts_newest_rows_first(self) -> None:
        older = {
            "sort_order": 2,
            "automatic_date": "Cv, 07/16/2026, 10:12",
            "name_event": "Test, Joe",
            "changes_text": "Photo Location: a → b",
            "completed_by": "",
            "completed_at": "",
        }
        newer = {
            "sort_order": 99,
            "automatic_date": "Cv, 07/16/2026, 10:35",
            "name_event": "Test, Joe",
            "changes_text": "Photo Location: b → c",
            "completed_by": "",
            "completed_at": "",
        }

        merged = merge_changes_list_entry_lists([older], [newer])

        self.assertEqual([entry["changes_text"] for entry in merged], [
            "Photo Location: b → c",
            "Photo Location: a → b",
        ])
        self.assertEqual(merged[0]["sort_order"], 2)
        self.assertEqual(merged[1]["sort_order"], 3)

    def test_merge_changes_list_entry_lists_deduplicates_identical_rows(self) -> None:
        entry = {
            "sort_order": 2,
            "automatic_date": "Ck, 07/14/2026, 10:57",
            "name_event": "Cuevas, Cindy",
            "changes_text": "Photo Location: old → new",
            "completed_by": "",
            "completed_at": "",
        }

        merged = merge_changes_list_entry_lists([entry], [dict(entry)])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["name_event"], "Cuevas, Cindy")

    def test_changes_list_entries_equal_normalizes_before_compare(self) -> None:
        left = [
            {
                "sort_order": 99,
                "automatic_date": "Ck, 07/14/2026, 10:57",
                "name_event": "Cuevas, Cindy",
                "changes_text": "Photo Location: old → new",
                "completed_by": "",
                "completed_at": "",
            }
        ]
        right = [
            {
                "sort_order": 2,
                "automatic_date": "Ck, 07/14/2026, 10:57",
                "name_event": "Cuevas, Cindy",
                "changes_text": "Photo Location: old → new",
                "completed_by": "",
                "completed_at": "",
            }
        ]

        self.assertTrue(changes_list_entries_equal(left, right))

    def test_update_changes_entry_clears_date_when_content_removed(self) -> None:
        import sqlite3
        from contextlib import contextmanager
        from unittest.mock import patch

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE changes_list_entries (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              sort_order INTEGER NOT NULL,
              automatic_date TEXT NOT NULL DEFAULT '',
              name_event TEXT NOT NULL DEFAULT '',
              changes_text TEXT NOT NULL DEFAULT '',
              completed_by TEXT NOT NULL DEFAULT '',
              completed_at TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE google_sync_state (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              changes_list_needs_drive_export INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO google_sync_state (id) VALUES (1);
            INSERT INTO changes_list_entries (
              sort_order, automatic_date, name_event, changes_text
            ) VALUES (2, 'Ck, 07/14/2026, 10:56', 'Cuevas, Cindy', 'Photo Location: old → new');
            """
        )
        conn.commit()

        @contextmanager
        def fake_get_connection():
            try:
                yield conn
            finally:
                pass

        with patch("app.services.changes_list_service.get_connection", fake_get_connection):
            from app.services.changes_list_service import ensure_changes_list_entries, list_changes_entries

            ensure_changes_list_entries()
            content_rows = [
                entry for entry in list_changes_entries() if str(entry.get("name_event") or "").strip()
            ]
            self.assertEqual(len(content_rows), 1)
            entry_id = int(content_rows[0]["id"])
            update_changes_entry(entry_id, name_event="", changes_text="", initials="Ck")
            remaining = list_changes_entries()

        dated_rows = [
            entry
            for entry in remaining
            if str(entry.get("automatic_date") or "").strip()
            or str(entry.get("name_event") or "").strip()
            or str(entry.get("changes_text") or "").strip()
        ]
        self.assertEqual(dated_rows, [])


if __name__ == "__main__":
    unittest.main()
