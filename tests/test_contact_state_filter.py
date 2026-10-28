import unittest

from app.services.contact_service import contact_matches_state_filters, normalize_state_filters


class ContactStateFilterTests(unittest.TestCase):
    def test_normalize_splits_and_dedupes(self) -> None:
        self.assertEqual(normalize_state_filters(["LA", " ms ", "LA", "MS,AL"]), ["LA", "ms", "AL"])
        self.assertEqual(normalize_state_filters("LA, MS"), ["LA", "MS"])
        self.assertEqual(normalize_state_filters(None), [])
        self.assertEqual(normalize_state_filters(["", "  "]), [])

    def test_retain_known_state_filters_drops_unused_codes(self) -> None:
        from app.services.contact_service import retain_known_state_filters

        self.assertEqual(retain_known_state_filters(["AT", "AR", "LA"], ["AR", "LA", "MS"]), ["AR", "LA"])
        self.assertEqual(retain_known_state_filters(["at"], ["AT"]), ["at"])
        self.assertEqual(retain_known_state_filters(["AT"], ["AR"]), [])

    def test_all_states_matches_everyone(self) -> None:
        self.assertTrue(contact_matches_state_filters("MS", ["LA"], []))
        self.assertTrue(contact_matches_state_filters("", ["LA"], None))

    def test_address_state_matches_without_exception(self) -> None:
        self.assertTrue(contact_matches_state_filters("", ["LA", "TX"], ["LA"]))
        self.assertFalse(contact_matches_state_filters("", ["LA"], ["MS"]))

    def test_exception_overrides_address_state(self) -> None:
        self.assertTrue(contact_matches_state_filters("MS", ["LA"], ["MS"]))
        self.assertFalse(contact_matches_state_filters("MS", ["LA"], ["LA"]))

    def test_combined_states(self) -> None:
        self.assertTrue(contact_matches_state_filters("", ["AL"], ["LA", "AL"]))
        self.assertTrue(contact_matches_state_filters("MS", ["LA"], ["LA", "MS"]))
        self.assertFalse(contact_matches_state_filters("TX", ["LA"], ["LA", "MS"]))

    def test_case_insensitive(self) -> None:
        self.assertTrue(contact_matches_state_filters("ms", ["LA"], ["MS"]))
        self.assertTrue(contact_matches_state_filters("", ["la"], ["LA"]))


if __name__ == "__main__":
    unittest.main()
