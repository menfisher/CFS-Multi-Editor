from __future__ import annotations

import json
import re
from urllib.parse import urlencode

from app.database import fetch_all, fetch_one, get_connection
from app.services.google_sync_service import (
    GOOGLE_PEOPLE_BASE_URL,
    _api_get_json,
    _api_json_request,
    _clean,
    _contact_groups_lookup,
    _get_valid_access_token,
    _group_membership_text,
    _load_google_account,
    _now_text,
    _split_group_membership_text,
    ensure_google_sync_records,
)
from app.services.share_sync_service import rename_share_app_group


_SHARED_SUFFIX = re.compile(r"\s*\(shared\)\s*$", re.I)


def group_name_variants(name: str) -> list[str]:
    cleaned = _clean(name)
    if not cleaned:
        return []
    base = _SHARED_SUFFIX.sub("", cleaned).strip() or cleaned
    variants: list[str] = []
    for item in (cleaned, base, f"{base} (Shared)"):
        if item and item.casefold() not in {value.casefold() for value in variants}:
            variants.append(item)
    return variants


def group_names_equivalent(left: str, right: str) -> bool:
    left_names = {item.casefold() for item in group_name_variants(left)}
    right_names = {item.casefold() for item in group_name_variants(right)}
    return bool(left_names and right_names and left_names & right_names)


