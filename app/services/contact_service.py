from __future__ import annotations

import calendar
from collections import OrderedDict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from html import escape
import re
from urllib.parse import quote
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.database import fetch_all, fetch_one, get_connection
from app.services.address_format import enrich_address_parts
from app.services.google_sync_service import (
    get_current_editor_initials,
    mark_contacts_manifest_for_drive_export,
    mark_contact_for_drive_export,
    mark_meetingdata_for_drive_export,
    queue_contact_sync,
)
from app.services.print_order_service import order_field_names, order_meeting_names, order_meetings_by_field


CONTACT_ROW_COLOR_DEFAULTS = {
    "title_bar": "#e3eff3",
    "primary": "#f8f3e3",
    "secondary": "#edf5f7",
    "highlight": "#ffe3a1",
}

UNASSIGNED_FIELD_NAME = "No Field"
UNASSIGNED_MEETING_NAME = "No Meeting"
ASSIGNMENT_MENU_SENTINELS = frozenset({
    "__add_field__",
    "__delete_field__",
    "__rename_field__",
    "__add_meeting__",
    "__delete_meeting__",
    "__rename_meeting__",
})


EDIT_LOG_DISPLAY_TIMEZONE = ZoneInfo("America/Chicago")
EDIT_LOG_RETENTION_MONTHS = 6
LOCAL_CONTACT_PHOTO_PREFIX = "/static/uploads/contact_photos/"
LEGACY_SHARED_GROUP_NAME = "TriState Separates (Shared)"


def _log_contact_service(message: str) -> None:
    return None


MEETING_COLUMN_NAMES = [
    "col_d", "col_e", "col_f", "col_g", "col_h", "col_i", "col_j", "col_k",
    "col_l", "col_m", "col_n", "col_o", "col_p", "col_q", "col_r", "col_s",
    "col_t", "col_u", "col_v", "col_w", "col_x", "col_y", "col_z", "col_aa",
    "col_ab", "col_ac", "col_ad", "col_ae",
]


def get_dashboard_counts() -> dict:
    contact_count = fetch_one("SELECT COUNT(*) AS count FROM contacts")
    relationship_count = fetch_one("SELECT COUNT(*) AS count FROM relationships")
    meeting_row_count = fetch_one("SELECT COUNT(*) AS count FROM meeting_data_rows")
    return {
        "contacts": contact_count["count"] if contact_count else 0,
        "relationships": relationship_count["count"] if relationship_count else 0,
        "meeting_rows": meeting_row_count["count"] if meeting_row_count else 0,
    }


def _split_text_list(value: str | None) -> list[str]:
    if not value:
        return []
    text = str(value).replace("\r", "\n")
    parts = []
    for chunk in text.replace(";", "\n").split("\n"):
        cleaned = chunk.strip()
        if cleaned:
            parts.append(cleaned)
    return parts


def is_unassigned_field_name(value: object) -> bool:
    return _clean_text(value).casefold() == UNASSIGNED_FIELD_NAME.casefold()


def is_unassigned_meeting_name(value: object) -> bool:
    return _clean_text(value).casefold() == UNASSIGNED_MEETING_NAME.casefold()


def is_assignment_menu_sentinel(value: object) -> bool:
    return str(value or "").strip() in ASSIGNMENT_MENU_SENTINELS


def _without_assignment_menu_sentinels(names: list[str]) -> list[str]:
    return [name for name in names if not is_assignment_menu_sentinel(name)]


