import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database import initialize_database, set_request_db_path
from app.services import roster_fix_service as fix
from app.services.roster_check_service import build_roster_report, condense_card_check


def _card_reply(**counts):
    base = {"missing": 0, "adopt": 0, "linked_elsewhere": 0, "wrong_card": 0, "not_in_group": 0, "duplicates": 0, "foreign": 0}
    base.update(counts)
    changes = sum(base.values())
    return {
        "ok": True,
        "clean": changes == 0,
        "changes": changes,
        "groups": [{"group": "AlMsLa", "counts": base, "duplicates": ["Doe, Jane"] if base["duplicates"] else []}],
    }


class RosterReportTests(unittest.TestCase):
    def _report(self, cards=None, google=None, drive=None, maps=None):
        return build_roster_report(
            cfs_people=[
                {"id": 1, "google_contact_id": "people/c1", "name": "Burns, Chad"},
                {"id": 2, "google_contact_id": "people/c2", "name": "Miller, Betty"},
            ],
            google_people=google if google is not None else [
                {"id": "people/c1", "name": "Burns, Chad"},
                {"id": "people/c2", "name": "Miller, Betty"},
            ],
            drive_ids=drive if drive is not None else {1, 2},
            drive_labels={},
            recipient_maps=maps if maps is not None else {"a@gmail.com": {"people/c1", "people/c2"}},
            share_cards=cards,
        )

    def test_clean_report_has_no_issues(self) -> None:
        report = self._report()
        self.assertFalse(report["mismatch"])
        self.assertEqual(report["issues"], [])

    def test_ids_and_extras_are_reported(self) -> None:
        report = self._report(
            google=[{"id": "people/c1", "name": "Burns, Chad"}, {"id": "people/cX", "name": "Stray, Sam"}],
            drive={1, 9},
            maps={"a@gmail.com": {"people/c1", "people/old"}},
        )
        self.assertEqual(report["google_missing_ids"], [2])
        self.assertEqual(report["google_extra_people"], [{"id": "people/cX", "name": "Stray, Sam"}])
        self.assertEqual(report["drive_missing_ids"], [2])
        self.assertEqual(report["drive_extra_ids"], [9])
        recipient = report["recipients"][0]
        self.assertEqual(recipient["missing_person_ids"], ["people/c2"])
        self.assertEqual(recipient["extra_person_ids"], ["people/old"])
        kinds = {item["kind"] for item in report["issues"]}
        self.assertTrue({"drive_extra", "drive_missing", "google_missing", "google_extra", "recipient"} <= kinds)
        google_extra = [item for item in report["issues"] if item["kind"] == "google_extra"][0]
        self.assertFalse(google_extra["fixable"])
        self.assertTrue(report["fixable"])

    def test_deep_card_problem_makes_mismatch(self) -> None:
        cards = {"recipients": {"a@gmail.com": condense_card_check(_card_reply(duplicates=2))}}
        report = self._report(cards=cards)
        self.assertTrue(report["mismatch"])
        text = " ".join(item["text"] for item in report["issues"])
        self.assertIn("duplicate", text)

    def test_condense_card_check_counts(self) -> None:
        condensed = condense_card_check(_card_reply(adopt=1, foreign=2))
        self.assertEqual(condensed["counts"]["adopt"], 1)
        self.assertEqual(condensed["counts"]["foreign"], 2)
        self.assertEqual(condensed["changes"], 3)
        self.assertFalse(condensed["clean"])


class RosterFixRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        set_request_db_path(Path(self._tempdir.name) / "app.sqlite3")
        initialize_database()
        fix._set_state(running=False, step="", log=[], ok=None, message="", started_at=0.0, finished_at=0.0)

    def tearDown(self) -> None:
        set_request_db_path(None)
        self._tempdir.cleanup()

    def test_edit_list_entry_is_written_as_completed(self) -> None:
        from app.database import fetch_all

        with patch.object(fix, "get_current_editor_initials", return_value="Cv"):
            fix._log_to_edit_list("Share roster fix", ["Google Drive: removed 2 contact(s)", "Result: everything matches"])
        rows = [
            dict(row)
            for row in fetch_all(
                "SELECT name_event, changes_text, completed_by, completed_at FROM changes_list_entries WHERE name_event = ?",
                ("Share roster fix",),
            )
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["completed_by"], "Cv")
        self.assertTrue(rows[0]["completed_at"])
        self.assertTrue(rows[0]["changes_text"].startswith("Cv - "))
        self.assertIn("Result: everything matches", rows[0]["changes_text"])

    def test_start_refused_without_editor_lock(self) -> None:
        with patch.object(fix, "is_editing_allowed", return_value=False), patch.object(fix.threading, "Thread") as thread:
            result = fix.start_roster_fix()
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "not_editor")
        thread.assert_not_called()

    def test_start_refused_without_share_key(self) -> None:
        summary = {
            "account": {"status": "connected", "drive_scope_ready": True},
            "state": {},
        }
        with patch.object(fix, "is_editing_allowed", return_value=True), patch.object(
            fix, "get_google_sync_summary", return_value=summary
        ), patch.object(fix, "get_share_web_api_key", return_value=""):
            result = fix.preflight()
        self.assertEqual(result["reason"], "share_key_missing")

    def test_start_runs_once(self) -> None:
        summary = {"account": {"status": "connected", "drive_scope_ready": True}, "state": {}}
        with patch.object(fix, "is_editing_allowed", return_value=True), patch.object(
            fix, "get_google_sync_summary", return_value=summary
        ), patch.object(fix, "get_share_web_api_key", return_value="k"), patch.object(fix.threading, "Thread") as thread:
            first = fix.start_roster_fix()
            second = fix.start_roster_fix()
        self.assertTrue(first["ok"])
        self.assertEqual(second["reason"], "already_running")
        thread.return_value.start.assert_called_once()

    def test_format_tally_summarizes_counts(self) -> None:
        self.assertEqual(
            fix._format_tally({"created": 5, "deleted": 2, "relinked": 1, "linked_elsewhere": 1}),
            "relinked 2, created 5, deleted 2 duplicate(s)",
        )

    def test_status_includes_elapsed_seconds(self) -> None:
        fix._set_state(running=True, started_at=time.time() - 12, finished_at=0.0, step="Working", log=["Checking"])
        status = fix.get_roster_fix_status()
        self.assertGreaterEqual(status["elapsed_seconds"], 11)
        self.assertEqual(status["step"], "Working")
        self.assertEqual(status["log"], ["Checking"])

    def test_fix_recipient_reports_live_progress(self) -> None:
        replies = [
            {"ok": False, "reason": "push_queue_busy"},
            {"ok": True, "partial": True, "groups": [{"applied": {"created": 3}}]},
            {"ok": True, "clean": True, "groups": [{"applied": {"created": 2, "deleted": 1}}]},
        ]
        steps: list[str] = []
        real_set = fix._set_state

        def spy(**changes):
            if "step" in changes:
                steps.append(changes["step"])
            real_set(**changes)

        with patch.object(fix, "fix_share_recipient_cards", side_effect=replies), patch.object(
            fix, "_sleep"
        ), patch.object(fix, "_set_state", new=spy):
            totals, problem = fix._fix_recipient("owner@gmail.com", "a@gmail.com", index=1, total=2)
        self.assertEqual(problem, "")
        self.assertEqual(totals, {"created": 5, "deleted": 1})
        joined = "\n".join(steps)
        self.assertIn("1 of 2", joined)
        self.assertIn("waiting for Share to finish sending", joined)
        self.assertIn("created 3", joined)
        self.assertIn("created 5", joined)
        self.assertTrue(any("round 1" in step for step in steps))

    def test_fix_recipient_repeats_until_clean(self) -> None:
        replies = [
            {"ok": True, "partial": True, "groups": [{"applied": {"created": 3, "partial": True, "errors": []}}]},
            {"ok": True, "clean": True, "groups": [{"applied": {"created": 2, "deleted": 1, "partial": False, "errors": []}}]},
        ]
        with patch.object(fix, "fix_share_recipient_cards", side_effect=replies) as call, patch.object(fix, "_sleep"):
            totals, problem = fix._fix_recipient("owner@gmail.com", "a@gmail.com")
        self.assertEqual(call.call_count, 2)
        self.assertEqual(problem, "")
        self.assertEqual(totals, {"created": 5, "deleted": 1})

    def test_fix_recipient_waits_when_share_queue_busy(self) -> None:
        replies = [{"ok": False, "reason": "push_queue_busy"}, {"ok": True, "clean": True, "groups": []}]
        with patch.object(fix, "fix_share_recipient_cards", side_effect=replies) as call, patch.object(fix, "_sleep") as sleeper:
            _totals, problem = fix._fix_recipient("owner@gmail.com", "a@gmail.com")
        self.assertEqual(problem, "")
        self.assertEqual(call.call_count, 2)
        sleeper.assert_called_once()

    def test_fix_recipient_reports_refusal(self) -> None:
        reply = {
            "ok": False,
            "groups": [{"ok": False, "reason": "too_many_changes", "message": "Too many changes at once"}],
        }
        with patch.object(fix, "fix_share_recipient_cards", return_value=reply) as call, patch.object(fix, "_sleep"):
            _totals, problem = fix._fix_recipient("owner@gmail.com", "a@gmail.com")
        self.assertEqual(problem, "Too many changes at once")
        self.assertEqual(call.call_count, 1)

    def test_fix_recipient_gives_up_when_not_connected(self) -> None:
        reply = {"ok": False, "reason": "recipient_not_connected", "message": "Not connected"}
        with patch.object(fix, "fix_share_recipient_cards", return_value=reply) as call:
            _totals, problem = fix._fix_recipient("owner@gmail.com", "a@gmail.com")
        self.assertEqual(problem, "Not connected")
        self.assertEqual(call.call_count, 1)

    def test_targets_only_recipients_with_problems(self) -> None:
        clean = condense_card_check(_card_reply())
        dirty = condense_card_check(_card_reply(duplicates=1))
        report = {
            "recipients": [
                {"email": "clean@gmail.com", "missing": [], "extra_count": 0},
                {"email": "dupes@gmail.com", "missing": [], "extra_count": 0},
                {"email": "mapping@gmail.com", "missing": ["Doe, Jane"], "extra_count": 0},
                {"email": "unchecked@gmail.com", "missing": [], "extra_count": 0},
            ],
            "share_cards": {
                "recipients": {
                    "clean@gmail.com": clean,
                    "dupes@gmail.com": dirty,
                    "mapping@gmail.com": clean,
                }
            },
        }
        self.assertEqual(fix._recipient_targets(report), ["dupes@gmail.com"])

    def test_drive_fix_marks_missing_and_exports(self) -> None:
        report = {"drive_missing_ids": [4, 5], "drive_extra_ids": [9]}
        with patch.object(fix, "mark_contact_for_drive_export") as mark, patch.object(
            fix, "mark_contacts_manifest_for_drive_export"
        ) as manifest, patch.object(fix, "export_shared_contacts_to_google_drive") as export:
            lines = fix._fix_drive(report)
        self.assertEqual([call.args[0] for call in mark.call_args_list], [4, 5])
        manifest.assert_called_once()
        export.assert_called_once()
        self.assertEqual(len(lines), 2)

    def test_drive_fix_does_nothing_when_matching(self) -> None:
        with patch.object(fix, "export_shared_contacts_to_google_drive") as export:
            self.assertEqual(fix._fix_drive({"drive_missing_ids": [], "drive_extra_ids": []}), [])
        export.assert_not_called()

    def test_google_missing_skipped_when_other_uploads_pending(self) -> None:
        from app.services.contact_service import create_contact

        with patch("app.services.contact_service.create_google_contact_and_notify_share"):
            create_contact({"family_name": "Blank", "given_name": "Card", "fields_text": "AL Central", "meetings_text": "X"})
        with patch.object(fix, "upload_google_contacts") as upload:
            lines = fix._fix_google_missing({"google_missing_ids": [1]})
        self.assertEqual(lines, [])
        upload.assert_not_called()

    def test_share_mapping_sync_sends_missing_and_stale_ids_once(self) -> None:
        report = {
            "recipients": [
                {"missing_person_ids": ["people/a"], "extra_person_ids": ["people/old"]},
                {"missing_person_ids": ["people/a"], "extra_person_ids": []},
            ]
        }
        with patch.object(fix, "sync_share_app_contact_changes", return_value={"ok": True}) as sync:
            lines = fix._fix_share_mappings(report, "owner@gmail.com")
        sync.assert_called_once_with("owner@gmail.com", ["people/a", "people/old"])
        self.assertEqual(len(lines), 1)

    def test_share_mapping_sync_includes_named_missing_cards(self) -> None:
        from app.database import get_connection

        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO contacts (family_name, given_name, google_contact_id)
                VALUES ('Garnett', 'Marsha', 'people/marsha')
                """
            )
            conn.commit()
        report = {
            "recipients": [{"missing_person_ids": [], "extra_person_ids": []}],
            "share_cards": {
                "recipients": {
                    "philip@gmail.com": {"names": {"missing": ["Garnett, Marsha"]}},
                }
            },
        }
        with patch.object(fix, "sync_share_app_contact_changes", return_value={"ok": True}) as sync:
            lines = fix._fix_share_mappings(report, "owner@gmail.com")
        sync.assert_called_once_with("owner@gmail.com", ["people/marsha"])
        self.assertEqual(len(lines), 1)

    def test_run_fix_does_not_scan_whole_group_for_known_mapping_ids(self) -> None:
        report = {
            "recipients": [
                {
                    "email": "philip@gmail.com",
                    "missing": ["Bible, Keith"],
                    "extra_count": 0,
                    "missing_person_ids": ["people/keith"],
                    "extra_person_ids": [],
                }
            ],
            "share_cards": {
                "recipients": {
                    "philip@gmail.com": {"ok": False, "changes": 4, "clean": False},
                    "other@gmail.com": {"ok": False, "changes": 2, "clean": False},
                }
            },
            "drive_missing_ids": [],
            "drive_extra_ids": [],
            "google_missing_ids": [],
            "issues": [],
        }
        with patch.object(fix, "_get_valid_access_token", return_value=("tok", "owner@gmail.com")), patch.object(
            fix, "sync_shared_data_from_google_drive"
        ), patch.object(fix, "refresh_share_roster_check", return_value=report) as refresh, patch.object(
            fix, "sync_share_app_contact_changes", return_value={"ok": True}
        ) as sync, patch.object(fix, "fix_share_recipient_cards") as cards, patch.object(
            fix, "_log_to_edit_list"
        ):
            fix._run_fix()
        sync.assert_called_once_with("owner@gmail.com", ["people/keith"])
        cards.assert_not_called()
        self.assertTrue(refresh.call_args_list)
        self.assertTrue(all(call.kwargs.get("deep") is False for call in refresh.call_args_list))

    def test_google_extra_actions_need_editor_lock(self) -> None:
        with patch.object(fix, "is_editing_allowed", return_value=False):
            self.assertEqual(fix.import_google_extras(["people/x"])["reason"], "not_editor")
            self.assertEqual(fix.delete_google_extras(["people/x"])["reason"], "not_editor")

    def test_google_extra_actions_only_for_listed_people(self) -> None:
        with patch.object(fix, "is_editing_allowed", return_value=True), patch.object(
            fix, "_load_saved_report", return_value={"google_extra_people": [{"id": "people/x", "name": "Stray, Sam"}]}
        ), patch.object(fix, "_get_valid_access_token", return_value=("tok", "owner@gmail.com")), patch.object(
            fix, "_api_json_request", return_value={}
        ) as api, patch.object(fix, "_schedule_share_app_contact_sync") as share, patch.object(
            fix, "_refresh_banner_later"
        ), patch.object(fix, "_log_to_edit_list"):
            refused = fix.delete_google_extras(["people/not-listed"])
            done = fix.delete_google_extras(["people/x", "people/not-listed"])
        self.assertEqual(refused["reason"], "not_listed")
        self.assertTrue(done["ok"])
        self.assertEqual(done["deleted"], 1)
        api.assert_called_once()
        self.assertIn("people/x:deleteContact", api.call_args.args[0])
        share.assert_called_once_with("owner@gmail.com", ["people/x"])


if __name__ == "__main__":
    unittest.main()
