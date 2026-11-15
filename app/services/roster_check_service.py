from __future__ import annotations

import json
from urllib.parse import urlencode

from app.database import get_connection
from app.services.google_sync_service import (
    GOOGLE_PEOPLE_BASE_URL,
    _api_get_json,
    _clean,
    _drive_load_json_file_content,
    _ensure_shared_drive_storage,
    _get_valid_access_token,
    _load_google_account,
    _now_text,
    ensure_google_sync_records,
)
from app.services.share_sync_service import fetch_share_roster_status


ROSTER_NAME_SAMPLE = 12


def _person_label(family: str, given: str, fallback: str = "") -> str:
    family_name = str(family or "").strip()
    given_name = str(given or "").strip()
    if family_name and given_name:
        return f"{family_name}, {given_name}"
    return family_name or given_name or fallback


def _sample_labels(labels: list[str], limit: int = ROSTER_NAME_SAMPLE) -> str:
    cleaned = [str(item).strip() for item in labels if str(item).strip()]
    if not cleaned:
        return ""
    shown = cleaned[:limit]
    extra = len(cleaned) - len(shown)
    text = "; ".join(shown)
    if extra > 0:
        text = f"{text}; and {extra} more"
    return text


def build_roster_report(
    *,
    cfs_people: list[dict],
    google_people: list[dict] | None,
    drive_ids: set[int] | None,
    drive_labels: dict[int, str],
    recipient_maps: dict[str, set[str]] | None,
) -> dict:
    cfs_by_person = {
        str(item.get("google_contact_id") or "").strip(): item
        for item in cfs_people
        if str(item.get("google_contact_id") or "").strip().startswith("people/")
    }
    cfs_by_id = {int(item.get("id") or 0): item for item in cfs_people if int(item.get("id") or 0) > 0}
    cfs_person_ids = set(cfs_by_person)
    cfs_local_ids = set(cfs_by_id)

    google_missing: list[str] = []
    google_extra: list[str] = []
    google_count = None
    if google_people is not None:
        google_by_id = {
            str(item.get("id") or "").strip(): item
            for item in google_people
            if str(item.get("id") or "").strip().startswith("people/")
        }
        google_count = len(google_by_id)
        google_missing = [
            str(cfs_by_person[person_id].get("name") or person_id)
            for person_id in sorted(cfs_person_ids - set(google_by_id))
        ]
        google_extra = [
            str(google_by_id[person_id].get("name") or person_id)
            for person_id in sorted(set(google_by_id) - cfs_person_ids)
        ]

    drive_missing: list[str] = []
    drive_extra: list[str] = []
    drive_count = None
    if drive_ids is not None:
        drive_count = len(drive_ids)
        drive_missing = [
            str(cfs_by_id[contact_id].get("name") or f"contact {contact_id}")
            for contact_id in sorted(cfs_local_ids - drive_ids)
        ]
        drive_extra = [
            str(drive_labels.get(contact_id) or f"contact {contact_id}")
            for contact_id in sorted(drive_ids - cfs_local_ids)
        ]

    recipients: list[dict] = []
    if recipient_maps is not None:
        for email in sorted(recipient_maps):
            mapped = {str(item).strip() for item in recipient_maps[email] if str(item).strip()}
            missing = [
                str(cfs_by_person[person_id].get("name") or person_id)
                for person_id in sorted(cfs_person_ids - mapped)
            ]
            extra_count = len(mapped - cfs_person_ids)
            recipients.append(
                {
                    "email": email,
                    "count": len(mapped),
                    "missing": missing,
                    "extra_count": extra_count,
                }
            )

    mismatch = bool(
        google_missing
        or google_extra
        or drive_missing
        or drive_extra
        or any(item["missing"] or item["extra_count"] for item in recipients)
        or (google_count is not None and google_count != len(cfs_people))
        or (drive_count is not None and drive_count != len(cfs_people))
    )
    lines = _roster_alert_lines(
        cfs_count=len(cfs_people),
        google_count=google_count,
        drive_count=drive_count,
        google_missing=google_missing,
        google_extra=google_extra,
        drive_missing=drive_missing,
        drive_extra=drive_extra,
        recipients=recipients,
        mismatch=mismatch,
    )
    return {
        "checked_at": _now_text(),
        "mismatch": mismatch,
        "cfs_count": len(cfs_people),
        "google_count": google_count,
        "drive_count": drive_count,
        "google_missing": google_missing,
        "google_extra": google_extra,
        "drive_missing": drive_missing,
        "drive_extra": drive_extra,
        "recipients": recipients,
        "lines": lines,
        "message": " ".join(lines),
    }


