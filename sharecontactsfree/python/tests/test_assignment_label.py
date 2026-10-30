"""Field+Meeting label and Job Title / Department mapping for shared contacts."""

from __future__ import annotations

import unittest

from app.people_recipient import (
    assignment_shared_group_name,
    prepare_contact_person,
    sanitize_contact_for_create,
)
from app.services.contact_hash import build_update_payload, compute_contact_hash


class AssignmentLabelTests(unittest.TestCase):
    def test_assignment_name_uses_job_title_and_company(self) -> None:
        name = assignment_shared_group_name(
            {
                "organizations": [
                    {
                        "title": "MS S & AL Coast, New Orleans",
                        "department": "MS S & AL Coast, New Orleans",
                        "name": "Diamondhead (GOODWIN)",
                    }
                ]
            }
        )
        self.assertEqual(
            name,
            "MS S & AL Coast, New Orleans - Diamondhead (GOODWIN) (Shared)",
        )

    def test_assignment_name_falls_back_to_department(self) -> None:
        name = assignment_shared_group_name(
            {"organizations": [{"department": "Raleigh", "name": "Cary"}]}
        )
        self.assertEqual(name, "Raleigh - Cary (Shared)")

    def test_assignment_name_empty_without_field_and_meeting(self) -> None:
        self.assertEqual(assignment_shared_group_name({"organizations": []}), "")
        self.assertEqual(
            assignment_shared_group_name({"organizations": [{"title": "Raleigh"}]}),
            "",
        )

    def test_prepare_adds_field_meeting_membership(self) -> None:
        payload = prepare_contact_person(
            {"names": [{"givenName": "Edward", "familyName": "Archer"}]},
            "contactGroups/almsla",
            extra_group_ids=["contactGroups/field-meeting"],
        )
        memberships = [
            item["contactGroupMembership"]["contactGroupResourceName"]
            for item in payload["memberships"]
        ]
        self.assertEqual(
            memberships,
            ["contactGroups/almsla", "contactGroups/field-meeting"],
        )

    def test_prepare_does_not_duplicate_the_import_group(self) -> None:
        payload = prepare_contact_person(
            {},
            "contactGroups/field-meeting",
            extra_group_ids=["contactGroups/field-meeting", ""],
        )
        memberships = [
            item["contactGroupMembership"]["contactGroupResourceName"]
            for item in payload["memberships"]
        ]
        self.assertEqual(memberships, ["contactGroups/field-meeting"])

    def test_sanitize_drops_department_copied_from_job_title(self) -> None:
        clean = sanitize_contact_for_create(
            {
                "organizations": [
                    {
                        "title": "MS S & AL Coast, New Orleans",
                        "department": "MS S & AL Coast, New Orleans",
                        "name": "Diamondhead (GOODWIN)",
                    }
                ]
            }
        )
        organization = clean["organizations"][0]
        self.assertEqual(organization["title"], "MS S & AL Coast, New Orleans")
        self.assertEqual(organization["name"], "Diamondhead (GOODWIN)")
        self.assertNotIn("department", organization)

    def test_sanitize_keeps_department_when_it_differs(self) -> None:
        clean = sanitize_contact_for_create(
            {
                "organizations": [
                    {"title": "Pastor", "department": "Youth", "name": "Church"}
                ]
            }
        )
        self.assertEqual(clean["organizations"][0]["department"], "Youth")

    def test_update_payload_clears_copied_department_without_changing_hash(self) -> None:
        copied = {
            "organizations": [
                {
                    "title": "MS S & AL Coast, New Orleans",
                    "department": "MS S & AL Coast, New Orleans",
                    "name": "Diamondhead (GOODWIN)",
                }
            ]
        }
        already_clean = {
            "organizations": [
                {
                    "title": "MS S & AL Coast, New Orleans",
                    "name": "Diamondhead (GOODWIN)",
                }
            ]
        }
        payload = build_update_payload(copied)
        organization = payload["organizations"][0]
        self.assertEqual(organization["title"], "MS S & AL Coast, New Orleans")
        self.assertEqual(organization["name"], "Diamondhead (GOODWIN)")
        self.assertEqual(organization["department"], "")
        self.assertEqual(build_update_payload(already_clean)["organizations"][0]["department"], "")
        self.assertEqual(compute_contact_hash(copied), compute_contact_hash(already_clean))

    def test_update_payload_keeps_a_real_department(self) -> None:
        payload = build_update_payload(
            {
                "organizations": [
                    {"title": "Pastor", "department": "Youth", "name": "Church"}
                ]
            }
        )
        self.assertEqual(payload["organizations"][0]["department"], "Youth")


if __name__ == "__main__":
    unittest.main()
