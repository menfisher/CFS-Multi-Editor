"""Roster group check: classify real group cards against Share mappings."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config, db
from app.services import roster_group_check as rgc

OWNER = "owner@example.com"
R1 = "r1@example.com"
RESOURCE = "contactGroups/abc123"
GROUP_ID = "contactGroups/recip-1"
SHARE_ID = "share-abc"


def _person(rn: str, given: str, family: str, phone: str = "", email: str = "") -> dict:
    person = {
        "resourceName": rn,
        "names": [{"givenName": given, "familyName": family}],
    }
    if phone:
        person["phoneNumbers"] = [{"value": phone}]
    if email:
        person["emailAddresses"] = [{"value": email}]
    return person


def _mapping(owner_id: str, rid: str = "") -> dict:
    return {
        "owner_person_id": owner_id,
        "recipient_person_id": rid,
        "last_assignment_label": "",
    }


class ClassifyGroupTests(unittest.TestCase):
    def classify(self, owner, mappings, members, recipient, tracked=None):
        if tracked is None:
            tracked = {
                m["recipient_person_id"] for m in mappings.values() if m["recipient_person_id"]
            }
        return rgc.classify_group(
            owner_people=owner,
            mappings=mappings,
            members=members,
            tracked_all=tracked,
            recipient_people=recipient,
        )

    def test_clean_group_has_no_changes(self):
        owner = {"people/c1": _person("people/c1", "Ann", "Lee", "5551110000")}
        recipient = {"people/r1": _person("people/r1", "Ann", "Lee", "5551110000")}
        plan = self.classify(
            owner, {"people/c1": _mapping("people/c1", "people/r1")}, ["people/r1"], recipient
        )
        self.assertEqual(plan["changes"], 0)
        self.assertEqual(plan["healthy"], 1)

    def test_name_duplicate_is_flagged_for_delete(self):
        owner = {"people/c1": _person("people/c1", "Ann", "Lee", "5551110000")}
        recipient = {
            "people/r1": _person("people/r1", "Ann", "Lee", "5551110000"),
            "people/r2": _person("people/r2", "Ann", "Lee", "5551110000"),
        }
        plan = self.classify(
            owner,
            {"people/c1": _mapping("people/c1", "people/r1")},
            ["people/r1", "people/r2"],
            recipient,
            tracked={"people/r1"},
        )
        self.assertEqual(plan["duplicates"], ["people/r2"])
        self.assertEqual(plan["foreign"], [])

    def test_unknown_card_is_foreign_not_deleted(self):
        owner = {"people/c1": _person("people/c1", "Ann", "Lee")}
        recipient = {
            "people/r1": _person("people/r1", "Ann", "Lee"),
            "people/r9": _person("people/r9", "Zed", "Stranger"),
        }
        plan = self.classify(
            owner,
            {"people/c1": _mapping("people/c1", "people/r1")},
            ["people/r1", "people/r9"],
            recipient,
            tracked={"people/r1"},
        )
        self.assertEqual(plan["foreign"], ["people/r9"])
        self.assertEqual(plan["duplicates"], [])

    def test_missing_card_is_adopted_before_creating(self):
        owner = {"people/c1": _person("people/c1", "Ann", "Lee", "5551110000")}
        recipient = {"people/r5": _person("people/r5", "Ann", "Lee", "5551110000")}
        plan = self.classify(
            owner,
            {"people/c1": _mapping("people/c1", "people/gone")},
            ["people/r5"],
            recipient,
            tracked={"people/gone"},
        )
        self.assertEqual(plan["relink"], [("people/c1", "people/r5")])
        self.assertEqual(plan["create"], [])

    def test_missing_card_is_created_when_nothing_to_adopt(self):
        owner = {"people/c1": _person("people/c1", "Ann", "Lee")}
        plan = self.classify(owner, {"people/c1": _mapping("people/c1", "")}, [], {})
        self.assertEqual(plan["create"], ["people/c1"])

    def test_wrong_person_link_is_relinked_and_old_card_cleaned(self):
        # Brenda is mapped to a second copy of Bob; Brenda's real card sits untracked.
        owner = {
            "people/brenda": _person("people/brenda", "Brenda", "Gatlin", email="shared@x.com"),
            "people/bob": _person("people/bob", "Bob", "Gatlin", email="shared@x.com"),
        }
        recipient = {
            "people/bob1": _person("people/bob1", "Bob", "Gatlin", email="shared@x.com"),
            "people/bob2": _person("people/bob2", "Bob", "Gatlin", email="shared@x.com"),
            "people/brenda1": _person("people/brenda1", "Brenda", "Gatlin", email="shared@x.com"),
        }
        mappings = {
            "people/brenda": _mapping("people/brenda", "people/bob2"),
            "people/bob": _mapping("people/bob", "people/bob1"),
        }
        plan = self.classify(
            owner,
            mappings,
            ["people/bob1", "people/bob2", "people/brenda1"],
            recipient,
            tracked={"people/bob1", "people/bob2"},
        )
        self.assertEqual(plan["relink"], [("people/brenda", "people/brenda1")])
        self.assertEqual(plan["duplicates"], ["people/bob2"])

    def test_wrong_link_with_no_better_card_is_repaired_in_place(self):
        owner = {"people/c1": _person("people/c1", "Victor", "Hebert")}
        recipient = {"people/r1": _person("people/r1", "Victor & Elaine", "Hebert")}
        plan = self.classify(
            owner,
            {"people/c1": _mapping("people/c1", "people/r1")},
            ["people/r1"],
            recipient,
        )
        self.assertEqual(plan["repair"], [("people/c1", "people/r1")])
        self.assertEqual(plan["relink"], [])

    def test_nickname_card_is_not_wrong(self):
        owner = {"people/c1": _person("people/c1", "Steve", "Walden")}
        recipient = {"people/r1": _person("people/r1", 'Steve "Stevie"', "Walden")}
        plan = self.classify(
            owner,
            {"people/c1": _mapping("people/c1", "people/r1")},
            ["people/r1"],
            recipient,
        )
        self.assertEqual(plan["changes"], 0)

    def test_card_outside_group_is_added_back(self):
        owner = {"people/c1": _person("people/c1", "Ann", "Lee")}
        recipient = {"people/r1": _person("people/r1", "Ann", "Lee")}
        plan = self.classify(
            owner, {"people/c1": _mapping("people/c1", "people/r1")}, [], recipient
        )
        self.assertEqual(plan["not_in_group"], [("people/c1", "people/r1")])

    def test_shared_card_between_two_owner_ids_is_left_alone(self):
        owner = {
            "people/c1": _person("people/c1", "Ann", "Lee"),
            "people/c2": _person("people/c2", "Bo", "Lee"),
        }
        recipient = {"people/r1": _person("people/r1", "Ann", "Lee")}
        plan = self.classify(
            owner,
            {
                "people/c1": _mapping("people/c1", "people/r1"),
                "people/c2": _mapping("people/c2", "people/r1"),
            },
            ["people/r1"],
            recipient,
        )
        self.assertEqual(plan["changes"], 0)
        self.assertEqual(len(plan["conflicts"]), 2)

    def test_card_mapped_in_another_group_is_not_an_extra(self):
        owner = {"people/c1": _person("people/c1", "Ann", "Lee")}
        recipient = {
            "people/r1": _person("people/r1", "Ann", "Lee"),
            "people/r2": _person("people/r2", "Zed", "Other"),
        }
        plan = self.classify(
            owner,
            {"people/c1": _mapping("people/c1", "people/r1")},
            ["people/r1", "people/r2"],
            recipient,
            tracked={"people/r1", "people/r2"},
        )
        self.assertEqual(plan["changes"], 0)


class SiblingTests(unittest.TestCase):
    def test_dead_mapping_reuses_card_from_another_group(self):
        owner = {"people/c1": _person("people/c1", "Ann", "Lee")}
        recipient = {"people/r1": _person("people/r1", "Ann", "Lee")}
        plan = rgc.classify_group(
            owner_people=owner,
            mappings={"people/c1": _mapping("people/c1", "people/dead")},
            members=[],
            tracked_all={"people/dead", "people/r1"},
            recipient_people=recipient,
            sibling_cards={"people/c1": "people/r1"},
        )
        self.assertEqual(plan["sibling"], [("people/c1", "people/r1")])
        self.assertEqual(plan["create"], [])

    def test_sibling_card_beats_adopting_in_group_copy(self):
        owner = {"people/c1": _person("people/c1", "Ann", "Lee")}
        recipient = {
            "people/r1": _person("people/r1", "Ann", "Lee"),
            "people/r2": _person("people/r2", "Ann", "Lee"),
        }
        plan = rgc.classify_group(
            owner_people=owner,
            mappings={"people/c1": _mapping("people/c1", "")},
            members=["people/r2"],
            tracked_all={"people/r1"},
            recipient_people=recipient,
            sibling_cards={"people/c1": "people/r1"},
        )
        self.assertEqual(plan["sibling"], [("people/c1", "people/r1")])
        self.assertEqual(plan["duplicates"], ["people/r2"])

    def test_pending_owner_is_not_created_twice_in_check_mode(self):
        owner = {"people/c1": _person("people/c1", "Ann", "Lee")}
        plan = rgc.classify_group(
            owner_people=owner,
            mappings={"people/c1": _mapping("people/c1", "")},
            members=[],
            tracked_all=set(),
            recipient_people={},
            pending_owner_ids={"people/c1"},
        )
        self.assertEqual(plan["create"], [])
        self.assertEqual(plan["sibling"], [("people/c1", "")])


class EtagRateLimitTests(unittest.TestCase):
    def _run(self, fetch_result):
        from app.services import contact_sync

        mapping = {
            "id": 1, "share_id": SHARE_ID, "group_resource_name": RESOURCE,
            "recipient_email": R1, "owner_person_id": "people/c1",
            "recipient_person_id": "people/r1", "last_hash": "", "last_photo_hash": "",
            "owner_etag": "", "last_assignment_label": "",
        }
        member = _person("people/c1", "Ann", "Lee")
        with patch.object(contact_sync, "recipient_credentials", lambda e: FakeCreds()), \
                patch.object(contact_sync, "get_person_etags_batch", lambda c, ids: {}), \
                patch.object(contact_sync, "_load_recipient_person", lambda c, r: {}), \
                patch.object(contact_sync, "_fetch_etag_or_gone", lambda c, r: fetch_result), \
                patch.object(contact_sync.db, "upsert_sync_mapping") as upsert, \
                patch.object(contact_sync, "_ensure_recipient_group", lambda *a, **k: "contactGroups/x"), \
                patch.object(contact_sync, "create_contact_in_group", return_value="people/new") as create, \
                patch.object(contact_sync.db, "get_app_data", lambda e: {"sharedGroups": [{
                    "owner": OWNER, "resourceName": RESOURCE, "shareId": SHARE_ID, "name": "G"}]}), \
                patch.object(contact_sync, "ensure_contact_in_group", lambda *a, **k: True), \
                patch.object(contact_sync, "set_contact_photo", lambda *a, **k: True), \
                patch.object(contact_sync, "ensure_assignment_contact_label", lambda *a, **k: True):
            outcome = contact_sync._apply_mapping_update(
                owner=OWNER, mapping=mapping, member_data=member, owner_etag="e"
            )
        return outcome, create, upsert

    def test_rate_limited_etag_lookup_never_creates_a_copy(self):
        outcome, create, upsert = self._run(("", False))
        self.assertEqual(outcome["reason"], "etag_unavailable")
        create.assert_not_called()
        upsert.assert_not_called()

    def test_confirmed_missing_card_is_recreated(self):
        outcome, create, _ = self._run(("", True))
        create.assert_called_once()
        self.assertEqual(outcome["status"], "created")


class SharedKeyTests(unittest.TestCase):
    def test_shared_key_accepted_but_not_for_ops(self):
        with patch.object(config, "CFS_API_KEY", "legacy"), patch.object(
            config, "CFS_SHARED_API_KEY", "editor"
        ):
            self.assertTrue(config.cfs_api_key_valid("legacy"))
            self.assertTrue(config.cfs_api_key_valid("editor"))
            self.assertFalse(config.cfs_api_key_valid("editor", allow_shared=False))
            self.assertTrue(config.cfs_api_key_valid("legacy", allow_shared=False))
            self.assertFalse(config.cfs_api_key_valid(""))
            self.assertFalse(config.cfs_api_key_valid("nope"))

    def test_empty_shared_key_never_matches_blank(self):
        with patch.object(config, "CFS_API_KEY", "legacy"), patch.object(
            config, "CFS_SHARED_API_KEY", ""
        ):
            self.assertFalse(config.cfs_api_key_valid(""))
            self.assertFalse(config.cfs_api_key_valid("editor"))


class FakeCreds:
    token = "fake-token"


class RunRecipientCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev_path = db._active_database_path
        db._active_database_path = Path(self._tmpdir.name) / "test.db"
        db.init_db()
        db.save_app_data(
            R1,
            {
                "sharedGroups": [
                    {
                        "owner": OWNER,
                        "shareId": SHARE_ID,
                        "resourceName": RESOURCE,
                        "name": "AlMsLa",
                        "created": GROUP_ID,
                    }
                ]
            },
        )
        for owner_id, rid in (("people/c1", "people/r1"), ("people/c2", "people/r2")):
            db.upsert_sync_mapping(
                share_id=SHARE_ID,
                owner=OWNER,
                group_resource_name=RESOURCE,
                recipient_email=R1,
                owner_person_id=owner_id,
                recipient_person_id=rid,
            )
        self.owner_people = {
            "people/c1": _person("people/c1", "Ann", "Lee"),
            "people/c2": _person("people/c2", "Bo", "Ray"),
        }
        self.recipient_people = {
            "people/r1": _person("people/r1", "Ann", "Lee"),
            "people/r2": _person("people/r2", "Bo", "Ray"),
            "people/r3": _person("people/r3", "Ann", "Lee"),  # duplicate
        }
        rgc._owner_cache.clear()

    def tearDown(self) -> None:
        db._active_database_path = self._prev_path
        self._tmpdir.cleanup()

    def _patches(self):
        def fake_people(creds, names, person_fields=None):
            pool = {**self.owner_people, **self.recipient_people}
            return {n: pool[n] for n in names if n in pool}

        return [
            patch.object(rgc, "recipient_credentials", lambda e: FakeCreds()),
            patch.object(rgc, "owner_credentials", lambda e: FakeCreds()),
            patch.object(
                rgc, "get_group_member_ids",
                lambda creds, rn, max_members=2000: ["people/r1", "people/r2", "people/r3"],
            ),
            patch.object(rgc, "get_people_batch", fake_people),
        ]

    def test_check_is_read_only(self):
        deleted: list[str] = []
        patches = self._patches() + [
            patch.object(rgc, "delete_contact", lambda c, rid: deleted.append(rid) or True)
        ]
        for p in patches:
            p.start()
        try:
            result = rgc.run_recipient_roster_check(OWNER, R1)
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(deleted, [])
        self.assertFalse(result["clean"])
        group = result["groups"][0]
        self.assertEqual(group["duplicates"], ["Lee, Ann"])
        self.assertEqual(group["counts"]["duplicates"], 1)

    def test_fix_deletes_duplicate(self):
        deleted: list[str] = []
        patches = self._patches() + [
            patch.object(rgc, "delete_contact", lambda c, rid: deleted.append(rid) or True)
        ]
        for p in patches:
            p.start()
        try:
            result = rgc.run_recipient_roster_check(OWNER, R1, apply=True)
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(deleted, ["people/r3"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["groups"][0]["applied"]["deleted"], 1)

    def test_fix_links_second_group_to_existing_card_instead_of_creating(self):
        db.save_app_data(
            R1,
            {
                "sharedGroups": [
                    {
                        "owner": OWNER, "shareId": SHARE_ID, "resourceName": RESOURCE,
                        "name": "AlMsLa", "created": GROUP_ID,
                    },
                    {
                        "owner": OWNER, "shareId": "share-b", "resourceName": "contactGroups/b",
                        "name": "Meeting", "created": "contactGroups/recip-b",
                    },
                ]
            },
        )
        db.upsert_sync_mapping(
            share_id="share-b", owner=OWNER, group_resource_name="contactGroups/b",
            recipient_email=R1, owner_person_id="people/c1", recipient_person_id="people/dead",
        )
        db.upsert_sync_mapping(
            share_id="share-b", owner=OWNER, group_resource_name="contactGroups/b",
            recipient_email=R1, owner_person_id="people/c2", recipient_person_id="people/r2",
        )
        added: list[tuple[str, str]] = []
        created: list[str] = []

        def members(creds, rn, max_members=2000):
            if rn == "contactGroups/recip-b":
                return ["people/r2"]
            return ["people/r1", "people/r2"]

        self.recipient_people.pop("people/r3")
        patches = self._patches() + [
            patch.object(rgc, "get_group_member_ids", members),
            patch.object(
                rgc, "ensure_contact_in_group",
                lambda c, rid, gid: added.append((rid, gid)) or True,
            ),
            patch("app.services.contact_sync._apply_mapping_update",
                  lambda **kw: created.append(kw["mapping"]["owner_person_id"]) or {"status": "created"}),
        ]
        for p in patches:
            p.start()
        try:
            result = rgc.run_recipient_roster_check(OWNER, R1, apply=True)
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(created, [])
        self.assertEqual(added, [("people/r1", "contactGroups/recip-b")])
        fresh = db.load_sync_mappings("share-b", OWNER, "contactGroups/b", R1)[0]
        self.assertEqual(fresh["people/c1"]["recipient_person_id"], "people/r1")
        self.assertTrue(result["ok"], result)

    def test_fix_prunes_dead_rows_for_groups_the_recipient_no_longer_has(self):
        db.upsert_sync_mapping(
            share_id="old-share", owner=OWNER, group_resource_name="contactGroups/old",
            recipient_email=R1, owner_person_id="people/c1", recipient_person_id="people/dead",
        )
        patches = self._patches() + [patch.object(rgc, "delete_contact", lambda c, rid: True)]
        for p in patches:
            p.start()
        try:
            check = rgc.run_recipient_roster_check(OWNER, R1)
            self.assertEqual(check["orphan_rows"], 1)
            self.assertEqual(
                len(db.load_sync_mappings("old-share", OWNER, "contactGroups/old", R1)[0]), 1
            )
            rgc.run_recipient_roster_check(OWNER, R1, apply=True)
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(
            db.load_sync_mappings("old-share", OWNER, "contactGroups/old", R1)[0], {}
        )
        # Rows for live groups are untouched.
        self.assertEqual(len(db.load_sync_mappings(SHARE_ID, OWNER, RESOURCE, R1)[0]), 2)

    def test_fix_refuses_when_too_many_changes(self):
        deleted: list[str] = []
        patches = self._patches() + [
            patch.object(rgc, "delete_contact", lambda c, rid: deleted.append(rid) or True)
        ]
        for p in patches:
            p.start()
        try:
            result = rgc.run_recipient_roster_check(OWNER, R1, apply=True, max_changes=0)
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(deleted, [])
        self.assertFalse(result["ok"])
        self.assertEqual(result["groups"][0]["reason"], "too_many_changes")

    def test_fix_refuses_when_push_queue_busy(self):
        db.enqueue_push_job(
            owner=OWNER, resource_name=RESOURCE, recipient_email=R1, job_type="contact_batch"
        )
        patches = self._patches()
        for p in patches:
            p.start()
        try:
            result = rgc.run_recipient_roster_check(OWNER, R1, apply=True)
        finally:
            for p in patches:
                p.stop()
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "push_queue_busy")

    def test_fix_refuses_on_short_owner_read(self):
        self.owner_people.pop("people/c2")
        patches = self._patches()
        for p in patches:
            p.start()
        try:
            result = rgc.run_recipient_roster_check(OWNER, R1, apply=True)
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(result["groups"][0]["reason"], "owner_read_short")


if __name__ == "__main__":
    unittest.main()