def _roster_alert_lines(
    *,
    cfs_count: int,
    google_count: int | None,
    drive_count: int | None,
    google_missing: list[str],
    google_extra: list[str],
    drive_missing: list[str],
    drive_extra: list[str],
    recipients: list[dict],
    mismatch: bool,
) -> list[str]:
    if not mismatch:
        return []
    counts = [f"CFS {cfs_count}"]
    if google_count is not None:
        counts.append(f"owner Google {google_count}")
    if drive_count is not None:
        counts.append(f"Drive {drive_count}")
    lines = [
        "Share roster mismatch.",
        " · ".join(counts) + ".",
    ]
    if google_missing or google_extra:
        parts = []
        if google_missing:
            parts.append(f"missing {_sample_labels(google_missing)}")
        if google_extra:
            parts.append(f"extra {_sample_labels(google_extra)}")
        lines.append("Owner Google: " + "; ".join(parts) + ".")
    if drive_missing or drive_extra:
        parts = []
        if drive_missing:
            parts.append(f"missing {_sample_labels(drive_missing)}")
        if drive_extra:
            parts.append(f"extra {_sample_labels(drive_extra)}")
        lines.append("Drive: " + "; ".join(parts) + ".")
    for item in recipients:
        if not item["missing"] and not item["extra_count"]:
            continue
        parts = [f"{item['count']} cards"]
        if item["missing"]:
            parts.append(f"missing {_sample_labels(item['missing'])}")
        if item["extra_count"]:
            extra = item["extra_count"]
            parts.append(f"{extra} extra not in CFS")
        lines.append(f"{item['email']}: " + "; ".join(parts) + ".")
    return lines


def _local_cfs_people() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, google_contact_id, family_name, given_name
            FROM contacts
            ORDER BY family_name, given_name, id
            """
        ).fetchall()
    people = []
    for row in rows:
        people.append(
            {
                "id": int(row["id"] or 0),
                "google_contact_id": _clean(row["google_contact_id"]),
                "name": _person_label(row["family_name"], row["given_name"], f"contact {row['id']}"),
            }
        )
    return people


def _owner_google_people(access_token: str) -> list[dict]:
    people: list[dict] = []
    page_token = ""
    for _ in range(10):
        params = {
            "personFields": "names,metadata",
            "pageSize": 1000,
            "sources": "READ_SOURCE_TYPE_CONTACT",
        }
        if page_token:
            params["pageToken"] = page_token
        payload = _api_get_json(
            f"{GOOGLE_PEOPLE_BASE_URL}/people/me/connections?{urlencode(params)}",
            access_token,
        )
        for person in payload.get("connections") or []:
            if (person.get("metadata") or {}).get("deleted"):
                continue
            resource = str(person.get("resourceName") or "").strip()
            if not resource.startswith("people/"):
                continue
            names = person.get("names") or []
            family = given = ""
            if names and isinstance(names[0], dict):
                family = str(names[0].get("familyName") or "")
                given = str(names[0].get("givenName") or "")
            people.append({"id": resource, "name": _person_label(family, given, resource)})
        page_token = str(payload.get("nextPageToken") or "")
        if not page_token:
            break
    return people


def _drive_roster_snapshot(access_token: str) -> tuple[set[int], dict[int, str]]:
    storage = _ensure_shared_drive_storage(access_token, contact_only=True)
    file_id = str(storage.get("contacts_current_file_id") or "").strip()
    if not file_id:
        return set(), {}
    payload = _drive_load_json_file_content(access_token, file_id)
    ids: set[int] = set()
    labels: dict[int, str] = {}
    if not isinstance(payload, dict):
        return ids, labels
    entries = payload.get("contacts") or []
    if not isinstance(entries, list):
        return ids, labels
    for item in entries:
        if not isinstance(item, dict):
            continue
        contact_id = int(item.get("id") or 0)
        if contact_id <= 0:
            continue
        ids.add(contact_id)
        labels[contact_id] = _person_label(
            item.get("family_name"),
            item.get("given_name"),
            f"contact {contact_id}",
        )
    return ids, labels


def refresh_share_roster_check() -> dict:
    ensure_google_sync_records()
    cfs_people = _local_cfs_people()
    google_people = None
    drive_ids = None
    drive_labels: dict[int, str] = {}
    recipient_maps = None
    access_token = ""
    account_email = ""
    try:
        access_token, account_email = _get_valid_access_token()
    except Exception:
        access_token, account_email = "", str((_load_google_account() or {}).get("account_email") or "")
    if access_token:
        try:
            google_people = _owner_google_people(access_token)
        except Exception:
            google_people = None
        try:
            drive_ids, drive_labels = _drive_roster_snapshot(access_token)
        except Exception:
            drive_ids, drive_labels = None, {}
    if account_email:
        share_result = fetch_share_roster_status(account_email)
        if share_result and share_result.get("ok") is not False:
            payload = share_result.get("result") if isinstance(share_result.get("result"), dict) else share_result
            recipients = payload.get("recipients") if isinstance(payload, dict) else None
            if isinstance(recipients, list):
                recipient_maps = {}
                for item in recipients:
                    if not isinstance(item, dict):
                        continue
                    email = str(item.get("email") or "").strip().lower()
                    if not email:
                        continue
                    recipient_maps[email] = {
                        str(person_id).strip()
                        for person_id in (item.get("owner_person_ids") or [])
                        if str(person_id).strip()
                    }
    report = build_roster_report(
        cfs_people=cfs_people,
        google_people=google_people,
        drive_ids=drive_ids,
        drive_labels=drive_labels,
        recipient_maps=recipient_maps,
    )
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE google_sync_state
            SET roster_check_json = ?, updated_at = ?
            WHERE id = 1
            """,
            (json.dumps(report, separators=(",", ":")), _now_text()),
        )
        conn.commit()
    return report
