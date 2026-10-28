import socket
import unittest
from unittest.mock import MagicMock, patch

from app.services.google_sync_service import _create_connection_prefer_ipv4


class PreferIpv4ConnectTests(unittest.TestCase):
    def test_connects_ipv4_before_ipv6(self) -> None:
        ipv6 = (
            socket.AF_INET6,
            socket.SOCK_STREAM,
            6,
            "",
            ("2001:4860:4846:400::", 443, 0, 0),
        )
        ipv4 = (
            socket.AF_INET,
            socket.SOCK_STREAM,
            6,
            "",
            ("142.250.72.14", 443),
        )
        connected = []

        def fake_socket(family, socktype, proto):
            sock = MagicMock()
            def connect(address):
                connected.append((family, address))
            sock.connect.side_effect = connect
            sock.close.return_value = None
            return sock

        with (
            patch("app.services.google_sync_service.socket.getaddrinfo", return_value=[ipv6, ipv4]),
            patch("app.services.google_sync_service.socket.socket", side_effect=fake_socket),
        ):
            sock = _create_connection_prefer_ipv4(("www.googleapis.com", 443), timeout=5)

        self.assertIsNotNone(sock)
        self.assertEqual(connected, [(socket.AF_INET, ("142.250.72.14", 443))])