def _strip_trailing_parenthetical(value: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*$", "", str(value or "")).strip()


def build_contact_group_membership(field_value: str, meeting_value: str) -> str:
    field_name = str(field_value or "").strip()
    meeting_name = str(meeting_value or "").strip()
    if not field_name or not meeting_name:
        return ""
    from app.services.preset_service import get_shared_contacts_group_name

    shared_group_name = get_shared_contacts_group_name()
    return f"{field_name} - {meeting_name} (Shared):::{shared_group_name}:::myContacts"


def _map_url(address_text: str | None, coordinates: str | None) -> str:
    target = (coordinates or address_text or "").strip()
    if not target:
        return ""
    return f"http://maps.google.com/?q={quote(target, safe=',')}"


def _apple_map_url(address_text: str | None, coordinates: str | None) -> str:
    target = (coordinates or address_text or "").strip()
    if not target:
        return ""
    return f"http://maps.apple.com/?q={quote(target, safe=',')}"


def _looks_like_coordinates(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    match = re.search(r"(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)", text)
    if not match:
        return False
    try:
        latitude = float(match.group(1))
        longitude = float(match.group(2))
    except ValueError:
        return False
    return -90 <= latitude <= 90 and -180 <= longitude <= 180


def _is_coordinate_custom_field(field_type: object, field_value: object = "") -> bool:
    label = str(field_type or "").strip().lower()
    if not label:
        return False
    has_coordinate_label = any(
        token in label
        for token in ("coordinate", "coordinates", "coord", "gps", "lat", "lng", "longitude", "latitude")
    )
    return has_coordinate_label and _looks_like_coordinates(field_value)


def _filter_coordinate_custom_fields(rows: list[dict]) -> list[dict]:
    return [
        row
        for row in rows
        if not _is_coordinate_custom_field(row.get("field_type"), row.get("field_value"))
    ]


def _normalize_child_rows(table_name: str, contact_id: int, value_columns: tuple[str, ...]) -> list[dict]:
    rows = fetch_all(
        f"""
        SELECT *
        FROM {table_name}
        WHERE contact_id = ?
        ORDER BY position, id
        """,
        (contact_id,),
    )
    normalized = []
    for row in rows:
        item = dict(row)
        item["summary"] = " | ".join(
            str(item.get(col) or "").strip()
            for col in value_columns
            if str(item.get(col) or "").strip()
        )
        normalized.append(item)
    return normalized


def _collect_distinct_contact_tags(column_name: str) -> list[str]:
    rows = fetch_all(
        f"""
        SELECT {column_name} AS value
        FROM contacts
        WHERE TRIM(COALESCE({column_name}, '')) != ''
        ORDER BY {column_name}
        """
    )
    values = []
    seen = set()
    for row in rows:
        for part in _split_text_list(row["value"]):
            if part not in seen:
                seen.add(part)
                values.append(part)
    return values


def _collect_saved_assignment_options(kind: str) -> list[str]:
    rows = fetch_all(
        """
        SELECT value
        FROM contact_assignment_options
        WHERE kind = ? AND TRIM(COALESCE(value, '')) != ''
        ORDER BY value COLLATE NOCASE
        """,
        (str(kind or "").strip().lower(),),
    )
    return [str(row["value"] or "").strip() for row in rows if str(row["value"] or "").strip()]


def _reserved_assignment_option_error(kind: str, value: str) -> str | None:
    if kind == "field" and is_unassigned_field_name(value):
        return "No Field is reserved for contacts that do not have a Field."
    if kind == "meeting" and is_unassigned_meeting_name(value):
        return "No Meeting is reserved for contacts that do not have a Meeting."
    return None


def add_contact_assignment_option(kind: str, value: str, parent_value: str = "") -> str:
    normalized_kind = str(kind or "").strip().lower()
    normalized_value = str(value or "").strip()
    normalized_parent_value = str(parent_value or "").strip()
    if normalized_kind not in {"field", "meeting"}:
        raise ValueError("Invalid assignment option kind.")
    if not normalized_value:
        raise ValueError("Assignment option value is required.")
    if is_assignment_menu_sentinel(normalized_value):
        raise ValueError("That name is reserved for the field menu.")
    reserved_error = _reserved_assignment_option_error(normalized_kind, normalized_value)
    if reserved_error:
        raise ValueError(reserved_error)

    with get_connection() as conn:
        if normalized_kind == "meeting" and normalized_parent_value:
            conn.execute(
                """
                INSERT INTO contact_assignment_options (kind, value, parent_value)
                VALUES (?, ?, ?)
                ON CONFLICT(kind, value) DO UPDATE SET
                  parent_value = excluded.parent_value
                """,
                (normalized_kind, normalized_value, normalized_parent_value),
            )
        else:
            conn.execute(
                """
                INSERT OR IGNORE INTO contact_assignment_options (kind, value)
                VALUES (?, ?)
                """,
                (normalized_kind, normalized_value),
            )
        conn.commit()
    return normalized_value


def _count_contacts_using_assignment_option(kind: str, value: str) -> int:
    normalized_kind = str(kind or "").strip().lower()
    normalized_value = str(value or "").strip().lower()
    if normalized_kind not in {"field", "meeting"} or not normalized_value:
        return 0

    column_name = "fields_text" if normalized_kind == "field" else "meetings_text"
    rows = fetch_all(
        f"""
        SELECT {column_name} AS value
        FROM contacts
        WHERE TRIM(COALESCE({column_name}, '')) != ''
        """
    )
    count = 0
    for row in rows:
        values = {item.strip().lower() for item in _split_text_list(row["value"])}
        if normalized_value in values:
            count += 1
    return count


def _delete_empty_meetingdata_sections_for_assignment_option(conn, kind: str, value: str) -> int:
    normalized_kind = str(kind or "").strip().lower()
    normalized_value = str(value or "").strip()
    if normalized_kind not in {"field", "meeting"} or not normalized_value:
        return 0

    column_name = "field_name" if normalized_kind == "field" else "meeting_name"
    rows = conn.execute(
        f"""
        SELECT s.id
        FROM meeting_sections_v2 s
        LEFT JOIN meeting_section_rows_v2 r ON r.section_id = s.id
        LEFT JOIN meeting_row_cells_v2 c ON c.row_id = r.id
        WHERE s.{column_name} = ?
        GROUP BY s.id
        HAVING SUM(CASE WHEN TRIM(COALESCE(c.text_value, '')) != '' THEN 1 ELSE 0 END) = 0
        """,
        (normalized_value,),
    ).fetchall()
    from app.services.field_end_list_service import is_field_end_list

    section_ids = []
    for row in rows:
        section_id = int(row["id"])
        section = conn.execute(
            """
            SELECT field_name, meeting_name
            FROM meeting_sections_v2
            WHERE id = ?
            """,
            (section_id,),
        ).fetchone()
        if section and is_field_end_list(section["field_name"], section["meeting_name"], conn=conn):
            continue
        section_ids.append(section_id)
    for section_id in section_ids:
        conn.execute("DELETE FROM meeting_sections_v2 WHERE id = ?", (section_id,))

    remaining = conn.execute(
        f"""
        SELECT 1
        FROM meeting_sections_v2
        WHERE {column_name} = ?
        LIMIT 1
        """,
        (normalized_value,),
    ).fetchone()
    if not remaining:
        conn.execute(
            """
            DELETE FROM meeting_section_name_options
            WHERE kind = ? AND value = ?
            """,
            (normalized_kind, normalized_value),
        )
    return len(section_ids)


def _rename_meetingdata_sections_for_assignment_option(conn, kind: str, old_value: str, new_value: str) -> int:
    normalized_kind = str(kind or "").strip().lower()
    normalized_old_value = str(old_value or "").strip()
    normalized_new_value = str(new_value or "").strip()
    if normalized_kind not in {"field", "meeting"} or not normalized_old_value or not normalized_new_value:
        return 0

    column_name = "field_name" if normalized_kind == "field" else "meeting_name"
    sections = conn.execute(
        f"""
        SELECT id, field_name, meeting_name
        FROM meeting_sections_v2
        WHERE {column_name} = ?
        ORDER BY id
        """,
        (normalized_old_value,),
    ).fetchall()
    section_ids_to_delete: list[int] = []
    conflict_ids_to_delete: list[int] = []
    for row in sections:
        section_id = int(row["id"])
        field_name = normalized_new_value if normalized_kind == "field" else str(row["field_name"] or "")
        meeting_name = normalized_new_value if normalized_kind == "meeting" else str(row["meeting_name"] or "")
        conflict = conn.execute(
            """
            SELECT id
            FROM meeting_sections_v2
            WHERE field_name = ? AND meeting_name = ? AND id != ?
            LIMIT 1
            """,
            (field_name, meeting_name, section_id),
        ).fetchone()
        if conflict:
            conflict_id = int(conflict["id"])
            conflict_content = conn.execute(
                """
                SELECT SUM(CASE WHEN TRIM(COALESCE(c.text_value, '')) != '' THEN 1 ELSE 0 END) AS nonempty_cell_count
                FROM meeting_sections_v2 s
                LEFT JOIN meeting_section_rows_v2 r ON r.section_id = s.id
                LEFT JOIN meeting_row_cells_v2 c ON c.row_id = r.id
                WHERE s.id = ?
                """,
                (conflict_id,),
            ).fetchone()
            conflict_nonempty_cell_count = int((conflict_content or {})["nonempty_cell_count"] or 0)
            if conflict_nonempty_cell_count == 0:
                conflict_ids_to_delete.append(conflict_id)
                continue
            content = conn.execute(
                """
                SELECT COUNT(c.id) AS cell_count,
                       SUM(CASE WHEN TRIM(COALESCE(c.text_value, '')) != '' THEN 1 ELSE 0 END) AS nonempty_cell_count
                FROM meeting_sections_v2 s
                LEFT JOIN meeting_section_rows_v2 r ON r.section_id = s.id
                LEFT JOIN meeting_row_cells_v2 c ON c.row_id = r.id
                WHERE s.id = ?
                """,
                (section_id,),
            ).fetchone()
            nonempty_cell_count = int((content or {})["nonempty_cell_count"] or 0)
            if nonempty_cell_count == 0:
                section_ids_to_delete.append(section_id)
                continue
            raise ValueError("Rename would create a duplicate MeetingData Field/Meeting pair.")

    if not sections:
        return 0

    conn.execute(
        """
        INSERT OR IGNORE INTO meeting_section_name_options (kind, value)
        VALUES (?, ?)
        """,
        (normalized_kind, normalized_new_value),
    )
    for section_id in conflict_ids_to_delete:
        conn.execute("DELETE FROM meeting_sections_v2 WHERE id = ?", (section_id,))
    if section_ids_to_delete:
        conn.execute(
            f"""
            UPDATE meeting_sections_v2
            SET {column_name} = ?
            WHERE {column_name} = ?
              AND id NOT IN ({",".join("?" for _ in section_ids_to_delete)})
            """,
            (normalized_new_value, normalized_old_value, *section_ids_to_delete),
        )
    else:
        conn.execute(
            f"""
            UPDATE meeting_sections_v2
            SET {column_name} = ?
            WHERE {column_name} = ?
            """,
            (normalized_new_value, normalized_old_value),
        )
    for section_id in section_ids_to_delete:
        conn.execute("DELETE FROM meeting_sections_v2 WHERE id = ?", (section_id,))
    conn.execute(
        """
        DELETE FROM meeting_section_name_options
        WHERE kind = ? AND value = ?
        """,
        (normalized_kind, normalized_old_value),
    )
    return len(sections)


def delete_contact_assignment_option(kind: str, value: str) -> str:
    normalized_kind = str(kind or "").strip().lower()
    normalized_value = str(value or "").strip()
    if normalized_kind not in {"field", "meeting"}:
        raise ValueError("Invalid assignment option kind.")
    if not normalized_value:
        raise ValueError("Select a value to delete first.")
    reserved_error = _reserved_assignment_option_error(normalized_kind, normalized_value)
    if reserved_error:
        raise ValueError(reserved_error)

    in_use_count = _count_contacts_using_assignment_option(normalized_kind, normalized_value)
    if in_use_count > 0:
        label = "Field" if normalized_kind == "field" else "Meeting"
        raise ValueError(f"{label} groups must have no contacts before they can be deleted.")

    with get_connection() as conn:
        conn.execute(
            """
            DELETE FROM contact_assignment_options
            WHERE kind = ? AND value = ?
            """,
            (normalized_kind, normalized_value),
        )
        conn.commit()
    return normalized_value


def rename_contact_assignment_option(kind: str, old_value: str, new_value: str) -> dict:
    normalized_kind = str(kind or "").strip().lower()
    normalized_old_value = str(old_value or "").strip()
    normalized_new_value = str(new_value or "").strip()
    if normalized_kind not in {"field", "meeting"}:
        raise ValueError("Invalid assignment option kind.")
    if not normalized_old_value:
        raise ValueError("Select a value to rename first.")
    if not normalized_new_value:
        raise ValueError("Enter a new name first.")
    if normalized_old_value == normalized_new_value:
        raise ValueError("Choose a different new name.")
    if is_assignment_menu_sentinel(normalized_old_value) or is_assignment_menu_sentinel(normalized_new_value):
        raise ValueError("That name is reserved for the field menu.")
    reserved_error = _reserved_assignment_option_error(normalized_kind, normalized_old_value) or _reserved_assignment_option_error(normalized_kind, normalized_new_value)
    if reserved_error:
        raise ValueError(reserved_error)

    column_name = "fields_text" if normalized_kind == "field" else "meetings_text"
    affected_rows = fetch_all(
        f"""
        SELECT id, family_name, given_name, google_contact_id, fields_text, meetings_text, group_membership
        FROM contacts
        WHERE TRIM(COALESCE({column_name}, '')) != ''
        ORDER BY id
        """
    )

    queued_updates: list[dict] = []
    affected_count = 0
    removed_meetingdata_sections = 0
    renamed_meetingdata_sections = 0

    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO contact_assignment_options (kind, value)
            VALUES (?, ?)
            """,
            (normalized_kind, normalized_new_value),
        )
        renamed_meetingdata_sections = _rename_meetingdata_sections_for_assignment_option(
            conn,
            normalized_kind,
            normalized_old_value,
            normalized_new_value,
        )

        for row in affected_rows:
            item = dict(row)
            current_values = _split_text_list(item[column_name])
            next_values = [
                normalized_new_value if value.strip().lower() == normalized_old_value.lower() else value
                for value in current_values
            ]
            if next_values == current_values:
                continue

            affected_count += 1
            next_fields_text = "\n".join(next_values) if normalized_kind == "field" else str(item.get("fields_text") or "").strip()
            next_meetings_text = "\n".join(next_values) if normalized_kind == "meeting" else str(item.get("meetings_text") or "").strip()
            next_group_membership = build_contact_group_membership(next_fields_text, next_meetings_text)

            conn.execute(
                f"""
                UPDATE contacts
                SET
                  {column_name} = ?,
                  group_membership = ?,
                  shared_drive_revision = CASE
                    WHEN shared_drive_revision > 0 THEN shared_drive_revision + 1
                    ELSE 1
                  END,
                  shared_drive_needs_export = 1,
                  shared_drive_bootstrapped = 1
                WHERE id = ?
                """,
                ("\n".join(next_values), next_group_membership, int(item["id"])),
            )
            queued_updates.append(
                {
                    "contact_id": int(item["id"]),
                    "google_contact_id": str(item.get("google_contact_id") or ""),
                    "family_name": str(item.get("family_name") or ""),
                    "given_name": str(item.get("given_name") or ""),
                    "fields_text": next_fields_text,
                    "meetings_text": next_meetings_text,
                    "group_membership": next_group_membership,
                    "previous_fields_text": str(item.get("fields_text") or "").strip(),
                    "previous_meetings_text": str(item.get("meetings_text") or "").strip(),
                    "previous_group_membership": str(item.get("group_membership") or "").strip(),
                }
            )

        conn.execute(
            """
            DELETE FROM contact_assignment_options
            WHERE kind = ? AND value = ?
            """,
            (normalized_kind, normalized_old_value),
        )
        if normalized_kind == "field":
            conn.execute(
                """
                UPDATE contact_assignment_options
                SET parent_value = ?
                WHERE kind = 'meeting' AND parent_value = ?
                """,
                (normalized_new_value, normalized_old_value),
            )
        from app.services.meeting_toc_service import rename_unlisted_toc_assignment

        rename_unlisted_toc_assignment(normalized_kind, normalized_old_value, normalized_new_value, conn)
        from app.services.field_end_list_service import (
            rename_field_end_list_meeting,
            rename_field_end_lists_for_field,
        )

        if normalized_kind == "field":
            rename_field_end_lists_for_field(normalized_old_value, normalized_new_value, conn)
        else:
            rename_field_end_list_meeting(normalized_old_value, normalized_new_value, conn)
        removed_meetingdata_sections = _delete_empty_meetingdata_sections_for_assignment_option(
            conn,
            normalized_kind,
            normalized_old_value,
        )
        conn.commit()

    for item in queued_updates:
        queue_contact_sync(
            item["contact_id"],
            "update",
            google_contact_id=item["google_contact_id"],
            payload={
                "sync_action": "Move",
                "family_name": item["family_name"],
                "given_name": item["given_name"],
                "fields_text": item["fields_text"],
                "meetings_text": item["meetings_text"],
                "group_membership": item["group_membership"],
                "previous_fields_text": item["previous_fields_text"],
                "previous_meetings_text": item["previous_meetings_text"],
                "previous_group_membership": item["previous_group_membership"],
            },
        )

    if removed_meetingdata_sections or renamed_meetingdata_sections:
        mark_meetingdata_for_drive_export()

    from app.services.field_list_service import (
        remap_meeting_font_size_assignments,
        remap_meeting_line_height_assignments,
    )

    remap_meeting_line_height_assignments(normalized_kind, normalized_old_value, normalized_new_value)
    remap_meeting_font_size_assignments(normalized_kind, normalized_old_value, normalized_new_value)

    return {
        "old_value": normalized_old_value,
        "new_value": normalized_new_value,
        "affected_count": affected_count,
        "removed_meetingdata_sections": removed_meetingdata_sections,
        "renamed_meetingdata_sections": renamed_meetingdata_sections,
    }


def get_contact_filter_options() -> dict:
    settings_row = fetch_one("SELECT print_order_json FROM address_book_settings WHERE id = 1")
    print_order_json = str(settings_row["print_order_json"] or "") if settings_row else ""
    rows = fetch_all(
        """
        SELECT
          fields_text,
          meetings_text
        FROM contacts
        WHERE
          TRIM(COALESCE(fields_text, '')) != '' OR
          TRIM(COALESCE(meetings_text, '')) != ''
        ORDER BY family_name, given_name, id
        """
    )

    fields = []
    meetings = []
    field_seen = set()
    meeting_seen = set()
    meetings_by_field: dict[str, list[str]] = {}

    for row in rows:
        row_fields = _without_assignment_menu_sentinels(_split_text_list(row["fields_text"]))
        row_meetings = _without_assignment_menu_sentinels(_split_text_list(row["meetings_text"]))

        for field_name in row_fields:
            if field_name not in field_seen:
                field_seen.add(field_name)
                fields.append(field_name)

        for meeting_name in row_meetings:
            if meeting_name not in meeting_seen:
                meeting_seen.add(meeting_name)
                meetings.append(meeting_name)

        for field_name in row_fields:
            bucket = meetings_by_field.setdefault(field_name, [])
            for meeting_name in row_meetings:
                if meeting_name not in bucket:
                    bucket.append(meeting_name)

    for field_name in _collect_saved_assignment_options("field"):
        if is_assignment_menu_sentinel(field_name):
            continue
        if field_name not in field_seen:
            field_seen.add(field_name)
            fields.append(field_name)
        meetings_by_field.setdefault(field_name, meetings_by_field.get(field_name, []))

    saved_meeting_rows = fetch_all(
        """
        SELECT value, parent_value
        FROM contact_assignment_options
        WHERE kind = 'meeting' AND TRIM(COALESCE(value, '')) != ''
        ORDER BY value COLLATE NOCASE
        """
    )
    for row in saved_meeting_rows:
        meeting_name = str(row["value"] or "").strip()
        parent_field = str(row.get("parent_value") or "").strip()
        if not meeting_name or is_assignment_menu_sentinel(meeting_name):
            continue
        if is_assignment_menu_sentinel(parent_field):
            parent_field = ""
        if meeting_name not in meeting_seen:
            meeting_seen.add(meeting_name)
            meetings.append(meeting_name)
        if parent_field:
            bucket = meetings_by_field.setdefault(parent_field, [])
            if meeting_name not in bucket:
                bucket.append(meeting_name)

    unassigned_rows = fetch_all(
        """
        SELECT fields_text, meetings_text
        FROM contacts
        WHERE TRIM(COALESCE(fields_text, '')) = ''
           OR TRIM(COALESCE(meetings_text, '')) = ''
        """
    )
    has_unassigned_field = False
    has_unassigned_meeting = False
    for row in unassigned_rows:
        row_fields = _split_text_list(row["fields_text"])
        row_meetings = _split_text_list(row["meetings_text"])
        if not row_fields:
            has_unassigned_field = True
            bucket = meetings_by_field.setdefault(UNASSIGNED_FIELD_NAME, [])
            if row_meetings:
                for meeting_name in row_meetings:
                    if meeting_name not in meeting_seen:
                        meeting_seen.add(meeting_name)
                        meetings.append(meeting_name)
                    if meeting_name not in bucket:
                        bucket.append(meeting_name)
            else:
                has_unassigned_meeting = True
                if UNASSIGNED_MEETING_NAME not in bucket:
                    bucket.append(UNASSIGNED_MEETING_NAME)
        elif not row_meetings:
            has_unassigned_meeting = True
            for field_name in row_fields:
                bucket = meetings_by_field.setdefault(field_name, [])
                if UNASSIGNED_MEETING_NAME not in bucket:
                    bucket.append(UNASSIGNED_MEETING_NAME)

    if has_unassigned_field and UNASSIGNED_FIELD_NAME not in field_seen:
        field_seen.add(UNASSIGNED_FIELD_NAME)
        fields.append(UNASSIGNED_FIELD_NAME)
    if has_unassigned_meeting and UNASSIGNED_MEETING_NAME not in meeting_seen:
        meeting_seen.add(UNASSIGNED_MEETING_NAME)
        meetings.append(UNASSIGNED_MEETING_NAME)

    assigned_fields = [name for name in fields if not is_unassigned_field_name(name)]
    assigned_meetings = [name for name in meetings if not is_unassigned_meeting_name(name)]
    fields = order_field_names(assigned_fields, print_order_json)
    meetings = order_meeting_names(assigned_meetings, print_order_json)
    if UNASSIGNED_FIELD_NAME in field_seen:
        fields.append(UNASSIGNED_FIELD_NAME)
    if UNASSIGNED_MEETING_NAME in meeting_seen:
        meetings.append(UNASSIGNED_MEETING_NAME)
    meetings_by_field = order_meetings_by_field(meetings_by_field, print_order_json)
    for field_name, field_meetings in meetings_by_field.items():
        named = [name for name in field_meetings if not is_unassigned_meeting_name(name)]
        if any(is_unassigned_meeting_name(name) for name in field_meetings):
            meetings_by_field[field_name] = named + [UNASSIGNED_MEETING_NAME]
        else:
            meetings_by_field[field_name] = named

    return {
        "fields": fields,
        "meetings": meetings,
        "meetings_by_field": meetings_by_field,
        "states": list_contact_states(),
    }


def normalize_state_filters(values: list[str] | str | None) -> list[str]:
    if values is None:
        items: list[str] = []
    elif isinstance(values, str):
        items = [values]
    else:
        items = list(values)
    seen: set[str] = set()
    normalized: list[str] = []
    for item in items:
        for part in str(item or "").replace(";", ",").split(","):
            cleaned = _clean_text(part)
            key = cleaned.casefold()
            if not cleaned or key in seen:
                continue
            seen.add(key)
            normalized.append(cleaned)
    return normalized


def retain_known_state_filters(requested, available: list[str] | None) -> list[str]:
    """Drop state filter values that are no longer used on any contact."""
    known = {item.casefold() for item in (available or []) if str(item or "").strip()}
    return [item for item in normalize_state_filters(requested) if item.casefold() in known]


def list_contact_states() -> list[str]:
    rows = fetch_all(
        """
        SELECT TRIM(region) AS state
        FROM addresses
        WHERE TRIM(COALESCE(region, '')) != ''
        UNION
        SELECT TRIM(meets_in_state) AS state
        FROM contacts
        WHERE TRIM(COALESCE(meets_in_state, '')) != ''
        """
    )
    states = [_clean_text(row["state"]) for row in rows if _clean_text(row.get("state"))]
    return sorted(states, key=str.casefold)


def contact_matches_state_filters(
    meets_in_state: str | None,
    address_regions: list[str] | None,
    state_filters: list[str] | str | None,
) -> bool:
    selected = {item.casefold() for item in normalize_state_filters(state_filters)}
    if not selected:
        return True
    override = _clean_text(meets_in_state)
    if override:
        return override.casefold() in selected
    return any(_clean_text(region).casefold() in selected for region in (address_regions or []))


def _filter_contacts_by_states(contacts: list[dict], state_filters: list[str] | str | None) -> list[dict]:
    selected = normalize_state_filters(state_filters)
    if not selected or not contacts:
        return contacts
    contact_ids = [int(contact["id"]) for contact in contacts]
    placeholders = ",".join("?" for _ in contact_ids)
    rows = fetch_all(
        f"""
        SELECT contact_id, region
        FROM addresses
        WHERE contact_id IN ({placeholders})
          AND TRIM(COALESCE(region, '')) != ''
        """,
        tuple(contact_ids),
    )
    regions_by_contact: dict[int, list[str]] = {}
    for row in rows:
        regions_by_contact.setdefault(int(row["contact_id"]), []).append(str(row["region"] or ""))
    return [
        contact
        for contact in contacts
        if contact_matches_state_filters(
            contact.get("meets_in_state"),
            regions_by_contact.get(int(contact["id"]), []),
            selected,
        )
    ]


def get_contact_row_colors() -> dict:
    default_preset = fetch_one(
        """
        SELECT id
        FROM book_layout_presets
        WHERE is_default = 1
        ORDER BY id
        LIMIT 1
        """
    ) or fetch_one(
        """
        SELECT id
        FROM book_layout_presets
        ORDER BY id
        LIMIT 1
        """
    )
    if not default_preset:
        return dict(CONTACT_ROW_COLOR_DEFAULTS)

    preset = fetch_one(
        """
        SELECT
          contacts_title_bar_color,
          contacts_primary_row_color,
          contacts_secondary_row_color,
          contacts_highlight_row_color
        FROM book_layout_presets
        WHERE id = ?
        """,
        (int(default_preset["id"]),),
    )
    if not preset:
        return dict(CONTACT_ROW_COLOR_DEFAULTS)
    return {
        "title_bar": str(preset["contacts_title_bar_color"] or CONTACT_ROW_COLOR_DEFAULTS["title_bar"]),
        "primary": str(preset["contacts_primary_row_color"] or CONTACT_ROW_COLOR_DEFAULTS["primary"]),
        "secondary": str(preset["contacts_secondary_row_color"] or CONTACT_ROW_COLOR_DEFAULTS["secondary"]),
        "highlight": str(preset["contacts_highlight_row_color"] or CONTACT_ROW_COLOR_DEFAULTS["highlight"]),
    }


def list_contacts(
    search_text: str = "",
    field_filter: str = "",
    meeting_filter: str = "",
    state_filters: list[str] | str | None = None,
) -> list[dict]:
    try:
        search_text = (search_text or "").strip()
        field_filter = (field_filter or "").strip()
        meeting_filter = (meeting_filter or "").strip()
        unassigned_field = is_unassigned_field_name(field_filter)
        unassigned_meeting = is_unassigned_meeting_name(meeting_filter)
        sql_field_filter = "" if unassigned_field else field_filter
        sql_meeting_filter = "" if unassigned_meeting else meeting_filter
        selected_states = normalize_state_filters(state_filters)
        like = f"%{search_text}%"
        field_like = f"%{sql_field_filter}%"
        meeting_like = f"%{sql_meeting_filter}%"

        _log_contact_service("list_contacts main query start")
        rows = fetch_all(
            """
            SELECT
              c.id,
              c.family_name,
              c.given_name,
              c.photo,
              c.fields_text,
              c.meetings_text,
              c.mtg_home_elder_flag,
              c.meets_in_state,
              c.notes
            FROM contacts c
            WHERE
              (? = '' OR c.family_name LIKE ? OR c.given_name LIKE ?) AND
              (? = '' OR COALESCE(c.fields_text, '') LIKE ?) AND
              (? = '' OR COALESCE(c.meetings_text, '') LIKE ?)
            ORDER BY c.family_name, c.given_name
            """,
            (
                search_text,
                like,
                like,
                sql_field_filter,
                field_like,
                sql_meeting_filter,
                meeting_like,
            ),
        )

        normalized = [dict(row) for row in rows]
        if unassigned_field:
            normalized = [contact for contact in normalized if not _split_text_list(contact.get("fields_text"))]
        if unassigned_meeting:
            normalized = [contact for contact in normalized if not _split_text_list(contact.get("meetings_text"))]
        if selected_states:
            normalized = _filter_contacts_by_states(normalized, selected_states)
        if meeting_filter:
            has_meeting_home = any(_clean_text(contact.get("mtg_home_elder_flag")).startswith("1") for contact in normalized)

            def meeting_role_sort_key(contact: dict) -> tuple[int, str, str, int]:
                flag = _clean_text(contact.get("mtg_home_elder_flag"))
                if flag.startswith("1"):
                    rank = 0
                elif flag.startswith("2"):
                    rank = 1 if has_meeting_home else 0
                else:
                    rank = 2 if has_meeting_home else 1
                return (
                    rank,
                    _clean_text(contact.get("family_name")).lower(),
                    _clean_text(contact.get("given_name")).lower(),
                    int(contact.get("id") or 0),
                )

            normalized.sort(key=meeting_role_sort_key)
        _log_contact_service(f"list_contacts main query count={len(normalized)}")
        if not normalized:
            return normalized

        contact_ids = [int(contact["id"]) for contact in normalized]
        placeholders = ",".join("?" for _ in contact_ids)
        phone_rows = fetch_all(
            f"""
            SELECT contact_id, phone_type, phone_value
            FROM phones
            WHERE contact_id IN ({placeholders})
            ORDER BY contact_id, position, id
            """,
            tuple(contact_ids),
        )
        _log_contact_service(f"list_contacts phones count={len(phone_rows)}")
        address_rows = fetch_all(
            f"""
            SELECT contact_id, address_type, formatted_address
            FROM addresses
            WHERE contact_id IN ({placeholders})
            ORDER BY contact_id, position, id
            """,
            tuple(contact_ids),
        )
        _log_contact_service(f"list_contacts addresses count={len(address_rows)}")
        email_rows = fetch_all(
            f"""
            SELECT contact_id, email_type, email_value
            FROM emails
            WHERE contact_id IN ({placeholders})
            ORDER BY contact_id, position, id
            """,
            tuple(contact_ids),
        )
        _log_contact_service(f"list_contacts emails count={len(email_rows)}")

        phones_by_contact: dict[int, list[dict]] = {}
        for row in phone_rows:
            phones_by_contact.setdefault(int(row["contact_id"]), []).append(dict(row))

        addresses_by_contact: dict[int, list[dict]] = {}
        for row in address_rows:
            addresses_by_contact.setdefault(int(row["contact_id"]), []).append(dict(row))

        emails_by_contact: dict[int, list[dict]] = {}
        for row in email_rows:
            emails_by_contact.setdefault(int(row["contact_id"]), []).append(dict(row))

        for contact in normalized:
            contact_id = int(contact["id"])
            contact_phone_rows = phones_by_contact.get(contact_id, [])
            contact_address_rows = addresses_by_contact.get(contact_id, [])
            contact_email_rows = emails_by_contact.get(contact_id, [])

            contact["phone_summary"] = "\n".join(
                (
                    f"{item['phone_type']}  {item['phone_value']}"
                    if str(item.get("phone_type") or "").strip()
                    else str(item.get("phone_value") or "").strip()
                )
                for item in contact_phone_rows
                if str(item.get("phone_value") or "").strip()
            )
            contact["message_phones"] = [
                {
                    "type": str(item.get("phone_type") or "").strip(),
                    "value": str(item.get("phone_value") or "").strip(),
                }
                for item in contact_phone_rows
                if str(item.get("phone_value") or "").strip()
            ]
            contact["message_emails"] = [
                {
                    "type": str(item.get("email_type") or "").strip(),
                    "value": str(item.get("email_value") or "").strip(),
                }
                for item in contact_email_rows
                if str(item.get("email_value") or "").strip()
            ]
            contact["address_summary"] = "\n".join(
                (
                    f"{item['address_type']}: {item['formatted_address']}"
                    if str(item.get("address_type") or "").strip()
                    else str(item.get("formatted_address") or "").strip()
                )
                for item in contact_address_rows
                if str(item.get("formatted_address") or "").strip()
            )

        _log_contact_service("list_contacts complete")
        return normalized
    except BaseException as exc:
        _log_contact_service("list_contacts failed")
        _log_contact_service(f"{type(exc).__name__}: {exc}")
        raise


def get_contacts_by_ids(contact_ids: list[int]) -> list[dict]:
    ordered_ids: list[int] = []
    seen: set[int] = set()
    for contact_id in contact_ids:
        if contact_id in seen:
            continue
        seen.add(contact_id)
        ordered_ids.append(int(contact_id))
    if not ordered_ids:
        return []

    placeholders = ",".join("?" for _ in ordered_ids)
    rows = fetch_all(
        f"""
        SELECT
          id,
          family_name,
          given_name,
          fields_text,
          meetings_text
        FROM contacts
        WHERE id IN ({placeholders})
        """,
        tuple(ordered_ids),
    )
    rows_by_id = {int(row["id"]): dict(row) for row in rows}
    return [rows_by_id[contact_id] for contact_id in ordered_ids if contact_id in rows_by_id]


def list_contacts_for_field_list_print() -> list[dict]:
    """Batch-load contacts for Field List / Address Book print payloads.

    Avoids per-contact get_contact_detail() (N+1 child queries + photo URL work).
    """
    rows = fetch_all(
        """
        SELECT
          id,
          family_name,
          given_name,
          fields_text,
          meetings_text,
          notes,
          mtg_home_elder_flag,
          do_not_print
        FROM contacts
        ORDER BY family_name, given_name, id
        """
    )
    contacts = [dict(row) for row in rows]
    if not contacts:
        return []

    contact_ids = [int(contact["id"]) for contact in contacts]
    placeholders = ",".join("?" for _ in contact_ids)
    params = tuple(contact_ids)

    relationships_by_contact: dict[int, list[dict]] = {}
    for row in fetch_all(
        f"""
        SELECT contact_id, relation_type, relation_value
        FROM relationships
        WHERE contact_id IN ({placeholders})
        ORDER BY contact_id, position, id
        """,
        params,
    ):
        relationships_by_contact.setdefault(int(row["contact_id"]), []).append(dict(row))

    phones_by_contact: dict[int, list[dict]] = {}
    for row in fetch_all(
        f"""
        SELECT contact_id, phone_type, phone_value
        FROM phones
        WHERE contact_id IN ({placeholders})
        ORDER BY contact_id, position, id
        """,
        params,
    ):
        phones_by_contact.setdefault(int(row["contact_id"]), []).append(dict(row))

    addresses_by_contact: dict[int, list[dict]] = {}
    for row in fetch_all(
        f"""
        SELECT contact_id, address_type, formatted_address, coordinates
        FROM addresses
        WHERE contact_id IN ({placeholders})
        ORDER BY contact_id, position, id
        """,
        params,
    ):
        addresses_by_contact.setdefault(int(row["contact_id"]), []).append(dict(row))

    for contact in contacts:
        contact_id = int(contact["id"])
        contact["fields_list"] = _split_text_list(contact.get("fields_text"))
        contact["meetings_list"] = _split_text_list(contact.get("meetings_text"))
        contact["relationships"] = relationships_by_contact.get(contact_id, [])
        contact["phones"] = phones_by_contact.get(contact_id, [])
        contact["addresses"] = addresses_by_contact.get(contact_id, [])
    return contacts


def build_empty_contact() -> dict:
    return {
        "id": 0,
        "selected": "",
        "google_contact_id": "",
        "etag": "",
        "last_updated": "",
        "status": "",
        "do_not_print": 0,
        "fields_text": "",
        "meetings_text": "",
        "group_membership": "",
        "family_name": "",
        "given_name": "",
        "photo": "",
        "birthday": "",
        "notes": "",
        "mtg_home_elder_flag": "",
        "meets_in_state": "",
        "fields_list": [],
        "meetings_list": [],
        "group_membership_list": [],
        "relationships": [],
        "phones": [],
        "emails": [],
        "addresses": [],
        "custom_fields": [],
    }


def get_contact_detail(contact_id: int) -> dict | None:
    contact_row = fetch_one(
        """
        SELECT *
        FROM contacts
        WHERE id = ?
        """,
        (contact_id,),
    )
    if not contact_row:
        return None

    contact = dict(contact_row)
    contact["fields_list"] = _split_text_list(contact.get("fields_text"))
    contact["meetings_list"] = _split_text_list(contact.get("meetings_text"))
    normalized_group_membership = build_contact_group_membership(contact.get("fields_text"), contact.get("meetings_text"))
    if normalized_group_membership:
        contact["group_membership"] = normalized_group_membership
    contact["group_membership_list"] = _split_text_list(contact.get("group_membership"))

    contact["relationships"] = _normalize_child_rows(
        "relationships",
        contact_id,
        ("relation_type", "relation_value"),
    )
    contact["phones"] = _normalize_child_rows(
        "phones",
        contact_id,
        ("phone_type", "phone_value"),
    )
    contact["addresses"] = _normalize_child_rows(
        "addresses",
        contact_id,
        (
            "address_type",
            "street_address",
            "extended_address",
            "city",
            "region",
            "postal_code",
            "formatted_address",
            "coordinates",
        ),
    )
    for item in contact["addresses"]:
        enriched = enrich_address_parts(
            street_address=item.get("street_address"),
            extended_address=item.get("extended_address"),
            city=item.get("city"),
            region=item.get("region"),
            postal_code=item.get("postal_code"),
            formatted_address=item.get("formatted_address"),
        )
        item.update(enriched)
        item["map_url"] = _map_url(item.get("formatted_address"), item.get("coordinates"))
        item["apple_map_url"] = _apple_map_url(item.get("formatted_address"), item.get("coordinates"))
        item["formatted_lines"] = _split_text_list(item.get("formatted_address"))
    contact["emails"] = _normalize_child_rows(
        "emails",
        contact_id,
        ("email_type", "email_value"),
    )
    contact["custom_fields"] = _normalize_child_rows(
        "custom_fields",
        contact_id,
        ("field_type", "field_value"),
    )
    contact["custom_fields"] = _filter_coordinate_custom_fields(contact["custom_fields"])

    photo = str(contact.get("photo") or "")
    if photo:
        from app.services.google_sync_service import CONTACT_PHOTO_STATIC_PREFIX, google_photo_display_url

        if photo.startswith(CONTACT_PHOTO_STATIC_PREFIX):
            contact["photo"] = photo
            contact["photo_large"] = photo
        else:
            contact["photo"] = google_photo_display_url(photo, size=168)
            contact["photo_large"] = google_photo_display_url(photo, size=2048)
    else:
        contact["photo_large"] = ""

    return contact


def _assignment_tokens(value: object) -> set[str]:
    return {item.strip().lower() for item in _split_text_list(value) if str(item).strip()}


def is_deleted_assignment(fields_text: object = "", meetings_text: object = "") -> bool:
    return "deleted" in _assignment_tokens(fields_text) or "deleted" in _assignment_tokens(meetings_text)


def is_deleted_contact(contact: dict | None) -> bool:
    if not contact:
        return False
    return is_deleted_assignment(contact.get("fields_text"), contact.get("meetings_text"))


def is_moved_contact(contact: dict | None) -> bool:
    if not contact:
        return False
    meetings = _assignment_tokens(contact.get("meetings_text"))
    return "moved" in meetings and not is_deleted_contact(contact)


def _build_contact_update_payload(contact: dict) -> dict:
    return {
        "selected": str(contact.get("selected") or ""),
        "google_contact_id": str(contact.get("google_contact_id") or ""),
        "etag": str(contact.get("etag") or ""),
        "last_updated": str(contact.get("last_updated") or ""),
        "status": str(contact.get("status") or ""),
        "do_not_print": "1" if bool(contact.get("do_not_print") or 0) else "",
        "fields_text": str(contact.get("fields_text") or ""),
        "meetings_text": str(contact.get("meetings_text") or ""),
        "group_membership": str(contact.get("group_membership") or ""),
        "family_name": str(contact.get("family_name") or ""),
        "given_name": str(contact.get("given_name") or ""),
        "photo": str(contact.get("photo") or ""),
        "birthday": str(contact.get("birthday") or ""),
        "notes": str(contact.get("notes") or ""),
        "mtg_home_elder_flag": str(contact.get("mtg_home_elder_flag") or ""),
        "meets_in_state": str(contact.get("meets_in_state") or ""),
        "relationship_type": [str(item.get("relation_type") or "") for item in contact.get("relationships", [])],
        "relationship_value": [str(item.get("relation_value") or "") for item in contact.get("relationships", [])],
        "phone_type": [str(item.get("phone_type") or "") for item in contact.get("phones", [])],
        "phone_value": [str(item.get("phone_value") or "") for item in contact.get("phones", [])],
        "email_type": [str(item.get("email_type") or "") for item in contact.get("emails", [])],
        "email_value": [str(item.get("email_value") or "") for item in contact.get("emails", [])],
        "address_type": [str(item.get("address_type") or "") for item in contact.get("addresses", [])],
        "street_address": [str(item.get("street_address") or "") for item in contact.get("addresses", [])],
        "extended_address": [str(item.get("extended_address") or "") for item in contact.get("addresses", [])],
        "city": [str(item.get("city") or "") for item in contact.get("addresses", [])],
        "region": [str(item.get("region") or "") for item in contact.get("addresses", [])],
        "postal_code": [str(item.get("postal_code") or "") for item in contact.get("addresses", [])],
        "formatted_address": [str(item.get("formatted_address") or "") for item in contact.get("addresses", [])],
        "coordinates": [str(item.get("coordinates") or "") for item in contact.get("addresses", [])],
        "custom_field_type": [str(item.get("field_type") or "") for item in contact.get("custom_fields", [])],
        "custom_field_value": [str(item.get("field_value") or "") for item in contact.get("custom_fields", [])],
    }


def delete_contact(contact_id: int) -> bool:
    contact_exists = fetch_one(
        """
        SELECT id
        FROM contacts
        WHERE id = ?
        """,
        (contact_id,),
    )
    if not contact_exists:
        return False

    contact = get_contact_detail(contact_id)
    if not contact:
        return False

    deleted_field_value = "Deleted"
    deleted_meeting_value = str(datetime.now().year)
    updated = update_contact(
        contact_id,
        {
            **_build_contact_update_payload(contact),
            "selected": "",
            "status": "deleted",
            "fields_text": deleted_field_value,
            "meetings_text": deleted_meeting_value,
            "group_membership": build_contact_group_membership(deleted_field_value, deleted_meeting_value),
            "sync_action": "Delete",
        },
    )
    return updated


def delete_contacts(contact_ids: list[int]) -> int:
    deleted_count = 0
    seen: set[int] = set()
    for contact_id in contact_ids:
        if contact_id in seen:
            continue
        seen.add(contact_id)
        if delete_contact(contact_id):
            deleted_count += 1
    return deleted_count


def permanently_delete_contact(contact_id: int) -> bool:
    contact = get_contact_detail(contact_id)
    if not contact or not is_deleted_contact(contact):
        return False

    google_contact_id = str(contact.get("google_contact_id") or "").strip()
    group_names = [
        item.strip()
        for item in str(contact.get("group_membership") or "").split(":::")
        if item.strip()
    ]
    field_name = str(contact.get("fields_text") or "").strip()
    meeting_name = str(contact.get("meetings_text") or "").strip()
    if field_name and meeting_name:
        from app.services.preset_service import get_shared_contacts_group_name

        group_names.append(f"{field_name} - {meeting_name} (Shared)")
        group_names.append(get_shared_contacts_group_name())
    group_names.append("myContacts")
    deduped_group_names = []
    seen_group_names = set()
    for group_name in group_names:
        if group_name in seen_group_names:
            continue
        seen_group_names.add(group_name)
        deduped_group_names.append(group_name)
    if google_contact_id:
        queue_contact_sync(
            contact_id,
            "delete",
            google_contact_id=google_contact_id,
            payload={
                "sync_action": "Permanently delete",
                "family_name": str(contact.get("family_name") or ""),
                "given_name": str(contact.get("given_name") or ""),
                "google_contact_id": google_contact_id,
                "group_names": deduped_group_names,
            },
        )

    contact_name = (
        _format_contact_name(contact.get("family_name"), contact.get("given_name"))
        or f"Contact {contact_id}"
    )
    assignment = " - ".join(part for part in (field_name, meeting_name) if part) or "Deleted"
    editor_initials = get_current_editor_initials() or "SE"
    with get_connection() as conn:
        _store_contact_edit_entries(
            conn,
            contact_id,
            contact_name,
            editor_initials,
            [
                {
                    "label_name": "Contact",
                    "original_text": assignment,
                    "edited_text": "Permanently deleted",
                }
            ],
        )
        conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
        conn.commit()
    mark_contacts_manifest_for_drive_export()
    return True


def permanently_delete_contacts(contact_ids: list[int]) -> int:
    deleted_count = 0
    seen: set[int] = set()
    for contact_id in contact_ids:
        if contact_id in seen:
            continue
        seen.add(contact_id)
        if permanently_delete_contact(contact_id):
            deleted_count += 1
    return deleted_count


def move_contacts(contact_ids: list[int], field_value: str, meeting_value: str) -> int:
    moved_count = 0
    for contact in get_contacts_by_ids(contact_ids):
        contact_id = int(contact["id"])
        detail = get_contact_detail(contact_id)
        if not detail:
            continue

        next_status = str(detail.get("status") or "")
        next_field = str(field_value or "").strip()
        next_meeting = str(meeting_value or "").strip()
        if is_deleted_assignment(next_field, next_meeting):
            next_status = "deleted"
        elif next_status.strip().lower() == "deleted":
            next_status = "updated"

        moved = update_contact(
            contact_id,
            {
                **_build_contact_update_payload(detail),
                "status": next_status,
                "fields_text": next_field,
                "meetings_text": next_meeting,
                "group_membership": (
                    build_contact_group_membership(next_field, next_meeting)
                ),
                "sync_action": (
                    "Restore"
                    if is_deleted_assignment(detail.get("fields_text"), detail.get("meetings_text"))
                    and not is_deleted_assignment(next_field, next_meeting)
                    else "Move"
                ),
            },
        )
        if moved:
            moved_count += 1
    return moved_count


def _clean_text(value: str | None) -> str:
    return str(value or "").strip()


def _is_local_contact_photo(photo_url: str | None) -> bool:
    return _clean_text(photo_url).startswith(LOCAL_CONTACT_PHOTO_PREFIX)


def _next_photo_sync_fields(existing_contact: dict | None, next_photo: str) -> dict:
    existing = existing_contact or {}
    current_photo = _clean_text(existing.get("photo"))
    current_drive_file_id = _clean_text(existing.get("photo_drive_file_id"))
    current_revision = int(existing.get("photo_sync_revision") or 0)
    next_photo_clean = _clean_text(next_photo)
    if next_photo_clean == current_photo:
        return {
            "photo_drive_file_id": current_drive_file_id,
            "photo_sync_revision": current_revision,
            "photo_needs_export": int(existing.get("photo_needs_export") or 0),
        }
    return {
        "photo_drive_file_id": current_drive_file_id,
        "photo_sync_revision": current_revision + 1,
        "photo_needs_export": 1,
    }


def _next_shared_drive_sync_fields(existing_contact: dict | None) -> dict:
    existing = existing_contact or {}
    current_revision = int(existing.get("shared_drive_revision") or 0)
    return {
        "shared_drive_revision": max(current_revision, 0) + 1,
        "shared_drive_needs_export": 1,
        "shared_drive_bootstrapped": 1,
    }


def _clean_compare_text(value: str | None) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in text.strip().split("\n")]
    return "\n".join(lines)


def _normalize_repeatable_rows(*columns: list[str]) -> list[tuple[str, ...]]:
    max_len = max((len(column) for column in columns), default=0)
    rows: list[tuple[str, ...]] = []
    for index in range(max_len):
        values = tuple(_clean_text(column[index]) if index < len(column) else "" for column in columns)
        if any(values):
            rows.append(values)
    return rows




def _normalize_address_rows(payload: dict) -> list[tuple[str, ...]]:
    raw_rows = _normalize_repeatable_rows(
        payload.get("address_type", []),
        payload.get("street_address", []),
        payload.get("extended_address", []),
        payload.get("city", []),
        payload.get("region", []),
        payload.get("postal_code", []),
        payload.get("coordinates", []),
        payload.get("formatted_address", []),
    )
    rows: list[tuple[str, ...]] = []
    for (
        address_type,
        street_address,
        extended_address,
        city,
        region,
        postal_code,
        coordinates,
        formatted_address,
    ) in raw_rows:
        enriched = enrich_address_parts(
            street_address=street_address,
            extended_address=extended_address,
            city=city,
            region=region,
            postal_code=postal_code,
            formatted_address=formatted_address,
        )
        if not any(
            (
                address_type,
                enriched["street_address"],
                enriched["extended_address"],
                enriched["city"],
                enriched["region"],
                enriched["postal_code"],
                enriched["formatted_address"],
                coordinates,
            )
        ):
            continue
        rows.append(
            (
                address_type,
                enriched["street_address"],
                enriched["extended_address"],
                enriched["city"],
                enriched["region"],
                enriched["postal_code"],
                enriched["formatted_address"],
                coordinates,
            )
        )
    return rows


def _address_insert_values(row: tuple[str, ...]) -> tuple:
    (
        address_type,
        street_address,
        extended_address,
        city,
        region,
        postal_code,
        formatted_address,
        coordinates,
    ) = row
    return (
        address_type,
        street_address,
        extended_address,
        city,
        region,
        postal_code,
        formatted_address,
        coordinates,
    )

def _format_contact_name(family_name: str | None, given_name: str | None) -> str:
    family = _clean_text(family_name)
    given = _clean_text(given_name)
    if family and given:
        return f"{family}, {given}"
    return family or given


def build_contact_id_lookup_by_formatted_name() -> dict[str, int]:
    lookup: dict[str, int] = {}
    rows = fetch_all(
        """
        SELECT id, family_name, given_name
        FROM contacts
        ORDER BY id
        """
    )
    for row in rows:
        name = _format_contact_name(row.get("family_name"), row.get("given_name"))
        key = " ".join(name.strip().lower().split())
        if key and key not in lookup:
            lookup[key] = int(row["id"])
    return lookup


def _append_contact_edit_entry(entries: list[dict], label_name: str, original_text: str | None, edited_text: str | None) -> None:
    original_clean = _clean_text(original_text)
    edited_clean = _clean_text(edited_text)
    if _clean_compare_text(original_clean) == _clean_compare_text(edited_clean):
        return
    entries.append(
        {
            "label_name": label_name,
            "original_text": original_clean,
            "edited_text": edited_clean,
        }
    )


def _collect_repeatable_edit_entries(
    entries: list[dict],
    existing_rows: list[dict],
    next_rows: list[dict],
    fields: list[tuple[str, str]],
) -> None:
    row_count = max(len(existing_rows), len(next_rows))
    for row_index in range(row_count):
        previous_row = existing_rows[row_index] if row_index < len(existing_rows) else {}
        next_row = next_rows[row_index] if row_index < len(next_rows) else {}
        for key, label in fields:
            numbered_label = f"{label} {row_index + 1}" if row_count > 1 else label
            _append_contact_edit_entry(entries, numbered_label, previous_row.get(key), next_row.get(key))


def _collect_contact_edit_entries(
    existing_contact: dict,
    core_fields: dict,
    relationship_rows: list[tuple[str, ...]],
    phone_rows: list[tuple[str, ...]],
    email_rows: list[tuple[str, ...]],
    address_rows: list[tuple[str, ...]],
    custom_field_rows: list[tuple[str, ...]],
) -> list[dict]:
    entries: list[dict] = []

    for label, existing_value, next_value in (
        ("Family name", existing_contact.get("family_name"), core_fields["family_name"]),
        ("Given name", existing_contact.get("given_name"), core_fields["given_name"]),
        ("Photo Location", existing_contact.get("photo"), core_fields["photo"]),
        ("Birthday", existing_contact.get("birthday"), core_fields["birthday"]),
        ("Do not print", "1" if bool(existing_contact.get("do_not_print") or 0) else "", "1" if core_fields["do_not_print"] else ""),
        ("1 - Meeting home, 2 - Elder", existing_contact.get("mtg_home_elder_flag"), core_fields["mtg_home_elder_flag"]),
        ("Meets in different state", existing_contact.get("meets_in_state"), core_fields["meets_in_state"]),
        ("Fields", existing_contact.get("fields_text"), core_fields["fields_text"]),
        ("Meetings", existing_contact.get("meetings_text"), core_fields["meetings_text"]),
        ("Notes", existing_contact.get("notes"), core_fields["notes"]),
    ):
        _append_contact_edit_entry(entries, label, existing_value, next_value)

    existing_emails = [dict(item) for item in existing_contact.get("emails", [])]
    next_emails = [{"email_type": email_type, "email_value": email_value} for email_type, email_value in email_rows]
    _collect_repeatable_edit_entries(entries, existing_emails, next_emails, [
        ("email_value", "Email"),
        ("email_type", "Email Label"),
    ])

    existing_phones = [dict(item) for item in existing_contact.get("phones", [])]
    next_phones = [{"phone_type": phone_type, "phone_value": phone_value} for phone_type, phone_value in phone_rows]
    _collect_repeatable_edit_entries(entries, existing_phones, next_phones, [
        ("phone_value", "Phone"),
        ("phone_type", "Phone Label"),
    ])

    existing_addresses = [dict(item) for item in existing_contact.get("addresses", [])]
    next_addresses = [
        {
            "address_type": address_type,
            "street_address": street_address,
            "extended_address": extended_address,
            "city": city,
            "region": region,
            "postal_code": postal_code,
            "coordinates": coordinates,
            "formatted_address": formatted_address,
        }
        for (
            address_type,
            street_address,
            extended_address,
            city,
            region,
            postal_code,
            formatted_address,
            coordinates,
        ) in address_rows
    ]
    _collect_repeatable_edit_entries(entries, existing_addresses, next_addresses, [
        ("address_type", "Address Label"),
        ("street_address", "Street address"),
        ("extended_address", "Street address 2"),
        ("city", "City"),
        ("region", "State"),
        ("postal_code", "ZIP"),
        ("coordinates", "Coordinates"),
        ("formatted_address", "Formatted address"),
    ])

    existing_relationships = [dict(item) for item in existing_contact.get("relationships", [])]
    next_relationships = [
        {
            "relation_type": relation_type,
            "relation_value": relation_value,
        }
        for relation_type, relation_value in relationship_rows
    ]
    _collect_repeatable_edit_entries(entries, existing_relationships, next_relationships, [
        ("relation_value", "Relationship Name"),
        ("relation_type", "Relationship Type"),
    ])

    existing_custom_fields = [dict(item) for item in existing_contact.get("custom_fields", [])]
    next_custom_fields = [
        {
            "field_type": field_type,
            "field_value": field_value,
        }
        for field_type, field_value in custom_field_rows
    ]
    _collect_repeatable_edit_entries(entries, existing_custom_fields, next_custom_fields, [
        ("field_type", "Custom Field"),
        ("field_value", "Custom Field Value"),
    ])

    return entries


def _store_contact_edit_entries(
    conn,
    contact_id: int,
    contact_name: str,
    editor_initials: str,
    entries: list[dict],
) -> None:
    if not entries or not editor_initials:
        return

    batch_id = uuid4().hex
    batch_created_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    for batch_row_index, entry in enumerate(entries, start=1):
        conn.execute(
            """
            INSERT INTO contact_edit_log_entries (
              batch_id,
              batch_created_at,
              batch_row_index,
              editor_initials,
              contact_id,
              contact_name,
              label_name,
              original_text,
              edited_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                batch_created_at,
                batch_row_index,
                editor_initials,
                contact_id,
                contact_name,
                entry["label_name"],
                entry["original_text"],
                entry["edited_text"],
            ),
        )
    _prune_old_contact_edit_log_entries(conn)


def _edit_log_retention_cutoff() -> str:
    now = datetime.now(timezone.utc)
    month = now.month - EDIT_LOG_RETENTION_MONTHS
    year = now.year
    while month <= 0:
        month += 12
        year -= 1
    day = min(now.day, calendar.monthrange(year, month)[1])
    return now.replace(year=year, month=month, day=day).isoformat(timespec="microseconds")


def _prune_old_contact_edit_log_entries(conn) -> int:
    cutoff = _edit_log_retention_cutoff()
    affected_rows = conn.execute(
        """
        SELECT DISTINCT contact_id
        FROM contact_edit_log_entries
        WHERE batch_created_at < ?
        """,
        (cutoff,),
    ).fetchall()
    affected_contact_ids = [int(row["contact_id"]) for row in affected_rows if int(row["contact_id"] or 0) > 0]
    if not affected_contact_ids:
        return 0

    result = conn.execute(
        """
        DELETE FROM contact_edit_log_entries
        WHERE batch_created_at < ?
        """,
        (cutoff,),
    )
    placeholders = ",".join("?" for _ in affected_contact_ids)
    conn.execute(
        f"""
        UPDATE contacts
        SET shared_drive_needs_export = 1
        WHERE id IN ({placeholders})
        """,
        tuple(affected_contact_ids),
    )
    return int(result.rowcount or 0)


def list_contact_edit_batches() -> list[dict]:
    _log_contact_service("list contact edit batches start")
    with get_connection() as conn:
        pruned_count = _prune_old_contact_edit_log_entries(conn)
        rows = conn.execute(
            """
            SELECT
              batch_id,
              batch_created_at,
              batch_row_index,
              editor_initials,
              contact_id,
              contact_name,
              label_name,
              original_text,
              edited_text
            FROM contact_edit_log_entries
            ORDER BY batch_created_at DESC, batch_row_index ASC, id ASC
            """
        ).fetchall()
        if pruned_count:
            conn.commit()
    _log_contact_service(f"list contact edit batches rows count={len(rows)}")

    batches: list[dict] = []
    batch_lookup: dict[str, dict] = {}
    for row in rows:
        batch_id = str(row["batch_id"])
        batch = batch_lookup.get(batch_id)
        if batch is None:
            batch = {
                "batch_id": batch_id,
                "batch_created_at": str(row["batch_created_at"] or ""),
                "batch_created_at_display": _format_edit_batch_timestamp(row["batch_created_at"]),
                "editor_initials": str(row["editor_initials"] or ""),
                "contact_id": int(row["contact_id"]),
                "contact_name": str(row["contact_name"] or ""),
                "entries": [],
            }
            batches.append(batch)
            batch_lookup[batch_id] = batch

        batch["entries"].append(
            {
                "label_name": str(row["label_name"] or ""),
                "original_text": str(row["original_text"] or "") if str(row["original_text"] or "").strip() else "blank",
                "edited_text_html": _build_contact_edit_highlight_html(row["original_text"], row["edited_text"]),
            }
        )

    for batch in batches:
        batch["row_count"] = len(batch["entries"])
    _log_contact_service(f"list contact edit batches complete batches={len(batches)}")
    return batches


def _format_edit_batch_timestamp(value: object) -> str:
    timestamp_text = str(value or "").strip()
    if not timestamp_text:
        return ""

    normalized_value = timestamp_text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized_value)
    except ValueError:
        return timestamp_text

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    localized = parsed.astimezone(EDIT_LOG_DISPLAY_TIMEZONE)

    month = localized.month
    day = localized.day
    year = localized.year
    hour = localized.hour % 12 or 12
    minute = localized.minute
    meridiem = "AM" if localized.hour < 12 else "PM"
    return f"{month}/{day}/{year}, {hour}:{minute:02d} {meridiem}"


