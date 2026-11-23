"""One person must never end up with two Share cards on a recipient.

Cause that was found in production: adding an existing card to a new group failed
for any Google error, including a rate limit, and the import answered by creating
a brand-new card. The roster check could not see the result because each group
looked healthy on its own.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from googleapiclient.errors import HttpError

from app import db, people_recipient
from app.services import recipient_import, roster_group_check as rgc

OWNER = "owner@example.com"
RECIPIENT = "r1@example.com"
ALMSLA = ("share-almsla", "contactGroups/almsla", "contactGroups/r-almsla")
MEETING = ("share-meet", "contactGroups/meet", "contactGroups/r-meet")
PERSON = "people/c-owner"


def _person(given: str, family: str) -> dict:
    return {"names": [{"givenName": given, "familyName": family, "displayName": f"{given} {family}"}]}


class _Resp:
    def __init__(self, status: int) -> None:
        self.status = status
        self.reason = "x"


def _http_error(status: int) -> HttpError:
    return HttpError(_Resp(status), b"{}")


class AddResultTests(unittest.TestCase):
    def _result(self, exc: Exception) -> str:
        class Members:
            def modify(self, **_kw):
                return self

            def execute(self):
                raise exc

        class Groups:
            def members(self):
                return Members()

        class Svc:
            def contactGroups(self):
                return Groups()

        with patch.object(people_recipient, "people_service", return_value=Svc()):
            return people_recipient.add_contact_to_group_result(object(), "people/c1", "contactGroups/g")

    def test_404_means_card_is_missing(self) -> None:
        self.assertEqual(self._result(_http_error(404)), people_recipient.GROUP_ADD_MISSING)

    def test_rate_limit_and_server_errors_are_not_missing(self) -> None:
        for status in (429, 500, 503):
            self.assertEqual(self._result(_http_error(status)), people_recipient.GROUP_ADD_ERROR)

    def test_network_error_is_not_missing(self) -> None:
        self.assertEqual(self._result(OSError("boom")), people_recipient.GROUP_ADD_ERROR)


class AttachTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev_path = db._active_database_path
        db._active_database_path = Path(self._tmpdir.name) / "test.db"
        db.init_db()
        db.upsert_sync_mapping(
            share_id=ALMSLA[0], owner=OWNER, group_resource_name=ALMSLA[1],
            recipient_email=RECIPIENT, owner_person_id=PERSON,
            recipient_person_id="people/r-old", last_hash="h",
        )
        self.group = {
            "owner": OWNER, "recipientEmail": RECIPIENT,
            "shareId": MEETING[0], "resourceName": MEETING[1],
        }
        self.member = {"resourceName": PERSON, **_person("Ann", "Lee")}

    def tearDown(self) -> None:
        db._active_database_path = self._prev_path
        self._tmpdir.cleanup()

    def test_temporary_failure_defers_instead_of_creating_a_copy(self) -> None:
        with patch.object(
            recipient_import, "add_contact_to_group_result", return_value=people_recipient.GROUP_ADD_ERROR
        ):
            with self.assertRaises(recipient_import.AttachDeferred):
                recipient_import._attach_existing_recipient_to_group(
                    object(), self.group, MEETING[2], self.member
                )
        rows = db.list_sync_mappings_for_recipient(RECIPIENT)
        self.assertEqual([r["share_id"] for r in rows], [ALMSLA[0]])  # nothing mapped

    def test_confirmed_missing_card_allows_a_new_one(self) -> None:
        with patch.object(
            recipient_import, "add_contact_to_group_result", return_value=people_recipient.GROUP_ADD_MISSING
        ):
            got = recipient_import._attach_existing_recipient_to_group(
                object(), self.group, MEETING[2], self.member
            )
        self.assertEqual(got, "")

    def test_dead_sibling_is_skipped_for_a_live_one(self) -> None:
        db.upsert_sync_mapping(
            share_id="share-other", owner=OWNER, group_resource_name="contactGroups/other",
            recipient_email=RECIPIENT, owner_person_id=PERSON,
            recipient_person_id="people/r-live", last_hash="h",
        )
        answers = {"people/r-old": people_recipient.GROUP_ADD_MISSING, "people/r-live": people_recipient.GROUP_ADD_OK}
        with patch.object(
            recipient_import, "add_contact_to_group_result", side_effect=lambda _c, rid, _g: answers[rid]
        ):
            got = recipient_import._attach_existing_recipient_to_group(
                object(), self.group, MEETING[2], self.member
            )
        self.assertEqual(got, "people/r-live")
        mapped = {r["share_id"]: r["recipient_person_id"] for r in db.list_sync_mappings_for_recipient(RECIPIENT)}
        self.assertEqual(mapped[MEETING[0]], "people/r-live")


class SplitCardPlanTests(unittest.TestCase):
    def rows(self, *specs):
        return [
            {"id": i + 1, "share_id": s, "group_resource_name": g, "owner_person_id": o, "recipient_person_id": r}
            for i, (s, g, o, r) in enumerate(specs)
        ]

    def test_same_person_two_cards_is_planned_and_busiest_card_kept(self) -> None:
        rows = self.rows(
            (ALMSLA[0], ALMSLA[1], PERSON, "people/a"),
            (MEETING[0], MEETING[1], PERSON, "people/b"),
            ("share-x", "contactGroups/x", PERSON, "people/b"),
        )
        plans = rgc.plan_split_cards(
            rows=rows,
            owner_people={PERSON: _person("Ann", "Lee")},
            recipient_people={"people/a": _person("Ann", "Lee"), "people/b": _person("Ann", "Lee")},
        )
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["keep"], "people/b")
        self.assertEqual(list(plans[0]["merge"]), ["people/a"])

    def test_wrong_person_card_is_left_alone(self) -> None:
        rows = self.rows(
            (ALMSLA[0], ALMSLA[1], PERSON, "people/a"),
            (MEETING[0], MEETING[1], PERSON, "people/b"),
        )
        plans = rgc.plan_split_cards(
            rows=rows,
            owner_people={PERSON: _person("Ann", "Lee")},
            recipient_people={"people/a": _person("Ann", "Lee"), "people/b": _person("Bob", "Ray")},
        )
        self.assertEqual(plans, [])

    def test_card_shared_by_two_people_is_left_alone(self) -> None:
        rows = self.rows(
            (ALMSLA[0], ALMSLA[1], PERSON, "people/a"),
            (MEETING[0], MEETING[1], PERSON, "people/b"),
            (MEETING[0], MEETING[1], "people/other-owner", "people/b"),
        )
        plans = rgc.plan_split_cards(
            rows=rows,
            owner_people={PERSON: _person("Ann", "Lee"), "people/other-owner": _person("Ann", "Lee")},
            recipient_people={"people/a": _person("Ann", "Lee"), "people/b": _person("Ann", "Lee")},
        )
        self.assertEqual(plans, [])

    def test_dead_card_is_not_a_split(self) -> None:
        rows = self.rows(
            (ALMSLA[0], ALMSLA[1], PERSON, "people/a"),
            (MEETING[0], MEETING[1], PERSON, "people/gone"),
        )
        plans = rgc.plan_split_cards(
            rows=rows,
            owner_people={PERSON: _person("Ann", "Lee")},
            recipient_people={"people/a": _person("Ann", "Lee")},
        )
        self.assertEqual(plans, [])


class SplitCardMergeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev_path = db._active_database_path
        db._active_database_path = Path(self._tmpdir.name) / "test.db"
        db.init_db()
        for (share, resource, _gid), rid in ((ALMSLA, "people/a"), (MEETING, "people/b")):
            db.upsert_sync_mapping(
                share_id=share, owner=OWNER, group_resource_name=resource,
                recipient_email=RECIPIENT, owner_person_id=PERSON,
                recipient_person_id=rid, last_hash="h-" + rid,
            )
        self.groups = [
            {"share_id": s, "resource_name": r, "group_id": g, "name": s} for s, r, g in (ALMSLA, MEETING)
        ]
        rows = db.list_sync_mappings_for_recipient(RECIPIENT)
        self.plans = [
            {
                "owner_id": PERSON,
                "keep": "people/a",
                "merge": {"people/b": [r for r in rows if r["recipient_person_id"] == "people/b"]},
            }
        ]
        self.cards = rgc._Cards(object())

    def tearDown(self) -> None:
        db._active_database_path = self._prev_path
        self._tmpdir.cleanup()

    def _run(self, add_ok: bool, delete_ok: bool = True) -> dict:
        calls: list[tuple] = []

        def add(_c, rid, gid):
            calls.append(("add", rid, gid))
            return add_ok

        def delete(_c, rid):
            calls.append(("delete", rid))
            return delete_ok

        with patch.object(rgc, "ensure_contact_in_group", side_effect=add), patch.object(
            rgc, "delete_contact", side_effect=delete
        ), patch.object(time, "sleep"):
            out = rgc._merge_split_cards(
                owner=OWNER, recipient=RECIPIENT, groups=self.groups, creds=object(),
                plans=self.plans, cards=self.cards, deadline=time.monotonic() + 60, limit=10,
            )
        out["calls"] = calls
        return out

    def test_merge_moves_group_then_mapping_then_deletes_extra(self) -> None:
        out = self._run(add_ok=True)
        self.assertEqual(out["merged"], 1)
        self.assertEqual(out["calls"], [("add", "people/a", MEETING[2]), ("delete", "people/b")])
        mapped = {r["share_id"]: r["recipient_person_id"] for r in db.list_sync_mappings_for_recipient(RECIPIENT)}
        self.assertEqual(mapped, {ALMSLA[0]: "people/a", MEETING[0]: "people/a"})
        row = [r for r in db.list_sync_mappings_for_recipient(RECIPIENT) if r["share_id"] == MEETING[0]][0]
        self.assertEqual(row["last_hash"], "h-people/b")  # no needless re-push

    def test_failed_group_add_changes_nothing(self) -> None:
        out = self._run(add_ok=False)
        self.assertEqual(out["merged"], 0)
        self.assertTrue(out["errors"])
        self.assertNotIn(("delete", "people/b"), out["calls"])
        mapped = {r["share_id"]: r["recipient_person_id"] for r in db.list_sync_mappings_for_recipient(RECIPIENT)}
        self.assertEqual(mapped[MEETING[0]], "people/b")

    def test_limit_stops_and_reports_partial(self) -> None:
        with patch.object(rgc, "ensure_contact_in_group", return_value=True), patch.object(
            rgc, "delete_contact", return_value=True
        ), patch.object(time, "sleep"):
            out = rgc._merge_split_cards(
                owner=OWNER, recipient=RECIPIENT, groups=self.groups, creds=object(),
                plans=self.plans, cards=self.cards, deadline=time.monotonic() + 60, limit=0,
            )
        self.assertTrue(out["partial"])
        self.assertEqual(out["merged"], 0)


if __name__ == "__main__":
    unittest.main()
