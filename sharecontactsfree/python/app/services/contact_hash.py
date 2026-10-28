"""Contact hash helpers ported from gsWebApp.gs for incremental sync."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from ..people_recipient import sanitize_contact_for_create

SYNC_WRITABLE_FIELDS = (
    "addresses",
    "biographies",
    "birthdays",
    "emailAddresses",
    "names",
    "nicknames",
    "organizations",
    "phoneNumbers",
    "relations",
    "urls",
    "userDefined",
)


def stable_stringify(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ",".join(stable_stringify(v) for v in value) + "]"
    if isinstance(value, dict):
        keys = sorted(value.keys())
        parts = [f"{json.dumps(k)}:{stable_stringify(value[k])}" for k in keys]
        return "{" + ",".join(parts) + "}"
    return json.dumps(str(value))


def compute_hash(value: Any) -> str:
    digest = hashlib.sha256(stable_stringify(value).encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def compute_contact_hash(member_data: dict[str, Any] | None) -> str:
    payload = sanitize_contact_for_create(member_data)
    payload.pop("memberships", None)
    payload.pop("photos", None)
    return compute_hash(payload)


def compute_photo_hash(member_data: dict[str, Any] | None) -> str:
    from ..people_recipient import contact_photo_url

    url = contact_photo_url(member_data)
    if not url:
        return "no-photo"
    return compute_hash(url)


def build_update_payload(member_data: dict[str, Any] | None) -> dict[str, Any]:
    clean = sanitize_contact_for_create(member_data)
    return {field: clean.get(field) or [] for field in SYNC_WRITABLE_FIELDS}