def _highlight_text_html(value: str) -> str:
    return escape(value).replace("\n", "<br>")


def _tokenize_edit_text(value: str) -> list[str]:
    return [token for token in re.split(r"(\s+|[^\w\s]+)", str(value or "")) if token]


def _highlight_token_segment(tokens: list[str]) -> str:
    if not tokens:
        return ""
    raw_text = "".join(tokens)
    if not raw_text:
        return ""
    return f'<mark class="meeting-edit-highlight">{_highlight_text_html(raw_text)}</mark>'


def _deleted_token_marker() -> str:
    return '<mark class="meeting-edit-highlight">[deleted]</mark>'


def _build_single_line_edit_highlight_html(original: str, edited: str) -> str:
    if original == edited:
        return _highlight_text_html(edited)

    original_tokens = _tokenize_edit_text(original)
    edited_tokens = _tokenize_edit_text(edited)
    matcher = SequenceMatcher(a=original_tokens, b=edited_tokens, autojunk=False)
    parts: list[str] = []
    changed = False
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        segment_tokens = edited_tokens[j1:j2]
        if tag == "equal":
            parts.append(_highlight_text_html("".join(segment_tokens)))
            continue
        if segment_tokens:
            parts.append(_highlight_token_segment(segment_tokens))
            changed = True
            continue
        if tag == "delete":
            parts.append(_deleted_token_marker())
            changed = True

    if not changed:
        return _highlight_text_html(edited)
    return "".join(parts)


