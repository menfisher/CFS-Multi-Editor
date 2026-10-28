"""Field+Meeting label and Job Title / Department mapping for shared contacts."""

from __future__ import annotations

import unittest

from app.people_recipient import (
    assignment_shared_group_name,
    prepare_contact_person,
    sanitize_contact_for_create,
)


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


if __name__ == "__main__":
    unittest.main()
