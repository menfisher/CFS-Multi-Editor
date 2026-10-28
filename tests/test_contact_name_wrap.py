import unittest

from app.services.field_list_service import _wrap_contact_name_lines, _wrap_relationship_text


class RelationshipWrapTests(unittest.TestCase):
    def test_wrapped_name_list_keeps_trailing_comma(self) -> None:
        lines = _wrap_relationship_text(
            "grandchildren: Jaylynn, Jathan, Aidan, Kaylynn, Isabella",
            lambda text: float(len(text)),
            40.0,
        )
        self.assertEqual(
            lines,
            ["grandchildren: Jaylynn, Jathan, Aidan,", "Kaylynn, Isabella"],
        )

    def test_contact_name_grandchildren_wrap_keeps_trailing_comma(self) -> None:
        contact = {
            "name": "HOCKERSMITH, Bruce",
            "children": [],
            "other_relationships": [
                {
                    "relation_type": "grandchildren",
                    "relation_value": "Jaylynn, Jathan, Aidan, Kaylynn, Isabella",
                }
            ],
        }
        lines = _wrap_contact_name_lines(contact, lambda text: float(len(text)), 40.0)
        self.assertGreaterEqual(len(lines), 3)
        self.assertEqual(lines[0], "HOCKERSMITH, Bruce")
        self.assertTrue(lines[1].rstrip().endswith(","), lines)
        self.assertFalse(lines[-1].rstrip().endswith(","), lines)

    def test_wrapped_child_uses_comma_not_semicolon(self) -> None:
        contact = {
            "name": "BERRY, Andrew & Christina",
            "children": ["Kynlee", "James", "Jack MOORE", "Lily", "Blakely BERRY"],
            "other_relationships": [],
        }
        lines = _wrap_contact_name_lines(contact, lambda text: float(len(text)), 32.0)
        self.assertEqual(lines[0], "BERRY, Andrew & Christina;")
        self.assertTrue(any("Lily" in line for line in lines), lines)
        self.assertTrue(any("Blakely BERRY" in line for line in lines), lines)
        joined = "\n".join(lines[1:])
        self.assertNotIn(";", joined)
        lily_line = next(line for line in lines if "Lily" in line)
        self.assertTrue(lily_line.rstrip().endswith(","), lines)


if __name__ == "__main__":
    unittest.main()
