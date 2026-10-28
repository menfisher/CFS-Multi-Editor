from __future__ import annotations

from app.database import fetch_all, fetch_one, get_connection


def _clean(value: object) -> str:
    return str(value or "").strip()


def _list_field_end_list_rows(conn, field_name: str = "") -> list[dict]:
    scoped = _clean(field_name)
    if scoped:
        rows = conn.execute(
            """
            SELECT id, field_name, list_name, sort_order
            FROM field_end_lists
            WHERE field_name = ?
            ORDER BY sort_order, lower(list_name), id
            """,
            (scoped,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, field_name, list_name, sort_order
            FROM field_end_lists
            ORDER BY field_name COLLATE NOCASE, sort_order, lower(list_name), id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def list_field_end_lists(field_name: str = "", conn=None) -> list[dict]:
    if conn is not None:
        return _list_field_end_list_rows(conn, field_name)
    scoped = _clean(field_name)
    if scoped:
        rows = fetch_all(
            """
            SELECT id, field_name, list_name, sort_order
            FROM field_end_lists
            WHERE field_name = ?
            ORDER BY sort_order, lower(list_name), id
            """,
            (scoped,),
        )
    else:
        rows = fetch_all(
            """
            SELECT id, field_name, list_name, sort_order
            FROM field_end_lists
            ORDER BY field_name COLLATE NOCASE, sort_order, lower(list_name), id
            """
        )
    return [dict(row) for row in rows]


def field_end_list_names_by_field() -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for item in list_field_end_lists():
        field_name = _clean(item.get("field_name"))
        list_name = _clean(item.get("list_name"))
        if not field_name or not list_name:
            continue
        grouped.setdefault(field_name, []).append(list_name)
    return grouped


def is_field_end_list(field_name: object, meeting_name: object, conn=None) -> bool:
    field = _clean(field_name)
    meeting = _clean(meeting_name)
    if not field or not meeting:
        return False
    return any(
        _clean(item.get("list_name")) == meeting
        for item in list_field_end_lists(field, conn=conn)
    )


def _section_id_for_field_list(field_name: str, list_name: str) -> int | None:
    field = _clean(field_name)
    name = _clean(list_name)
    if not field or not name:
        return None
    row = fetch_one(
        """
        SELECT id
        FROM meeting_sections_v2
        WHERE field_name = ? AND meeting_name = ?
        ORDER BY id
        LIMIT 1
        """,
        (field, name),
    )
    if not row:
        return None
    return int(row["id"])


def ensure_field_end_list_section(field_name: str, list_name: str) -> int:
    field = _clean(field_name)
    name = _clean(list_name)
    existing_id = _section_id_for_field_list(field, name)
    if existing_id:
        return existing_id
    from app.services.meeting_v2_service import create_meeting_v2_section

    return create_meeting_v2_section(field, name)


def ensure_field_end_list_sections() -> None:
    for item in list_field_end_lists():
        field_name = _clean(item.get("field_name"))
        list_name = _clean(item.get("list_name"))
        if field_name and list_name:
            ensure_field_end_list_section(field_name, list_name)


def _field_list_payload(list_id: int, field: str, name: str, sort_order: int, section_id: int) -> dict:
    return {
        "id": list_id,
        "field_name": field,
        "list_name": name,
        "sort_order": sort_order,
        "section_id": section_id,
        "editor_url": f"/meetings/v2/sections/{section_id}/editor#row-1",
    }


def add_field_end_list(field_name: str, list_name: str) -> dict:
    field = _clean(field_name)
    name = _clean(list_name)
    if not field:
        raise ValueError("Select a Field before adding a Field list.")
    if not name:
        raise ValueError("Field list name is required.")
    existing = list_field_end_lists(field)
    already = next(
        (item for item in existing if _clean(item.get("list_name")).lower() == name.lower()),
        None,
    )
    if already:
        saved_name = _clean(already.get("list_name")) or name
        section_id = ensure_field_end_list_section(field, saved_name)
        return _field_list_payload(
            int(already.get("id") or 0),
            field,
            saved_name,
            int(already.get("sort_order") or 0),
            section_id,
        )
    meeting_conflict = fetch_all(
        """
        SELECT meeting_name
        FROM meeting_sections_v2
        WHERE field_name = ? AND lower(meeting_name) = lower(?)
        LIMIT 1
        """,
        (field, name),
    )
    if meeting_conflict:
        raise ValueError("That name is already used by a Meeting in the selected Field.")
    next_order = max((int(item.get("sort_order") or 0) for item in existing), default=0) + 1
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO field_end_lists (field_name, list_name, sort_order)
            VALUES (?, ?, ?)
            """,
            (field, name, next_order),
        )
        list_id = int(cursor.lastrowid)
        conn.commit()
    section_id = ensure_field_end_list_section(field, name)
    return _field_list_payload(list_id, field, name, next_order, section_id)


def _fallback_editor_url(field_name: str, excluded_section_id: int | None = None) -> str:
    from app.services.meeting_v2_service import list_meeting_v2_sections

    for item in list_meeting_v2_sections("", field_name, ""):
        section_id = int(item.get("id") or 0)
        if section_id and section_id != int(excluded_section_id or 0):
            return f"/meetings/v2/sections/{section_id}/editor"
    return ""


def rename_field_end_list(field_name: str, old_list_name: str, new_list_name: str) -> dict:
    field = _clean(field_name)
    old_name = _clean(old_list_name)
    new_name = _clean(new_list_name)
    if not field:
        raise ValueError("Select a Field first.")
    if not old_name:
        raise ValueError("Select a Field list to rename first.")
    if not new_name:
        raise ValueError("Field list name is required.")
    existing = list_field_end_lists(field)
    current = next(
        (item for item in existing if _clean(item.get("list_name")).lower() == old_name.lower()),
        None,
    )
    if not current:
        raise ValueError("That Field list is not in the selected Field.")
    saved_old_name = _clean(current.get("list_name")) or old_name
    if saved_old_name == new_name:
        section_id = ensure_field_end_list_section(field, saved_old_name)
        return {
            **_field_list_payload(
                int(current.get("id") or 0),
                field,
                saved_old_name,
                int(current.get("sort_order") or 0),
                section_id,
            ),
            "old_list_name": saved_old_name,
            "lists": existing,
        }
    if any(
        _clean(item.get("list_name")).lower() == new_name.lower()
        and _clean(item.get("list_name")).lower() != saved_old_name.lower()
        for item in existing
    ):
        raise ValueError("That Field list already exists in the selected Field.")
    meeting_conflict = fetch_one(
        """
        SELECT id
        FROM meeting_sections_v2
        WHERE field_name = ?
          AND lower(meeting_name) = lower(?)
          AND meeting_name != ?
        LIMIT 1
        """,
        (field, new_name, saved_old_name),
    )
    if meeting_conflict:
        raise ValueError("That name is already used by a Meeting in the selected Field.")
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE field_end_lists
            SET list_name = ?
            WHERE field_name = ? AND list_name = ?
            """,
            (new_name, field, saved_old_name),
        )
        conn.execute(
            """
            UPDATE meeting_sections_v2
            SET meeting_name = ?
            WHERE field_name = ? AND meeting_name = ?
            """,
            (new_name, field, saved_old_name),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO meeting_section_name_options (kind, value)
            VALUES ('meeting', ?)
            """,
            (new_name,),
        )
        remaining_old = conn.execute(
            """
            SELECT 1
            FROM meeting_sections_v2
            WHERE meeting_name = ?
            LIMIT 1
            """,
            (saved_old_name,),
        ).fetchone()
        remaining_old_lists = conn.execute(
            """
            SELECT 1
            FROM field_end_lists
            WHERE list_name = ?
            LIMIT 1
            """,
            (saved_old_name,),
        ).fetchone()
        if not remaining_old and not remaining_old_lists:
            conn.execute(
                """
                DELETE FROM meeting_section_name_options
                WHERE kind = 'meeting' AND value = ?
                """,
                (saved_old_name,),
            )
        conn.commit()
    section_id = ensure_field_end_list_section(field, new_name)
    return {
        **_field_list_payload(
            int(current.get("id") or 0),
            field,
            new_name,
            int(current.get("sort_order") or 0),
            section_id,
        ),
        "old_list_name": saved_old_name,
        "lists": list_field_end_lists(field),
    }


def delete_field_end_list(field_name: str, list_name: str) -> dict:
    field = _clean(field_name)
    name = _clean(list_name)
    if not field or not name:
        raise ValueError("Select a Field list to delete first.")
    current = next(
        (item for item in list_field_end_lists(field) if _clean(item.get("list_name")).lower() == name.lower()),
        None,
    )
    saved_name = _clean((current or {}).get("list_name")) or name
    section_id = _section_id_for_field_list(field, saved_name)
    with get_connection() as conn:
        conn.execute(
            """
            DELETE FROM field_end_lists
            WHERE field_name = ? AND list_name = ?
            """,
            (field, saved_name),
        )
        conn.commit()
    if section_id:
        from app.services.meeting_v2_service import delete_meeting_v2_section

        delete_meeting_v2_section(section_id)
    remaining = list_field_end_lists(field)
    return {
        "value": saved_name,
        "deleted_section_id": section_id,
        "fallback_editor_url": _fallback_editor_url(field, section_id),
        "lists": remaining,
    }


def reorder_field_end_lists(field_name: str, list_names: list[str]) -> list[dict]:
    field = _clean(field_name)
    if not field:
        raise ValueError("Select a Field first.")
    current = list_field_end_lists(field)
    current_names = [_clean(item.get("list_name")) for item in current]
    requested = [_clean(name) for name in list_names if _clean(name)]
    if set(requested) != set(current_names):
        raise ValueError("The Field list order is missing one of the lists.")
    with get_connection() as conn:
        for index, name in enumerate(requested, start=1):
            conn.execute(
                """
                UPDATE field_end_lists
                SET sort_order = ?
                WHERE field_name = ? AND list_name = ?
                """,
                (index, field, name),
            )
        conn.commit()
    return list_field_end_lists(field)


def rename_field_end_lists_for_field(old_field_name: str, new_field_name: str, conn=None) -> None:
    old_field = _clean(old_field_name)
    new_field = _clean(new_field_name)
    if not old_field or not new_field or old_field == new_field:
        return
    if conn is None:
        with get_connection() as owned:
            _rename_field_end_lists_for_field(owned, old_field, new_field)
            owned.commit()
        return
    _rename_field_end_lists_for_field(conn, old_field, new_field)


def _rename_field_end_lists_for_field(conn, old_field: str, new_field: str) -> None:
    conn.execute(
        """
        UPDATE field_end_lists
        SET field_name = ?
        WHERE field_name = ?
        """,
        (new_field, old_field),
    )


def rename_field_end_list_meeting(old_meeting_name: str, new_meeting_name: str, conn=None) -> None:
    old_name = _clean(old_meeting_name)
    new_name = _clean(new_meeting_name)
    if not old_name or not new_name or old_name == new_name:
        return
    if conn is None:
        with get_connection() as owned:
            _rename_field_end_list_meeting(owned, old_name, new_name)
            owned.commit()
        return
    _rename_field_end_list_meeting(conn, old_name, new_name)


def _rename_field_end_list_meeting(conn, old_name: str, new_name: str) -> None:
    conn.execute(
        """
        UPDATE field_end_lists
        SET list_name = ?
        WHERE list_name = ?
        """,
        (new_name, old_name),
    )
