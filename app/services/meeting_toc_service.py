from __future__ import annotations

from app.database import fetch_all, get_connection


def _clean(value: object) -> str:
    return str(value or "").strip()


def _pair_key(field_name: object, meeting_name: object) -> tuple[str, str]:
    return (_clean(field_name).casefold(), _clean(meeting_name).casefold())


def list_unlisted_toc_meetings() -> list[dict[str, str]]:
    rows = fetch_all(
        """
        SELECT field_name, meeting_name
        FROM meeting_toc_exclusions
        ORDER BY field_name COLLATE NOCASE, meeting_name COLLATE NOCASE
        """
    )
    return [
        {
            "field_name": _clean(row["field_name"]),
            "meeting_name": _clean(row["meeting_name"]),
        }
        for row in rows
        if _clean(row["meeting_name"])
    ]


def unlisted_toc_meeting_keys() -> set[tuple[str, str]]:
    return {_pair_key(item["field_name"], item["meeting_name"]) for item in list_unlisted_toc_meetings()}


def meeting_is_unlisted_in_toc(field_name: object, meeting_name: object, keys: set[tuple[str, str]] | None = None) -> bool:
    meeting = _clean(meeting_name)
    if not meeting:
        return False
    lookup = keys if keys is not None else unlisted_toc_meeting_keys()
    return _pair_key(field_name, meeting) in lookup


def _normalized_pairs(pairs: list[tuple[str, str]] | list[dict]) -> list[tuple[str, str]]:
    cleaned: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in pairs or []:
        if isinstance(item, dict):
            field_name = _clean(item.get("field_name") or item.get("field"))
            meeting_name = _clean(item.get("meeting_name") or item.get("meeting"))
        else:
            field_name, meeting_name = item
            field_name = _clean(field_name)
            meeting_name = _clean(meeting_name)
        if not meeting_name:
            continue
        if not field_name:
            field_name = "No Field"
        key = _pair_key(field_name, meeting_name)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append((field_name, meeting_name))
    return cleaned


def set_meetings_unlisted_in_toc(pairs: list[tuple[str, str]] | list[dict], unlisted: bool) -> list[dict[str, str]]:
    cleaned = _normalized_pairs(pairs)
    if not cleaned:
        return list_unlisted_toc_meetings()
    with get_connection() as conn:
        if unlisted:
            conn.executemany(
                """
                INSERT OR IGNORE INTO meeting_toc_exclusions (field_name, meeting_name)
                VALUES (?, ?)
                """,
                cleaned,
            )
        else:
            for field_name, meeting_name in cleaned:
                conn.execute(
                    """
                    DELETE FROM meeting_toc_exclusions
                    WHERE field_name = ? COLLATE NOCASE
                      AND meeting_name = ? COLLATE NOCASE
                    """,
                    (field_name, meeting_name),
                )
        conn.commit()
    return list_unlisted_toc_meetings()


def replace_unlisted_toc_meetings(pairs: list[dict] | None) -> None:
    cleaned = _normalized_pairs(pairs or [])
    with get_connection() as conn:
        conn.execute("DELETE FROM meeting_toc_exclusions")
        if cleaned:
            conn.executemany(
                """
                INSERT INTO meeting_toc_exclusions (field_name, meeting_name)
                VALUES (?, ?)
                """,
                cleaned,
            )
        conn.commit()


def rename_unlisted_toc_assignment(kind: str, old_value: str, new_value: str, conn=None) -> None:
    column = "field_name" if str(kind or "").strip().lower() == "field" else "meeting_name"
    if column not in {"field_name", "meeting_name"}:
        return
    previous = _clean(old_value)
    nxt = _clean(new_value)
    if not previous or not nxt or previous.casefold() == nxt.casefold():
        return

    def _rename(active) -> None:
        active.execute(
            f"""
            UPDATE meeting_toc_exclusions
            SET {column} = ?
            WHERE {column} = ? COLLATE NOCASE
            """,
            (nxt, previous),
        )

    if conn is not None:
        _rename(conn)
        return
    with get_connection() as owned:
        _rename(owned)
        owned.commit()
