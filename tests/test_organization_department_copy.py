import unittest

from app.services.google_sync_service import (
    _contact_person_payload,
    _parse_field_meeting_from_organizations,
    _resolve_field_meeting,
)


class OrganizationDepartmentCopyTests(unittest.TestCase):
    def test_parse_uses_job_title_as_field_and_company_as_meeting(self) -> None:
        field, meeting = _parse_field_meeting_from_organizations(
            [{"title": "Richmond", "department": "Western Virginia", "name": "Midlothian"}]
        )
        self.assertEqual(field, "Richmond")
        self.assertEqual(meeting, "Midlothian")

    def test_parse_falls_back_to_department(self) -> None:
        field, meeting = _parse_field_meeting_from_organizations(
            [{"department": "Raleigh", "name": ""}]
        )
        self.assertEqual(field, "Raleigh")
        self.assertEqual(meeting, "")

    def test_resolve_uses_department_when_groups_only_have_meeting(self) -> None:
        field, meeting = _resolve_field_meeting(
            ["Some Meeting"],
            [{"department": "Raleigh", "name": "Cary"}],
        )
        self.assertEqual(field, "Raleigh")
        self.assertEqual(meeting, "Cary")

    def test_resolve_uses_job_title_and_company_instead_of_labels(self) -> None:
        field, meeting = _resolve_field_meeting(
            ["Other Contacts - Western VA Contacts (Shared)", "Western Virginia - Charlottesville, VA (Shared)"],
            [{"title": "Western Virginia", "department": "Other", "name": "Waynesboro"}],
        )
        self.assertEqual(field, "Western Virginia")
        self.assertEqual(meeting, "Waynesboro")

    def test_resolve_prefers_real_group_over_other_contacts_bucket(self) -> None:
        field, meeting = _resolve_field_meeting(
            [
                "Other Contacts - NoVA Contacts (Shared)",
                "Northern Virginia and Fredericksburg - Falls Church (Shared)",
            ],
            [],
        )
        self.assertEqual(field, "Northern Virginia and Fredericksburg")
        self.assertEqual(meeting, "Falls Church")

    def test_resolve_keeps_other_contacts_when_that_is_the_only_assignment(self) -> None:
        field, meeting = _resolve_field_meeting(
            ["Other Contacts - Moved Away (Shared)"],
            [],
        )
        self.assertEqual(field, "Other Contacts")
        self.assertEqual(meeting, "Moved Away")

    def test_resolve_keeps_company_when_a_label_disagrees(self) -> None:
        field, meeting = _resolve_field_meeting(
            ["Richmond - Midlothian (Shared)"],
            [{"department": "Raleigh", "title": "Western Virginia", "name": "Cary"}],
        )
        self.assertEqual(field, "Western Virginia")
        self.assertEqual(meeting, "Cary")

    def test_upload_address_keeps_contact_card_lines_for_google(self) -> None:
        payload = _contact_person_payload(
            {
                "family_name": "Baker",
                "given_name": "Ned",
                "addresses": [
                    {
                        "street_address": "Riverwalk Senior's Living",
                        "extended_address": "50001 Old Montgomery Hwy #204",
                        "city": "Tuscaloosa",
                        "region": "AL",
                        "postal_code": "35005",
                        "address_type": "Home",
                    }
                ],
            }
        )
        address = payload["addresses"][0]
        self.assertEqual(address["streetAddress"], "Riverwalk Senior's Living")
        self.assertEqual(address["extendedAddress"], "50001 Old Montgomery Hwy #204")
        self.assertEqual(address["city"], "Tuscaloosa")
        self.assertEqual(
            address["formattedValue"],
            "Riverwalk Senior's Living\n50001 Old Montgomery Hwy #204\nTuscaloosa, AL 35005",
        )

    def test_upload_address_splits_a_line_break_inside_street(self) -> None:
        payload = _contact_person_payload(
            {
                "addresses": [
                    {
                        "street_address": "123 Main\nApt 4",
                        "city": "Cary",
                        "region": "NC",
                        "postal_code": "27511",
                    }
                ],
            }
        )
        address = payload["addresses"][0]
        self.assertEqual(address["streetAddress"], "123 Main")
        self.assertEqual(address["extendedAddress"], "Apt 4")
        self.assertEqual(address["formattedValue"], "123 Main\nApt 4\nCary, NC 27511")

    def test_upload_payload_writes_field_to_job_title_only(self) -> None:
        payload = _contact_person_payload(
            {
                "family_name": "Baker",
                "given_name": "Ned",
                "fields_text": "Raleigh",
                "meetings_text": "Cary",
            }
        )
        self.assertEqual(payload["organizations"][0]["title"], "Raleigh")
        self.assertEqual(payload["organizations"][0]["name"], "Cary")
        self.assertNotIn("department", payload["organizations"][0])


if __name__ == "__main__":
    unittest.main()
