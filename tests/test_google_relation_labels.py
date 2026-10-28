import unittest

from app.services.google_sync_service import (
    CFS_RELATION_TYPES_FIELD,
    _contact_person_payload,
    _encode_cfs_relation_types,
    _google_relation_type,
    _parse_cfs_relation_types,
    _relation_compare_key,
    _restore_imported_relation_type,
)


class GoogleRelationLabelTests(unittest.TestCase):
    def test_upload_keeps_cfs_husband_and_wife_labels(self) -> None:
        self.assertEqual(_google_relation_type("Husband"), "Husband")
        self.assertEqual(_google_relation_type("Wife"), "Wife")
        self.assertEqual(_google_relation_type("spouse"), "spouse")
        self.assertEqual(_google_relation_type("Child"), "child")

    def test_upload_payload_sends_cfs_labels_and_keeps_hidden_field(self) -> None:
        payload = _contact_person_payload(
            {
                "family_name": "Smith",
                "given_name": "John",
                "relationships": [
                    {"relation_type": "Wife", "relation_value": "Jane"},
                    {"relation_type": "Child", "relation_value": "Tim"},
                ],
            }
        )
        self.assertEqual(
            payload["relations"],
            [
                {"person": "Jane", "type": "Wife"},
                {"person": "Tim", "type": "child"},
            ],
        )
        self.assertEqual(
            payload["userDefined"],
            [{"key": CFS_RELATION_TYPES_FIELD, "value": "Wife=Jane"}],
        )

    def test_import_restores_wife_from_hidden_field_when_google_says_other(self) -> None:
        restored = _restore_imported_relation_type(
            "other",
            "Jane",
            stored_labels=_parse_cfs_relation_types("Wife=Jane"),
        )
        self.assertEqual(restored, "Wife")

    def test_import_keeps_local_husband_when_google_says_spouse(self) -> None:
        restored = _restore_imported_relation_type(
            "spouse",
            "John",
            local_labels={"john": "Husband"},
        )
        self.assertEqual(restored, "Husband")

    def test_import_uses_complementary_label_for_already_other_cards(self) -> None:
        restored = _restore_imported_relation_type(
            "other",
            "Jane",
            complementary_label="Wife",
        )
        self.assertEqual(restored, "Wife")

    def test_import_leaves_true_other_when_there_is_no_spouse_label(self) -> None:
        restored = _restore_imported_relation_type("other", "Pastor Bob")
        self.assertEqual(restored, "other")

    def test_hidden_field_encodes_only_non_google_labels(self) -> None:
        encoded = _encode_cfs_relation_types(
            [
                {"relation_type": "Husband", "relation_value": "John"},
                {"relation_type": "child", "relation_value": "Tim"},
            ]
        )
        self.assertEqual(encoded, "Husband=John")

    def test_sync_compare_treats_husband_and_other_as_the_same_person(self) -> None:
        self.assertEqual(
            _relation_compare_key("Husband", "Jane"),
            _relation_compare_key("other", "Jane"),
        )
        self.assertEqual(
            _relation_compare_key("Wife", "Jane"),
            _relation_compare_key("spouse", "Jane"),
        )
        self.assertNotEqual(
            _relation_compare_key("Husband", "Jane"),
            _relation_compare_key("child", "Jane"),
        )


if __name__ == "__main__":
    unittest.main()