def get_pending_shared_group_rename() -> dict:
    ensure_google_sync_records()
    row = fetch_one("SELECT pending_shared_group_rename FROM google_sync_state WHERE id = 1")
    raw = str((row or {}).get("pending_shared_group_rename") or "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _set_pending_shared_group_rename(payload: dict | None) -> None:
    ensure_google_sync_records()
    encoded = json.dumps(payload, separators=(",", ":")) if payload else ""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE google_sync_state
            SET pending_shared_group_rename = ?, updated_at = ?
            WHERE id = 1
            """,
            (encoded, _now_text()),
        )
        conn.commit()


def replace_membership_group_name(value: str, old_name: str, new_name: str) -> str:
    next_name = _clean(new_name)
    if not next_name:
        return str(value or "")
    replaced = False
    names: list[str] = []
    for item in _split_group_membership_text(value):
        if group_names_equivalent(item, old_name):
            if next_name not in names:
                names.append(next_name)
            replaced = True
            continue
        if item not in names:
            names.append(item)
    if not replaced:
        return str(value or "")
    return _group_membership_text(names)


def replace_local_group_membership_name(old_name: str, new_name: str) -> int:
    updated = 0
    rows = fetch_all("SELECT id, group_membership FROM contacts")
    with get_connection() as conn:
        for row in rows:
            current = str(row.get("group_membership") or "")
            next_value = replace_membership_group_name(current, old_name, new_name)
            if next_value == current:
                continue
            conn.execute(
                "UPDATE contacts SET group_membership = ? WHERE id = ?",
                (next_value, int(row["id"])),
            )
            updated += 1
        conn.commit()
    return updated


def _find_group_resource(
    resource_to_name: dict[str, str],
    name_to_resource: dict[str, str],
    target_name: str,
) -> str:
    blocked = {"contactgroups/mycontacts", "contactgroups/starred"}
    exact = _clean(name_to_resource.get(_clean(target_name)))
    if exact and exact.casefold() not in blocked:
        return exact

    matches: list[str] = []
    seen: set[str] = set()
    for variant in group_name_variants(target_name):
        for resource_name, group_name in resource_to_name.items():
            resource = _clean(resource_name)
            if not resource or resource.casefold() in blocked or resource in seen:
                continue
            if group_names_equivalent(group_name, variant):
                seen.add(resource)
                matches.append(resource)

    if not matches:
        return ""
    target_fold = _clean(target_name).casefold()
    for resource_name in matches:
        if _clean(resource_to_name.get(resource_name)).casefold() == target_fold:
            return resource_name
    return matches[0]


def _rename_owner_google_group(access_token: str, resource_name: str, new_name: str) -> None:
    details = _api_get_json(f"{GOOGLE_PEOPLE_BASE_URL}/{resource_name}", access_token)
    current_name = _clean(details.get("formattedName") or details.get("name"))
    if group_names_equivalent(current_name, new_name) and current_name == _clean(new_name):
        return
    etag = _clean(details.get("etag"))
    payload = {"contactGroup": {"name": _clean(new_name)}}
    if etag:
        payload["contactGroup"]["etag"] = etag
    url = f"{GOOGLE_PEOPLE_BASE_URL}/{resource_name}?{urlencode({'updateGroupFields': 'name'})}"
    _api_json_request(
        url,
        access_token,
        method="PUT",
        json_data={
            **payload,
            "updateGroupFields": "name",
        },
    )


def _remember_rename_pending(result: dict, old_name: str, new_name: str) -> None:
    notice = str(result.get("notice_key") or "")
    if notice == "shared_group_renamed" and result.get("ok"):
        _set_pending_shared_group_rename(None)
        return
    if notice in {"", "shared_group_rename_not_found", "shared_group_rename_collision"}:
        return
    _set_pending_shared_group_rename(
        {
            "old_name": old_name,
            "new_name": new_name,
            "resource_name": str(result.get("resource_name") or ""),
        }
    )


def sync_share_invite_display_name(group_name: str) -> dict | None:
    """Store the Settings catch-all name on Share so new invite emails use it."""
    next_name = _clean(group_name)
    if not next_name:
        return None
    account = _load_google_account()
    email = str(account.get("account_email") or "").strip()
    if not email:
        return None
    pending = get_pending_shared_group_rename()
    resource_name = str((pending or {}).get("resource_name") or "").strip()
    return rename_share_app_group(email, resource_name, next_name, next_name)


def apply_shared_contacts_group_rename(old_name: str, new_name: str) -> dict:
    previous = _clean(old_name)
    next_name = _clean(new_name)
    result = {
        "ok": True,
        "notice_key": "shared_group_renamed",
        "local_updated": 0,
        "google_renamed": False,
        "resource_name": "",
        "share": None,
        "reason": "",
    }
    if not previous or not next_name or previous.casefold() == next_name.casefold():
        result["notice_key"] = ""
        return result

    try:
        result["local_updated"] = replace_local_group_membership_name(previous, next_name)

        account = _load_google_account()
        if str(account.get("account_status") or "").strip().lower() != "connected":
            result["notice_key"] = "shared_group_rename_local_only"
            result["reason"] = "google_not_connected"
            return result

        try:
            access_token, account_email = _get_valid_access_token()
        except RuntimeError as exc:
            result["ok"] = False
            result["notice_key"] = "shared_group_rename_local_only"
            result["reason"] = str(exc)
            return result

        resource_to_name, name_to_resource = _contact_groups_lookup(access_token)
        old_resource = _find_group_resource(resource_to_name, name_to_resource, previous)
        new_resource = _find_group_resource(resource_to_name, name_to_resource, next_name)
        if old_resource and new_resource and old_resource != new_resource:
            result["ok"] = False
            result["notice_key"] = "shared_group_rename_collision"
            result["reason"] = "new_name_already_exists"
            return result

        resource_name = old_resource or new_resource
        if not resource_name:
            result["ok"] = False
            result["notice_key"] = "shared_group_rename_not_found"
            result["reason"] = "owner_group_not_found"
            return result

        result["resource_name"] = resource_name
        if old_resource and old_resource != new_resource:
            try:
                _rename_owner_google_group(access_token, resource_name, next_name)
            except Exception as exc:
                result["ok"] = False
                result["notice_key"] = "shared_group_rename_google_failed"
                result["reason"] = str(exc)
                return result
        result["google_renamed"] = True

        share_result = rename_share_app_group(
            account_email,
            resource_name,
            previous,
            next_name,
        )
        result["share"] = share_result
        if share_result is None:
            result["notice_key"] = "shared_group_rename_share_skipped"
            return result
        if not share_result.get("ok"):
            result["ok"] = False
            result["notice_key"] = "shared_group_rename_share_failed"
            result["reason"] = str(share_result.get("reason") or "share_rename_failed")
            return result
        return result
    finally:
        _remember_rename_pending(result, previous, next_name)
