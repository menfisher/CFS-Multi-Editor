from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from .config import CFS_API_KEY

BRIDGE_TTL_SECONDS = 120


def _sign(payload_b64: str) -> str:
    return hmac.new(CFS_API_KEY.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()


def create_bridge_token(email: str) -> str:
    payload = {
        "email": email.strip().lower(),
        "exp": int(time.time()) + BRIDGE_TTL_SECONDS,
        "nonce": secrets.token_urlsafe(8),
    }
    payload_b64 = (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        .decode("utf-8")
        .rstrip("=")
    )
    return f"{payload_b64}.{_sign(payload_b64)}"


def verify_bridge_token(token: str) -> str:
    if not CFS_API_KEY:
        raise ValueError("Share app CFS API key is not configured.")
    parts = str(token or "").split(".", 1)
    if len(parts) != 2:
        raise ValueError("Invalid bridge token.")
    payload_b64, signature = parts
    if not hmac.compare_digest(_sign(payload_b64), signature):
        raise ValueError("Invalid bridge token signature.")
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")))
    if int(payload.get("exp") or 0) < int(time.time()):
        raise ValueError("Bridge token expired.")
    email = str(payload.get("email") or "").strip().lower()
    if not email:
        raise ValueError("Bridge token is missing owner email.")
    return email