def _build_contact_edit_highlight_html(original_text: object, edited_text: object) -> str:
    original = str(original_text or "")
    edited = str(edited_text or "")
    if not edited.strip():
        return '<mark class="meeting-edit-highlight">blank</mark>'
    if original == edited:
        return _highlight_text_html(edited)

    original_lines = original.splitlines()
    edited_lines = edited.splitlines()
    if len(original_lines) > 1 or len(edited_lines) > 1:
        line_count = max(len(original_lines), len(edited_lines))
        rendered_lines: list[str] = []
        changed = False
        for index in range(line_count):
            current_original = original_lines[index] if index < len(original_lines) else ""
            current_edited = edited_lines[index] if index < len(edited_lines) else ""
            rendered_lines.append(_build_single_line_edit_highlight_html(current_original, current_edited))
            if current_original != current_edited:
                changed = True
        if not changed:
            return _highlight_text_html(edited)
        return "<br>".join(rendered_lines)

    return _build_single_line_edit_highlight_html(original, edited)


def get_latest_contact_edit_log_entries(contact_id: int) -> list[dict]:
    rows = fetch_all(
        """
        SELECT label_name, original_text, edited_text
        FROM contact_edit_log_entries
        WHERE contact_id = ?
          AND batch_id = (
            SELECT batch_id
            FROM contact_edit_log_entries
            WHERE contact_id = ?
            ORDER BY batch_created_at DESC, id DESC
            LIMIT 1
          )
        ORDER BY batch_row_index ASC, id ASC
        """,
        (int(contact_id), int(contact_id)),
    )
    return [
        {
            "label_name": str(row["label_name"] or ""),
            "original_text": str(row["original_text"] or ""),
            "edited_text": str(row["edited_text"] or ""),
        }
        for row in rows
    ]


