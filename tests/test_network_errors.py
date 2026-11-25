import http.client
import unittest

from app.logging_utils import format_error_log_block
from app.network_errors import user_facing_request_error


class NetworkErrorsTests(unittest.TestCase):
    def test_user_facing_remote_disconnected_message(self) -> None:
        message = user_facing_request_error(http.client.RemoteDisconnected("Remote end closed connection without response"))
        self.assertIn("connection to Google closed", message)

    def test_error_log_block_includes_timestamp_and_summary(self) -> None:
        exc = http.client.RemoteDisconnected("Remote end closed connection without response")
        block = format_error_log_block(method="GET", url="http://127.0.0.1/app-revisions", exc=exc)
        self.assertRegex(block, r"\[\d{4}-\d{2}-\d{2}T")
        self.assertIn("Summary:", block)
        self.assertIn("RemoteDisconnected", block)


if __name__ == "__main__":
    unittest.main()