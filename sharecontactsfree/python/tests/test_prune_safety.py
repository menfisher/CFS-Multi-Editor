"""A short read of the owner's contacts must never delete recipient contacts.

Google drops entries from bulk reads. Treating those absent contacts as
"no longer shared" deleted them from the recipient's Google account.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.people_api import get_people_batch
from app.services.app_data import owner_contact_list_is_complete


def _person(idx: int) -> dict:
    return {"resourceName": f"people/{idx}", "names": [{"displayName": f"P{idx}"}]}


class PeopleBatchRetryTests(unittest.TestCase):
    def test_transient_failure_is_retried_rather_than_dropped(self) -> None:
        from googleapiclient.errors import HttpError

        resp = MagicMock()
        resp.status = 502
        error = HttpError(resp, b"bad gateway")

        execute = MagicMock(
            side_effect=[error, {"responses": [{"person": _person(1)}]}]
        )
        service = MagicMock()
        service.people.return_value.getBatchGet.return_value.execute = execute

        with (
            patch("app.people_api.people_service", return_value=service),
            patch("app.people_api.time.sleep"),
        ):
            out = get_people_batch(object(), ["people/1"])

        self.assertEqual(execute.call_count, 2)
        self.assertIn("people/1", out)

    def test_persistent_failure_raises_instead_of_returning_a_short_list(self) -> None:
        from googleapiclient.errors import HttpError

        resp = MagicMock()
        resp.status = 502
        error = HttpError(resp, b"bad gateway")

        service = MagicMock()
        service.people.return_value.getBatchGet.return_value.execute = MagicMock(
            side_effect=error
        )

        with (
            patch("app.people_api.people_service", return_value=service),
            patch("app.people_api.time.sleep"),
        ):
            with self.assertRaises(HttpError):
                get_people_batch(object(), ["people/1"])


class PruneGuardTests(unittest.TestCase):
    def test_short_read_is_not_treated_as_unshared_contacts(self) -> None:
        # One dropped chunk of 50 is what removed 50 contacts from a recipient.
        self.assertFalse(
            owner_contact_list_is_complete([_person(i) for i in range(676)], 726)
        )

    def test_whole_read_allows_pruning(self) -> None:
        self.assertTrue(
            owner_contact_list_is_complete([_person(i) for i in range(726)], 726)
        )

    def test_empty_group_never_authorizes_deletions(self) -> None:
        self.assertFalse(owner_contact_list_is_complete([], 0))


if __name__ == "__main__":
    unittest.main()
