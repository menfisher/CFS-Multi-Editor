"""Tests for stable share_id reuse and scoped recipient recovery.

Covers the duplicate-share bug: re-sharing a group must reuse the existing share_id
(no orphan staged snapshots), and recovery must not pull in other recipients' shares.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import db
from app.services import app_data as app_data_svc
from app.services.recipient_recovery import repair_recipient_app_data_from_staged


class _FakeCreds:
    token = "fake-token"


OWNER = "owner@example.com"
R1 = "r1@example.com"
R2 = "r2@example.com"
RESOURCE = "contactGroups/abc123"


def _stub_google(member_ids: list[str]):
    """Patch app_data Google calls so share_contact_group runs without network."""
    app_data_svc.owner_credentials = lambda email: _FakeCreds()
    app_data_svc.get_group_member_ids = lambda creds, resource: list(member_ids)
    app_data_svc.get_people_batch = lambda creds, ids: {
        mid: {"resourceName": mid, "names": [{"displayName": mid}]} for mid in ids
    }


class ShareIdReuseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev_path = db._active_database_path
        db._active_database_path = Path(self._tmpdir.name) / "test.db"
        db.init_db()
        self._orig = (
            app_data_svc.owner_credentials,
            app_data_svc.get_group_member_ids,
            app_data_svc.get_people_batch,
        )
        _stub_google(["people/1", "people/2", "people/3"])

    def tearDown(self) -> None:
        (
            app_data_svc.owner_credentials,
            app_data_svc.get_group_member_ids,
            app_data_svc.get_people_batch,
        ) = self._orig
        db._active_database_path = self._prev_path
        self._tmpdir.cleanup()

    def _recipient_share_id(self, recipient: str) -> str:
        data = db.get_app_data(recipient) or {}
        for g in data.get("sharedGroups") or []:
            if g.get("resourceName") == RESOURCE:
                return str(g.get("shareId") or "")
        return ""

    def test_reshare_reuses_share_id_after_removal(self) -> None:
        app_data_svc.share_contact_group(
            OWNER, RESOURCE, "Group A", [], 3, [R1], notify_recipients=False
        )
        first_id = self._recipient_share_id(R1)
        self.assertTrue(first_id)

        # Owner removes the recipient's row entirely (simulate stop-share + dismiss).
        recipient_data = db.get_app_data(R1) or {}
        recipient_data["sharedGroups"] = []
        db.save_app_data(R1, recipient_data)

        # Re-share the same group to the same recipient.
        app_data_svc.share_contact_group(
            OWNER, RESOURCE, "Group A", [], 3, [R1], notify_recipients=False
        )
        second_id = self._recipient_share_id(R1)

        self.assertEqual(
            first_id, second_id, "re-share must reuse the canonical share_id"
        )

    def test_owner_group_stores_canonical_share_id(self) -> None:
        app_data_svc.share_contact_group(
            OWNER, RESOURCE, "Group A", [], 3, [R1], notify_recipients=False
        )
        recipient_id = self._recipient_share_id(R1)
        owner_data = db.get_app_data(OWNER) or {}
        owner_group = (owner_data.get("groups") or {}).get(RESOURCE) or {}
        self.assertEqual(owner_group.get("shareId"), recipient_id)

    def test_second_recipient_shares_same_canonical_id(self) -> None:
        app_data_svc.share_contact_group(
            OWNER, RESOURCE, "Group A", [], 3, [R1], notify_recipients=False
        )
        app_data_svc.share_contact_group(
            OWNER, RESOURCE, "Group A", [], 3, [R2], notify_recipients=False
        )
        self.assertEqual(self._recipient_share_id(R1), self._recipient_share_id(R2))

    def test_sharing_a_second_group_keeps_the_first(self) -> None:
        other = "contactGroups/meeting"
        app_data_svc.share_contact_group(
            OWNER, RESOURCE, "AlMsLa", [], 3, [R1], notify_recipients=False
        )
        app_data_svc.share_contact_group(
            OWNER, other, "Tallassee", [], 2, [R1], notify_recipients=False
        )
        data = db.get_app_data(R1) or {}
        resources = {g.get("resourceName") for g in data.get("sharedGroups") or []}
        self.assertEqual(resources, {RESOURCE, other})

    def test_sync_recreates_missing_recipient_row(self) -> None:
        app_data_svc.share_contact_group(
            OWNER, RESOURCE, "Tallassee", [], 3, [R1], notify_recipients=False
        )
        db.save_app_data(R1, {"sharedGroups": []})
        orig_recipient_creds = app_data_svc.recipient_credentials
        app_data_svc.recipient_credentials = lambda email: None
        try:
            result = app_data_svc.sync_shared_group(OWNER, RESOURCE)
        finally:
            app_data_svc.recipient_credentials = orig_recipient_creds
        data = db.get_app_data(R1) or {}
        resources = {g.get("resourceName") for g in data.get("sharedGroups") or []}
        self.assertIn(RESOURCE, resources)
        self.assertIn(R1, result.get("syncStatus") or {})


class RepairScopingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev_path = db._active_database_path
        db._active_database_path = Path(self._tmpdir.name) / "test.db"
        db.init_db()

    def tearDown(self) -> None:
        db._active_database_path = self._prev_path
        self._tmpdir.cleanup()

    def _stage(self, share_id: str, resource: str, name: str, n: int) -> None:
        members = [
            {"resourceName": f"people/{share_id}-{i}", "names": [{"displayName": f"c{i}"}]}
            for i in range(n)
        ]
        db.write_shared_contacts(share_id, OWNER, name, resource, members)

    def test_repair_skips_other_recipients_staged_shares(self) -> None:
        # Owner staged two groups. R1 only has mappings for share-1.
        self._stage("share-1", "contactGroups/g1", "Group One (Shared)", 3)
        self._stage("share-2", "contactGroups/g2", "Group Two (Shared)", 4)

        db.upsert_sync_mapping(
            share_id="share-1",
            owner=OWNER,
            group_resource_name="contactGroups/g1",
            recipient_email=R1,
            owner_person_id="people/share-1-0",
            recipient_person_id="people/recip-0",
            last_hash="h",
            last_photo_hash="",
            owner_etag="",
        )

        changed = repair_recipient_app_data_from_staged(R1)
        self.assertTrue(changed)
        data = db.get_app_data(R1) or {}
        resources = {g.get("resourceName") for g in data.get("sharedGroups") or []}
        self.assertIn("contactGroups/g1", resources)
        self.assertNotIn(
            "contactGroups/g2",
            resources,
            "repair must not resurrect a staged share the recipient never received",
        )

    def test_repair_includes_currently_shared_group_without_mappings(self) -> None:
        self._stage("share-3", "contactGroups/g3", "Group Three (Shared)", 2)
        # Owner currently shares g3 with R1 (but no mappings yet) -> needs a mapping
        # to establish R1 as an owner-recipient since repair keys off mappings.
        db.upsert_sync_mapping(
            share_id="seed",
            owner=OWNER,
            group_resource_name="contactGroups/seed",
            recipient_email=R1,
            owner_person_id="people/seed",
            recipient_person_id="people/seed-r",
            last_hash="h",
            last_photo_hash="",
            owner_etag="",
        )
        db.save_app_data(
            OWNER,
            {"groups": {"contactGroups/g3": {"shared": [R1]}}, "sharedGroups": []},
        )

        changed = repair_recipient_app_data_from_staged(R1)
        self.assertTrue(changed)
        data = db.get_app_data(R1) or {}
        resources = {g.get("resourceName") for g in data.get("sharedGroups") or []}
        self.assertIn("contactGroups/g3", resources)


if __name__ == "__main__":
    unittest.main()
