from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import is_local_web_url
from app.services.google_sync_service import get_share_web_api_key
from app.services.share_web_app_service import share_web_app_url


def _post_share_app_json(path: str, payload: dict) -> dict | None:
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
        with urlopen(request, timeout=120) as response:
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
