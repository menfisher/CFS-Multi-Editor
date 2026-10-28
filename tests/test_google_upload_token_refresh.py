import json
import tempfile
import unittest
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from app.database import get_connection, initialize_database, set_request_db_path
from app.services import google_sync_service as gss


def _http_error(code: int, message: str, status: str = "UNAUTHENTICATED") -> HTTPError:
    payload = json.dumps({"error": {"message": message, "status": status}}).encode("utf-8")
    return HTTPError("https://people.googleapis.com/v1", code, "Unauthorized", hdrs=None, fp=BytesIO(payload))


class GoogleUploadTokenRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        set_request_db_path(Path(self._tempdir.name) / "app.sqlite3")
        initialize_database()
        gss.ensure_google_sync_records()
        expiry = (gss._utc_now() + timedelta(hours=1)).isoformat()
        with get_connection() as conn:
            conn.execute("UPDATE google_sync_state SET multi_editor_enabled = 1 WHERE id = 1")
            conn.execute(
                """
                UPDATE google_sync_accounts
                SET
                  account_email = 'owner@example.com',
                  account_status = 'connected',
                  access_token = 'old-token',
                  refresh_token = 'refresh-token',
                  token_expiry = ?
                WHERE id = 1
                """,
                (expiry,),
            )
            conn.commit()

    def tearDown(self) -> None:
        set_request_db_path(None)
        self._tempdir.cleanup()

    def test_detects_unauthenticated_as_expired_access_token(self) -> None:
        exc = _http_error(401, "Request had invalid authentication credentials.")
        message = "Bond, Kelly: Request had invalid authentication credentials. (UNAUTHENTICATED)"
        self.assertTrue(gss._looks_like_expired_access_token_error(exc, message))
        self.assertTrue(gss._looks_like_reconnect_error(message))

    def test_force_refresh_replaces_unexpired_access_token(self) -> None:
        with patch.object(
            gss,
            "_refresh_access_token",
            return_value={"access_token": "new-token", "expires_in": 3600},
        ) as refresh, patch.object(
            gss,
            "_fetch_userinfo",
            return_value={"email": "owner@example.com", "sub": ""},
        ):
            token, email = gss._get_valid_access_token()
            self.assertEqual(token, "old-token")
            token, email = gss._get_valid_access_token(force_refresh=True)
            self.assertEqual(token, "new-token")
            self.assertEqual(email, "owner@example.com")
            refresh.assert_called_once()

    def test_upload_refreshes_and_continues_after_401(self) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO contacts (family_name, given_name, google_contact_id, fields_text, meetings_text)
                VALUES ('Bond', 'Kelly', 'people/1', 'Field', 'Meeting')
                """
            )
            contact_id = int(conn.execute("SELECT id FROM contacts").fetchone()["id"])
            conn.execute(
                """
                INSERT INTO google_sync_queue (contact_id, google_contact_id, operation, payload_json, status)
                VALUES (?, 'people/1', 'update', '{}', 'pending')
                """,
                (contact_id,),
            )
            conn.commit()

        calls = {"upsert": 0}

        def fake_upsert(*_args, **_kwargs):
            calls["upsert"] += 1
            if calls["upsert"] == 1:
                raise _http_error(401, "Request had invalid authentication credentials.")

        with (
            patch.object(gss, "_contact_groups_lookup", return_value=({}, {})),
            patch.object(gss, "_process_upsert_queue_entry", side_effect=fake_upsert),
            patch.object(
                gss,
                "_refresh_access_token",
                return_value={"access_token": "new-token", "expires_in": 3600},
            ),
            patch.object(gss, "_fetch_userinfo", return_value={"email": "owner@example.com", "sub": ""}),
        ):
            processed = gss.upload_google_contacts(skip_share_sync=True)

        self.assertEqual(processed, 1)
        self.assertEqual(calls["upsert"], 2)
        with get_connection() as conn:
            pending = conn.execute(
                "SELECT COUNT(*) AS count FROM google_sync_queue WHERE status = 'pending'"
            ).fetchone()["count"]
            error = conn.execute("SELECT last_sync_error FROM google_sync_state WHERE id = 1").fetchone()[
                "last_sync_error"
            ]
        self.assertEqual(pending, 0)
        self.assertEqual(error, "")

    def test_upload_flushes_google_labels_before_aborting(self) -> None:
        with get_connection() as conn:
            for index, name in enumerate(("One", "Two"), start=1):
                conn.execute(
                    """
                    INSERT INTO contacts (family_name, given_name, google_contact_id, fields_text, meetings_text)
                    VALUES (?, 'Pat', ?, 'Field', 'Meeting')
                    """,
                    (name, f"people/{index}"),
                )
                contact_id = int(conn.execute("SELECT id FROM contacts ORDER BY id DESC LIMIT 1").fetchone()["id"])
                conn.execute(
                    """
                    INSERT INTO google_sync_queue (contact_id, google_contact_id, operation, payload_json, status)
                    VALUES (?, ?, 'update', '{}', 'pending')
                    """,
                    (contact_id, f"people/{index}"),
                )
            conn.commit()

        calls = {"upsert": 0}

        def fake_upsert(_conn, _token, _entry, _r2n, _n2r, group_sync_batch=None):
            calls["upsert"] += 1
            if group_sync_batch is not None:
                group_sync_batch.setdefault("contactGroups/shared", {"add": set(), "remove": set()})
                group_sync_batch["contactGroups/shared"]["add"].add(f"people/{calls['upsert']}")
            if calls["upsert"] == 2:
                raise HTTPError("https://people.googleapis.com/v1", 500, "Server Error", hdrs=None, fp=BytesIO(b"{}"))

        flushed_members: set[str] = set()

        def capture_flush(_token, batch, *_args, **_kwargs):
            flushed_members.update(batch.get("contactGroups/shared", {}).get("add", set()))

        with (
            patch.object(gss, "_contact_groups_lookup", return_value=({}, {})),
            patch.object(gss, "_process_upsert_queue_entry", side_effect=fake_upsert),
            patch.object(gss, "_flush_google_contact_group_batch", side_effect=capture_flush) as flush,
        ):
            with self.assertRaises(RuntimeError):
                gss.upload_google_contacts(skip_share_sync=True)
            flush.assert_called()
            self.assertIn("people/1", flushed_members)


if __name__ == "__main__":
    unittest.main()