def update_contact(contact_id: int, payload: dict, *, queue_google_sync: bool = True) -> bool:
    existing_contact = get_contact_detail(contact_id)
    if not existing_contact:
        return False

    core_fields = {
        "selected": _clean_text(payload.get("selected")),
        "google_contact_id": _clean_text(payload.get("google_contact_id")),
        "etag": _clean_text(payload.get("etag")),
        "last_updated": _clean_text(payload.get("last_updated")),
        "status": _clean_text(payload.get("status")),
        "do_not_print": 1 if str(payload.get("do_not_print") or "").lower() in {"1", "true", "yes", "on"} else 0,
        "fields_text": _clean_text(payload.get("fields_text")),
        "meetings_text": _clean_text(payload.get("meetings_text")),
        "group_membership": "",
        "family_name": _clean_text(payload.get("family_name")),
        "given_name": _clean_text(payload.get("given_name")),
        "photo": _clean_text(payload.get("photo")),
        "birthday": _clean_text(payload.get("birthday")),
        "notes": _clean_text(payload.get("notes")),
        "mtg_home_elder_flag": _clean_text(payload.get("mtg_home_elder_flag")),
        "meets_in_state": (
            _clean_text(payload.get("meets_in_state"))
            if "meets_in_state" in payload
            else _clean_text(existing_contact.get("meets_in_state"))
        ),
    }
    core_fields["group_membership"] = build_contact_group_membership(core_fields["fields_text"], core_fields["meetings_text"])
    photo_sync_fields = _next_photo_sync_fields(existing_contact, core_fields["photo"])
    shared_drive_sync_fields = _next_shared_drive_sync_fields(existing_contact)
    editor_initials = get_current_editor_initials()
    google_contact_id = core_fields["google_contact_id"]
    existing_deleted = is_deleted_assignment(
        existing_contact.get("fields_text"), existing_contact.get("meetings_text")
    )
    next_deleted = is_deleted_assignment(core_fields["fields_text"], core_fields["meetings_text"])
    action_hint = _clean_text(payload.get("sync_action"))
    if not action_hint:
        if not existing_contact.get("google_contact_id"):
            action_hint = "Create"
        elif existing_deleted and not next_deleted:
            action_hint = "Restore"
        elif next_deleted:
            action_hint = "Delete"
        elif (
            _clean_text(existing_contact.get("fields_text")) != core_fields["fields_text"] or
            _clean_text(existing_contact.get("meetings_text")) != core_fields["meetings_text"]
        ):
            action_hint = "Move"
        else:
            action_hint = "Update"

    relationship_rows = _normalize_repeatable_rows(
        payload.get("relationship_type", []),
        payload.get("relationship_value", []),
    )
    phone_rows = _normalize_repeatable_rows(
        payload.get("phone_type", []),
        payload.get("phone_value", []),
    )
    email_rows = _normalize_repeatable_rows(
        payload.get("email_type", []),
        payload.get("email_value", []),
    )
    address_rows = _normalize_address_rows(payload)
    custom_field_rows = _normalize_repeatable_rows(
        payload.get("custom_field_type", []),
        payload.get("custom_field_value", []),
    )
    custom_field_rows = [
        row
        for row in custom_field_rows
        if not _is_coordinate_custom_field(row[0], row[1])
    ]
    contact_edit_entries = _collect_contact_edit_entries(
        existing_contact,
        core_fields,
        relationship_rows,
        phone_rows,
        email_rows,
        address_rows,
        custom_field_rows,
    )
    contact_name = _format_contact_name(core_fields["family_name"], core_fields["given_name"])
    if not contact_name:
        contact_name = _format_contact_name(existing_contact.get("family_name"), existing_contact.get("given_name"))

    with get_connection() as conn:
        conn.execute(
            """
            UPDATE contacts
            SET
              selected = ?,
              google_contact_id = ?,
              etag = ?,
              last_updated = ?,
              status = ?,
              do_not_print = ?,
              fields_text = ?,
              meetings_text = ?,
              group_membership = ?,
              family_name = ?,
              given_name = ?,
              photo = ?,
              photo_drive_file_id = ?,
              photo_sync_revision = ?,
              photo_needs_export = ?,
              shared_drive_revision = ?,
              shared_drive_needs_export = ?,
              shared_drive_bootstrapped = ?,
              birthday = ?,
              notes = ?,
              mtg_home_elder_flag = ?,
              meets_in_state = ?
            WHERE id = ?
            """,
            (
                core_fields["selected"],
                core_fields["google_contact_id"],
                core_fields["etag"],
                core_fields["last_updated"],
                core_fields["status"],
                core_fields["do_not_print"],
                core_fields["fields_text"],
                core_fields["meetings_text"],
                core_fields["group_membership"],
                core_fields["family_name"],
                core_fields["given_name"],
                core_fields["photo"],
                photo_sync_fields["photo_drive_file_id"],
                photo_sync_fields["photo_sync_revision"],
                photo_sync_fields["photo_needs_export"],
                shared_drive_sync_fields["shared_drive_revision"],
                shared_drive_sync_fields["shared_drive_needs_export"],
                shared_drive_sync_fields["shared_drive_bootstrapped"],
                core_fields["birthday"],
                core_fields["notes"],
                core_fields["mtg_home_elder_flag"],
                core_fields["meets_in_state"],
                contact_id,
            ),
        )

        for table_name in ("relationships", "phones", "emails", "addresses", "custom_fields"):
            conn.execute(f"DELETE FROM {table_name} WHERE contact_id = ?", (contact_id,))

        for position, (relation_type, relation_value) in enumerate(relationship_rows, start=1):
            conn.execute(
                """
                INSERT INTO relationships (contact_id, position, relation_type, relation_value)
                VALUES (?, ?, ?, ?)
                """,
                (contact_id, position, relation_type, relation_value),
            )

        for position, (phone_type, phone_value) in enumerate(phone_rows, start=1):
            conn.execute(
                """
                INSERT INTO phones (contact_id, position, phone_type, phone_value)
                VALUES (?, ?, ?, ?)
                """,
                (contact_id, position, phone_type, phone_value),
            )

        for position, (email_type, email_value) in enumerate(email_rows, start=1):
            conn.execute(
                """
                INSERT INTO emails (contact_id, position, email_type, email_value)
                VALUES (?, ?, ?, ?)
                """,
                (contact_id, position, email_type, email_value),
            )

        for position, address_row in enumerate(address_rows, start=1):
            (
                address_type,
                street_address,
                extended_address,
                city,
                region,
                postal_code,
                formatted_address,
                coordinates,
            ) = _address_insert_values(address_row)
            conn.execute(
                """
                INSERT INTO addresses (
                  contact_id, position, address_type,
                  street_address, extended_address, city, region, postal_code,
                  formatted_address, coordinates
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    contact_id,
                    position,
                    address_type,
                    street_address,
                    extended_address,
                    city,
                    region,
                    postal_code,
                    formatted_address,
                    coordinates,
                ),
            )

        for position, (field_type, field_value) in enumerate(custom_field_rows, start=1):
            conn.execute(
                """
                INSERT INTO custom_fields (contact_id, position, field_type, field_value)
                VALUES (?, ?, ?, ?)
                """,
                (contact_id, position, field_type, field_value),
            )

        _store_contact_edit_entries(
            conn,
            contact_id,
            contact_name or f"Contact {contact_id}",
            editor_initials,
            contact_edit_entries,
        )

        conn.commit()

    try:
        from app.services.field_list_service import invalidate_field_list_print_payload_cache

        invalidate_field_list_print_payload_cache()
    except Exception:
        pass

    if queue_google_sync:
        queue_contact_sync(
            contact_id,
            "update",
            google_contact_id=google_contact_id,
            payload={
                "sync_action": action_hint,
                "family_name": core_fields["family_name"],
                "given_name": core_fields["given_name"],
                "fields_text": core_fields["fields_text"],
                "meetings_text": core_fields["meetings_text"],
                "group_membership": core_fields["group_membership"],
                "previous_group_membership": _clean_text(existing_contact.get("group_membership")),
                "previous_fields_text": _clean_text(existing_contact.get("fields_text")),
                "previous_meetings_text": _clean_text(existing_contact.get("meetings_text")),
            },
        )
        mark_contact_for_drive_export(contact_id)
    return True



def remove_contact_photo(
    contact_id: int,
    *,
    queue_google_sync: bool = True,
    delete_from_google: bool = True,
) -> str:
    """Remove the custom photo from Google and store Google's default letter avatar in the app DB."""
    from urllib.error import HTTPError, URLError

    from app.services.google_sync_service import (
        CONTACT_PHOTO_STATIC_PREFIX,
        _contact_photo_local_path,
        delete_google_contact_photo,
        fetch_google_contact_stock_photo_url,
    )

    row = fetch_one(
        """
        SELECT id, photo, google_contact_id
        FROM contacts
        WHERE id = ?
        """,
        (contact_id,),
    )
    if not row:
        return "contact_photo_remove_failed"

    previous_photo = _clean_text(row.get("photo"))
    google_contact_id = _clean_text(row.get("google_contact_id"))

    stock_photo_url = ""
    google_delete_failed = False
    if delete_from_google and google_contact_id:
        try:
            delete_google_contact_photo(google_contact_id)
        except (RuntimeError, HTTPError, URLError):
            google_delete_failed = True
        try:
            stock_photo_url = fetch_google_contact_stock_photo_url(google_contact_id)
        except (RuntimeError, HTTPError, URLError):
            stock_photo_url = ""

    # No custom photo to replace and we already have (or got) a stock URL.
    if not previous_photo:
        if stock_photo_url:
            contact = get_contact_detail(contact_id)
            if not contact:
                return "contact_photo_remove_failed"
            payload = _build_contact_update_payload(contact)
            payload["photo"] = stock_photo_url
            payload["google_contact_id"] = google_contact_id
            if not update_contact(contact_id, payload, queue_google_sync=queue_google_sync):
                return "contact_photo_remove_failed"
            return "contact_photo_remove_success"
        return "contact_photo_remove_already_clear"

    contact = get_contact_detail(contact_id)
    if not contact:
        return "contact_photo_remove_failed"
    payload = _build_contact_update_payload(contact)
    # Prefer Google's stock letter avatar URL in the app DB; fall back to empty (local letter).
    payload["photo"] = stock_photo_url
    payload["google_contact_id"] = google_contact_id
    if not update_contact(contact_id, payload, queue_google_sync=queue_google_sync):
        return "contact_photo_remove_failed"

    if previous_photo.startswith(CONTACT_PHOTO_STATIC_PREFIX):
        local_path = _contact_photo_local_path(previous_photo)
        if local_path is not None and local_path.is_file():
            try:
                local_path.unlink()
            except OSError:
                pass

    if google_delete_failed:
        return "contact_photo_remove_local_only"
    return "contact_photo_remove_success"



def _push_contact_photo_to_google_and_share(contact_id: int) -> str:
    """Upload the contact's local photo to Google Contacts and notify Share Contacts.

    Returns one of: ok, missing, not_connected, missing_bytes, google_failed.
    """
    from urllib.error import HTTPError, URLError

    from app.services.google_sync_service import (
        clear_pending_contact_sync,
        get_google_sync_summary,
        load_contact_photo_bytes,
        record_share_sync_result,
        update_google_contact_photo,
    )
    from app.services.share_sync_service import sync_share_app_contact_changes

    contact = get_contact_detail(contact_id)
    if not contact:
        return "missing"
    google_contact_id = _clean_text(contact.get("google_contact_id"))
    if not google_contact_id:
        return "not_connected"
    photo_bytes = load_contact_photo_bytes(contact.get("photo"))
    if not photo_bytes:
        return "missing_bytes"
    try:
        update_google_contact_photo(google_contact_id, photo_bytes)
    except (RuntimeError, HTTPError, URLError):
        return "google_failed"
    clear_pending_contact_sync(contact_id, google_contact_id)
    account_email = _clean_text((get_google_sync_summary().get("account") or {}).get("email"))
    if account_email:
        try:
            share_result = sync_share_app_contact_changes(account_email, [google_contact_id])
        except Exception as exc:
            share_result = {"ok": False, "reason": _clean_text(str(exc)) or "share_sync_failed"}
        record_share_sync_result(account_email, [google_contact_id], share_result)
    return "ok"


def apply_pending_photo_to_contact(contact_id: int) -> str:
    from urllib.error import HTTPError, URLError

    from app.services.changes_list_service import (
        find_pending_photo_location_for_contact,
        pending_photo_path_is_available,
    )
    from app.services.google_sync_service import (
        _clean_contact_photo_path,
        _contact_has_usable_local_photo,
        sync_pending_contact_photo_from_drive_for_editor,
    )

    contact = get_contact_detail(contact_id)
    if not contact:
        return "contact_photo_pending_missing"
    contact_name = _format_contact_name(contact.get("family_name"), contact.get("given_name"))
    pending = find_pending_photo_location_for_contact(contact_name)
    if not pending:
        return "contact_photo_pending_missing"
    pending_photo = _clean_contact_photo_path(pending.get("edited"))
    if not pending_photo:
        return "contact_photo_pending_missing"

    already_local = (
        _clean_text(contact.get("photo")) == pending_photo
        and pending_photo_path_is_available(pending_photo)
    )

    if not pending_photo_path_is_available(pending_photo):
        try:
            synced = sync_pending_contact_photo_from_drive_for_editor(
                contact_id,
                expected_photo_path=pending_photo,
            )
        except (HTTPError, URLError, RuntimeError):
            synced = False
        if not synced:
            contact = get_contact_detail(contact_id)
            if not contact or not _contact_has_usable_local_photo(contact):
                return "contact_photo_pending_not_synced"

    contact = get_contact_detail(contact_id)
    if not contact:
        return "contact_photo_pending_missing"
    if pending_photo_path_is_available(pending_photo):
        effective_photo = pending_photo
    elif _contact_has_usable_local_photo(contact):
        effective_photo = _clean_contact_photo_path(contact.get("photo"))
    else:
        return "contact_photo_pending_not_synced"

    # Always queue Google sync — Drive download can set the local photo path without
    # uploading to Google Contacts / shared accounts.
    payload = _build_contact_update_payload(contact)
    payload["photo"] = effective_photo
    if not update_contact(contact_id, payload):
        return "contact_photo_pending_missing"

    push_status = _push_contact_photo_to_google_and_share(contact_id)
    if push_status == "ok":
        return (
            "contact_photo_pending_already_applied"
            if already_local
            else "contact_photo_pending_applied"
        )
    if push_status == "not_connected":
        return "contact_photo_pending_not_connected"
    return "contact_photo_pending_applied_local"


def get_pending_photo_change_for_contact(contact: dict) -> dict | None:
    from app.services.changes_list_service import (
        find_pending_photo_location_for_contact,
        pending_photo_path_is_available,
    )
    from app.services.google_sync_service import _clean_contact_photo_path

    contact_name = _format_contact_name(contact.get("family_name"), contact.get("given_name"))
    pending = find_pending_photo_location_for_contact(contact_name)
    if not pending:
        return None
    pending_photo = _clean_contact_photo_path(pending.get("edited"))
    if not pending_photo:
        return None
    current_photo = _clean_contact_photo_path(contact.get("photo"))
    available = pending_photo_path_is_available(pending_photo)
    already_local = current_photo == pending_photo and available
    return {
        "edited": pending_photo,
        "available": available,
        "already_local": already_local,
    }


def create_contact(payload: dict) -> int:
    core_fields = {
        "selected": _clean_text(payload.get("selected")),
        "google_contact_id": _clean_text(payload.get("google_contact_id")),
        "etag": _clean_text(payload.get("etag")),
        "last_updated": _clean_text(payload.get("last_updated")),
        "status": _clean_text(payload.get("status")),
        "do_not_print": 1 if str(payload.get("do_not_print") or "").lower() in {"1", "true", "yes", "on"} else 0,
        "fields_text": _clean_text(payload.get("fields_text")),
        "meetings_text": _clean_text(payload.get("meetings_text")),
        "group_membership": "",
        "family_name": _clean_text(payload.get("family_name")),
        "given_name": _clean_text(payload.get("given_name")),
        "photo": _clean_text(payload.get("photo")),
        "birthday": _clean_text(payload.get("birthday")),
        "notes": _clean_text(payload.get("notes")),
        "mtg_home_elder_flag": _clean_text(payload.get("mtg_home_elder_flag")),
        "meets_in_state": _clean_text(payload.get("meets_in_state")),
    }
    core_fields["group_membership"] = build_contact_group_membership(core_fields["fields_text"], core_fields["meetings_text"])
    photo_sync_fields = _next_photo_sync_fields(None, core_fields["photo"])
    shared_drive_sync_fields = _next_shared_drive_sync_fields(None)

    relationship_rows = _normalize_repeatable_rows(
        payload.get("relationship_type", []),
        payload.get("relationship_value", []),
    )
    phone_rows = _normalize_repeatable_rows(
        payload.get("phone_type", []),
        payload.get("phone_value", []),
    )
    email_rows = _normalize_repeatable_rows(
        payload.get("email_type", []),
        payload.get("email_value", []),
    )
    address_rows = _normalize_address_rows(payload)
    custom_field_rows = _normalize_repeatable_rows(
        payload.get("custom_field_type", []),
        payload.get("custom_field_value", []),
    )
    custom_field_rows = [
        row
        for row in custom_field_rows
        if not _is_coordinate_custom_field(row[0], row[1])
    ]
    existing_contact = {
        "family_name": "",
        "given_name": "",
        "photo": "",
        "birthday": "",
        "do_not_print": 0,
        "mtg_home_elder_flag": "",
        "meets_in_state": "",
        "fields_text": "",
        "meetings_text": "",
        "notes": "",
        "emails": [],
        "phones": [],
        "addresses": [],
        "relationships": [],
        "custom_fields": [],
    }
    contact_edit_entries = _collect_contact_edit_entries(
        existing_contact,
        core_fields,
        relationship_rows,
        phone_rows,
        email_rows,
        address_rows,
        custom_field_rows,
    )
    editor_initials = get_current_editor_initials()
    contact_name = _format_contact_name(core_fields["family_name"], core_fields["given_name"])

    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO contacts (
              selected,
              google_contact_id,
              etag,
              last_updated,
              status,
              do_not_print,
              fields_text,
              meetings_text,
              group_membership,
              family_name,
              given_name,
              photo,
              photo_drive_file_id,
              photo_sync_revision,
              photo_needs_export,
              shared_drive_revision,
              shared_drive_needs_export,
              shared_drive_bootstrapped,
              birthday,
              notes,
              mtg_home_elder_flag,
              meets_in_state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                core_fields["selected"],
                core_fields["google_contact_id"],
                core_fields["etag"],
                core_fields["last_updated"],
                core_fields["status"],
                core_fields["do_not_print"],
                core_fields["fields_text"],
                core_fields["meetings_text"],
                core_fields["group_membership"],
                core_fields["family_name"],
                core_fields["given_name"],
                core_fields["photo"],
                photo_sync_fields["photo_drive_file_id"],
                photo_sync_fields["photo_sync_revision"],
                photo_sync_fields["photo_needs_export"],
                shared_drive_sync_fields["shared_drive_revision"],
                shared_drive_sync_fields["shared_drive_needs_export"],
                shared_drive_sync_fields["shared_drive_bootstrapped"],
                core_fields["birthday"],
                core_fields["notes"],
                core_fields["mtg_home_elder_flag"],
                core_fields["meets_in_state"],
            ),
        )
        contact_id = int(cursor.lastrowid)

        for position, (relation_type, relation_value) in enumerate(relationship_rows, start=1):
            conn.execute(
                """
                INSERT INTO relationships (contact_id, position, relation_type, relation_value)
                VALUES (?, ?, ?, ?)
                """,
                (contact_id, position, relation_type, relation_value),
            )

        for position, (phone_type, phone_value) in enumerate(phone_rows, start=1):
            conn.execute(
                """
                INSERT INTO phones (contact_id, position, phone_type, phone_value)
                VALUES (?, ?, ?, ?)
                """,
                (contact_id, position, phone_type, phone_value),
            )

        for position, (email_type, email_value) in enumerate(email_rows, start=1):
            conn.execute(
                """
                INSERT INTO emails (contact_id, position, email_type, email_value)
                VALUES (?, ?, ?, ?)
                """,
                (contact_id, position, email_type, email_value),
            )

        for position, address_row in enumerate(address_rows, start=1):
            (
                address_type,
                street_address,
                extended_address,
                city,
                region,
                postal_code,
                formatted_address,
                coordinates,
            ) = _address_insert_values(address_row)
            conn.execute(
                """
                INSERT INTO addresses (
                  contact_id, position, address_type,
                  street_address, extended_address, city, region, postal_code,
                  formatted_address, coordinates
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    contact_id,
                    position,
                    address_type,
                    street_address,
                    extended_address,
                    city,
                    region,
                    postal_code,
                    formatted_address,
                    coordinates,
                ),
            )

        for position, (field_type, field_value) in enumerate(custom_field_rows, start=1):
            conn.execute(
                """
                INSERT INTO custom_fields (contact_id, position, field_type, field_value)
                VALUES (?, ?, ?, ?)
                """,
                (contact_id, position, field_type, field_value),
            )
        _store_contact_edit_entries(
            conn,
            contact_id,
            contact_name or f"Contact {contact_id}",
            editor_initials,
            contact_edit_entries,
        )
        conn.commit()

    queue_contact_sync(
        contact_id,
        "update",
        google_contact_id="",
        payload={
            "sync_action": "Create",
            "family_name": core_fields["family_name"],
            "given_name": core_fields["given_name"],
            "fields_text": core_fields["fields_text"],
            "meetings_text": core_fields["meetings_text"],
            "group_membership": core_fields["group_membership"],
        },
    )
    mark_contact_for_drive_export(contact_id)
    return contact_id


def _build_meeting_preview(row: dict) -> str:
    parts = []
    for col_name in MEETING_COLUMN_NAMES:
        value = (row.get(col_name) or "").strip()
        if value:
            parts.append(value)
    return " | ".join(parts[:3])


def _row_has_meeting_content(row: dict) -> bool:
    if (row.get("format_code") or "").strip():
        return True
    for col_name in MEETING_COLUMN_NAMES:
        if (row.get(col_name) or "").strip():
            return True
    return False


def _build_visual_cells(row: dict) -> list[dict]:
    raw_cells = []
    for index, col_name in enumerate(MEETING_COLUMN_NAMES, start=1):
        value = (row.get(col_name) or "").strip()
        if value:
            raw_cells.append(
                {
                    "column_name": col_name.upper().replace("COL_", ""),
                    "value": value,
                    "grid_column": index,
                }
            )

    cells = []
    for idx, cell in enumerate(raw_cells):
        next_column = raw_cells[idx + 1]["grid_column"] if idx + 1 < len(raw_cells) else len(MEETING_COLUMN_NAMES) + 1
        span = max(1, next_column - cell["grid_column"])
        cell["grid_span"] = span
        cell["is_full_width"] = len(raw_cells) == 1
        cells.append(cell)
    return cells


def get_meeting_filter_options() -> dict:
    settings_row = fetch_one("SELECT print_order_json FROM address_book_settings WHERE id = 1")
    print_order_json = str(settings_row["print_order_json"] or "") if settings_row else ""
    fields = fetch_all(
        """
        SELECT DISTINCT field_name
        FROM meeting_data_rows
        WHERE TRIM(COALESCE(field_name, '')) != ''
        ORDER BY field_name
        """
    )
    meetings = fetch_all(
        """
        SELECT DISTINCT meeting_name
        FROM meeting_data_rows
        WHERE TRIM(COALESCE(meeting_name, '')) != ''
        ORDER BY meeting_name
        """
    )
    return {
        "fields": order_field_names([row["field_name"] for row in fields], print_order_json),
        "meetings": order_meeting_names([row["meeting_name"] for row in meetings], print_order_json),
    }


def _fetch_meeting_rows_with_context() -> list[dict]:
    rows = fetch_all(
        """
        SELECT *
        FROM meeting_data_rows
        ORDER BY sheet_row_number
        """
    )
    normalized = []
    current_field = ""
    current_meeting = ""

    for raw_row in rows:
        row = dict(raw_row)
        if (row.get("field_name") or "").strip():
            current_field = row["field_name"].strip()
        if (row.get("meeting_name") or "").strip():
            current_meeting = row["meeting_name"].strip()

        row["effective_field_name"] = current_field
        row["effective_meeting_name"] = current_meeting
        row["preview"] = _build_meeting_preview(row)
        row["cells"] = _build_visual_cells(row)
        row["has_visible_content"] = _row_has_meeting_content(row)
        normalized.append(row)

    return normalized


def list_meeting_rows(search_text: str = "", field_filter: str = "", meeting_filter: str = "") -> list[dict]:
    search_text = (search_text or "").strip()
    field_filter = (field_filter or "").strip()
    meeting_filter = (meeting_filter or "").strip()
    matched = []

    for row in _fetch_meeting_rows_with_context():
        effective_field = row.get("effective_field_name") or ""
        effective_meeting = row.get("effective_meeting_name") or ""
        haystack = " ".join(
            [
                effective_field,
                effective_meeting,
                row.get("format_code") or "",
                row.get("preview") or "",
            ]
        ).lower()

        if search_text and search_text.lower() not in haystack:
            continue
        if field_filter and field_filter != effective_field:
            continue
        if meeting_filter and meeting_filter != effective_meeting:
            continue
        if not row["has_visible_content"]:
            continue

        matched.append(row)

    return matched


def get_meeting_sections(search_text: str = "", field_filter: str = "", meeting_filter: str = "") -> list[dict]:
    rows = list_meeting_rows(search_text, field_filter, meeting_filter)
    sections: OrderedDict[tuple[str, str], dict] = OrderedDict()
    for row in rows:
        key = (
            row.get("effective_field_name") or "(No Field)",
            row.get("effective_meeting_name") or "(No Meeting)",
        )
        if key not in sections:
            sections[key] = {
                "field_name": key[0],
                "meeting_name": key[1],
                "rows": [],
            }
        sections[key]["rows"].append(
            {
                "id": row["id"],
                "sheet_row_number": row["sheet_row_number"],
                "format_code": row.get("format_code") or "",
                "preview": row.get("preview") or "",
                "cells": row.get("cells") or [],
                "is_underlined": "ul" in (row.get("format_code") or "").lower(),
            }
        )
    return list(sections.values())


def get_meeting_row_detail(row_id: int) -> dict | None:
    meeting_row = None
    for row in _fetch_meeting_rows_with_context():
        if row["id"] == row_id:
            meeting_row = row
            break
    if not meeting_row:
        return None

    columns = []
    for col_name in MEETING_COLUMN_NAMES:
        value = meeting_row.get(col_name)
        if value:
            columns.append(
                {
                    "name": col_name.upper().replace("COL_", ""),
                    "value": value,
                }
            )
    meeting_row["content_columns"] = columns
    meeting_row["visual_cells"] = meeting_row.get("cells") or []
    return meeting_row
