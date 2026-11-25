from __future__ import annotations

import http.client
import socket
from urllib.error import HTTPError, URLError

NETWORK_REQUEST_ERRORS: tuple[type[BaseException], ...] = (
    TimeoutError,
    socket.timeout,
    HTTPError,
    URLError,
    OSError,
    ConnectionResetError,
    ConnectionAbortedError,
    http.client.RemoteDisconnected,
    http.client.IncompleteRead,
    http.client.BadStatusLine,
)


def describe_request_error(exc: BaseException) -> str:
    if isinstance(exc, HTTPError):
        return f"HTTP {exc.code}: {exc.reason}".strip()
    if isinstance(exc, URLError):
        reason = str(exc.reason or exc).strip()
        return f"Network error: {reason}" if reason else "Network error"
    message = str(exc).strip()
    if message:
        return f"{type(exc).__name__}: {message}"
    return type(exc).__name__


def user_facing_request_error(exc: BaseException) -> str:
    if isinstance(exc, http.client.RemoteDisconnected):
        return (
            "The connection to Google closed before a response arrived. "
            "This is usually a temporary network problem. Try again in a moment."
        )
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "The request timed out while waiting for Google. Try again when the network is stable."
    if isinstance(exc, URLError):
        reason = str(exc.reason or exc).strip()
        if reason:
            return f"Could not reach Google over the network: {reason}"
        return "Could not reach Google over the network."
    return describe_request_error(exc)