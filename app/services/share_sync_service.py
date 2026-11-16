from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import is_local_web_url
from app.services.google_sync_service import get_share_web_api_key
from app.services.share_web_app_service import share_web_app_url


def _post_share_app_json(path: str, payload: dict, *, timeout: int = 120) -> dict | None:
    base_url = share_web_app_url().rstrip("/")
    api_key = get_share_web_api_key()
    if not base_url:
        return {"ok": False, "reason": "missing_share_web_app_url"}
    if not api_key:
        return None

    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = Request(
        f"{base_url}{path}",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-CFS-API-Key": api_key,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            if isinstance(data, dict):
                return data
            return {"ok": True, "result": data}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        if exc.code == 404:
            return {
                "ok": False,
                "reason": "share_app_endpoint_missing_redeploy_required",
                "detail": detail,
            }
        raise RuntimeError(f"Share app request failed ({exc.code}): {detail}") from exc
    except URLError as exc:
        if is_local_web_url(base_url):
            return {"ok": False, "reason": "share_app_unreachable", "detail": str(exc.reason)}
        raise RuntimeError(f"Share app unreachable: {exc.reason}") from exc


def rename_share_app_group(
    owner_email: str,
    resource_name: str,
    old_name: str,
    new_name: str,
) -> dict | None:
    """Ask Share to rename one shared group label in connected recipient accounts."""
    owner = str(owner_email or "").strip().lower()
    if not owner or not str(new_name or "").strip():
        return {"ok": False, "reason": "missing_owner_or_name"}
    try:
        return _post_share_app_json(
            "/api/cfs/rename-shared-group",
            {
                "owner_email": owner,
                "resource_name": str(resource_name or "").strip(),
                "old_name": str(old_name or "").strip(),
                "new_name": str(new_name or "").strip(),
            },
        )
    except RuntimeError as exc:
        return {"ok": False, "reason": str(exc)}


def sync_share_app_contact_changes(owner_email: str, person_ids: list[str]) -> dict | None:
    """Ask the hosted share app to push updated contacts to connected recipients."""
    owner = str(owner_email or "").strip().lower()
    normalized_ids = list(
        dict.fromkeys(
            str(item or "").strip()
            for item in person_ids
            if str(item or "").strip()
        )
    )
    if not owner or not normalized_ids:
        return {"ok": False, "reason": "missing_owner_or_person_ids"}
    return _post_share_app_json(
        "/api/cfs/sync-contact-changes",
        {"owner_email": owner, "person_ids": normalized_ids},
    )


def fetch_share_roster_status(owner_email: str) -> dict | None:
    """Read-only Share mapping counts. Does not start Retry/Sync."""
    owner = str(owner_email or "").strip().lower()
    if not owner:
        return {"ok": False, "reason": "missing_owner"}
    try:
        return _post_share_app_json("/api/cfs/roster-status", {"owner_email": owner})
    except RuntimeError as exc:
        return {"ok": False, "reason": str(exc)}


# The fix call can run up to ~100s on Share (it paces itself for Google's read quota),
# so it needs more headroom than the 120s default.
SHARE_CARD_FIX_TIMEOUT_SECONDS = 200


def _share_card_call(path: str, owner_email: str, recipient_email: str, *, timeout: int) -> dict:
    owner = str(owner_email or "").strip().lower()
    recipient = str(recipient_email or "").strip().lower()
    if not owner or not recipient:
        return {"ok": False, "reason": "missing_owner_or_recipient"}
    try:
        result = _post_share_app_json(
            path,
            {"owner_email": owner, "recipient_email": recipient},
            timeout=timeout,
        )
    except RuntimeError as exc:
        return {"ok": False, "reason": str(exc)}
    if result is None:
        return {"ok": False, "reason": "share_key_not_configured"}
    inner = result.get("result") if isinstance(result.get("result"), dict) else None
    return inner if inner is not None else result


def check_share_recipient_cards(owner_email: str, recipient_email: str) -> dict:
    """Read-only. Compares one recipient's real Google group cards with Share mappings."""
    return _share_card_call(
        "/api/cfs/roster-group-check", owner_email, recipient_email, timeout=SHARE_CARD_FIX_TIMEOUT_SECONDS
    )


def fix_share_recipient_cards(owner_email: str, recipient_email: str) -> dict:
    """Relink, create and de-duplicate one recipient's cards. May return partial=True; call again."""
    return _share_card_call(
        "/api/cfs/roster-group-fix", owner_email, recipient_email, timeout=SHARE_CARD_FIX_TIMEOUT_SECONDS
    )
