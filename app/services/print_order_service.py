from __future__ import annotations

import json
import re
from typing import Any


def _clean(value: object) -> str:
    return str(value or "").strip()


def _sort_key(value: object) -> str:
    return _clean(value).lower()


def print_order_match_key(value: object) -> str:
    text = re.sub(r"\s*\([^)]*\)\s*$", "", _clean(value))
    text = re.sub(r"&", " and ", text.lower())
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def print_order_payload(raw_json: object) -> dict[str, Any]:
    try:
        payload = json.loads(str(raw_json or "") or "{}")
    except json.JSONDecodeError:
        return {"fields": []}
    return payload if isinstance(payload, dict) else {"fields": []}


def print_order_lookup(raw_json: object) -> tuple[dict[str, int], dict[tuple[str, str], int]]:
    payload = print_order_payload(raw_json)
    field_orders: dict[str, int] = {}
    meeting_orders: dict[tuple[str, str], int] = {}
    for field_index, field in enumerate(payload.get("fields") or [], start=1):
        if not isinstance(field, dict):
            continue
        field_name = _clean(field.get("name"))
        if not field_name:
            continue
        try:
            field_order = int(field.get("order") or field_index)
        except (TypeError, ValueError):
            field_order = field_index
        field_orders[print_order_match_key(field_name)] = field_order
        for meeting_index, meeting in enumerate(field.get("meetings") or [], start=1):
            if not isinstance(meeting, dict):
                continue
            meeting_name = _clean(meeting.get("name"))
            if not meeting_name:
                continue
            try:
                meeting_order = int(meeting.get("order") or meeting_index)
            except (TypeError, ValueError):
                meeting_order = meeting_index
            meeting_orders[(print_order_match_key(field_name), print_order_match_key(meeting_name))] = meeting_order
    return field_orders, meeting_orders


def order_field_names(field_names: list[str], raw_json: object) -> list[str]:
    field_orders, _meeting_orders = print_order_lookup(raw_json)
    return sorted(
        [_clean(name) for name in field_names if _clean(name)],
        key=lambda name: (field_orders.get(print_order_match_key(name), 1_000_000), _sort_key(name)),
    )


def order_meeting_names(meeting_names: list[str], raw_json: object, field_name: str = "") -> list[str]:
    _field_orders, meeting_orders = print_order_lookup(raw_json)
    cleaned_names = [_clean(name) for name in meeting_names if _clean(name)]
    scoped_field = _clean(field_name)
    if scoped_field:
        scoped_field_key = print_order_match_key(scoped_field)
        return sorted(
            cleaned_names,
            key=lambda name: (meeting_orders.get((scoped_field_key, print_order_match_key(name)), 1_000_000), _sort_key(name)),
        )
    return sorted(
        cleaned_names,
        key=lambda name: (
            min(
                (order for (_ordered_field, meeting), order in meeting_orders.items() if meeting == print_order_match_key(name)),
                default=1_000_000,
            ),
            _sort_key(name),
        ),
    )


def order_meetings_by_field(meetings_by_field: dict[str, list[str]], raw_json: object) -> dict[str, list[str]]:
    return {
        field_name: order_meeting_names(meetings, raw_json, field_name)
        for field_name, meetings in meetings_by_field.items()
    }
