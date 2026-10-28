from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from difflib import SequenceMatcher
from html import escape
from urllib.parse import quote
from zoneinfo import ZoneInfo

from app.database import get_connection


CHANGES_LIST_TIMEZONE = ZoneInfo("America/Chicago")


def _now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _changes_timestamp(initials: str) -> str:
    now = datetime.now(CHANGES_LIST_TIMEZONE)
    normalized_initials = str(initials or "").strip()
    return f"{normalized_initials}, {now:%m/%d/%Y}, {now:%H:%M}"


def _normalize_stored_timestamp(value: str) -> str:
    parts = [part.strip() for part in str(value or "").split(",")]
    if len(parts) != 3 or "/" not in parts[1]:
        return str(value or "")
    date_parts = parts[1].split("/")
    if len(date_parts) != 3:
        return str(value or "")
    try:
        first = int(date_parts[0])
        second = int(date_parts[1])
    except ValueError:
        return str(value or "")
    if first <= 12:
        return str(value or "")
    return f"{parts[0]}, {second:02d}/{first:02d}/{date_parts[2]}, {parts[2]}"


def _entry_has_content(entry: dict) -> bool:
    return bool(
        str(entry.get("name_event") or "").strip()
        or str(entry.get("changes_text") or "").strip()
        or str(entry.get("completed_by") or "").strip()
    )


def normalize_changes_list_entry(item: dict, *, sort_order: int = 0) -> dict:
    try:
        resolved_sort_order = int(item.get("sort_order") or sort_order)
    except (TypeError, ValueError):
        resolved_sort_order = sort_order
    return {
        "sort_order": resolved_sort_order,
        "automatic_date": str(item.get("automatic_date") or "").strip(),
        "name_event": str(item.get("name_event") or "").strip(),
        "changes_text": str(item.get("changes_text") or "").strip(),
        "completed_by": str(item.get("completed_by") or "").strip(),
        "completed_at": str(item.get("completed_at") or "").strip(),
    }


def _normalize_changes_text_for_key(changes_text: str) -> str:
    """Stable text for merge keys across desktop/mobile/Drive copies of one edit."""
    body = _changes_text_for_diff(str(changes_text or ""))
    # Drive and SQLite often disagree on CR vs LF for the same Pending Edit.
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in body.split("\n")]
    return "\n".join(lines).strip()


def _changes_list_entry_key(entry: dict) -> tuple[str, str, str]:
    # Ignore the "Cv - " completion prefix so completed/incomplete copies of the
    # same Pending Edits row merge instead of duplicating.
    return (
        str(entry.get("automatic_date") or "").strip(),
        str(entry.get("name_event") or "").strip(),
        _normalize_changes_text_for_key(str(entry.get("changes_text") or "")),
    )


def _entry_merge_rank(entry: dict) -> tuple[int, int, str]:
    """Higher rank wins when two sources describe the same Pending Edits row."""
    completed = 1 if str(entry.get("completed_by") or "").strip() else 0
    prefix, _body = _split_completed_prefix(str(entry.get("changes_text") or ""))
    has_prefix = 1 if prefix else 0
    return (completed, has_prefix, str(entry.get("completed_at") or "").strip())


def _automatic_date_sort_key(value: str) -> tuple[int, float]:
    """Sort key for Pending Edits dates. Higher tuple sorts newer when reverse=True."""
    text = _normalize_stored_timestamp(str(value or "").strip())
    parts = [part.strip() for part in text.split(",")]
    if len(parts) != 3:
        return (0, 0.0)
    try:
        stamp = datetime.strptime(f"{parts[1]} {parts[2]}", "%m/%d/%Y %H:%M")
    except ValueError:
        return (0, 0.0)
    # Attach timezone so DST-safe comparisons stay consistent with Chicago stamps.
    stamp = stamp.replace(tzinfo=CHANGES_LIST_TIMEZONE)
    return (1, stamp.timestamp())


def sort_changes_list_entries_newest_first(entries: list[dict]) -> list[dict]:
    """Put newest automatic_date rows first so they appear at the top of the list UI."""
    ordered = sorted(
        entries,
        key=lambda entry: (
            _automatic_date_sort_key(str(entry.get("automatic_date") or "")),
            str(entry.get("name_event") or "").lower(),
            str(entry.get("changes_text") or "").lower(),
        ),
        reverse=True,
    )
    for index, entry in enumerate(ordered, start=2):
        entry["sort_order"] = index
    return ordered


def merge_changes_list_entry_lists(*sources: object) -> list[dict]:
    """Combine pending-edit rows from Drive and local editors without dropping others."""
    merged_by_key: dict[tuple[str, str, str], dict] = {}
    key_order: list[tuple[str, str, str]] = []
    for source in sources:
        if not isinstance(source, list):
            continue
        for index, item in enumerate(source, start=1):
            if not isinstance(item, dict):
                continue
            entry = normalize_changes_list_entry(item, sort_order=index)
            if not _entry_has_content(entry):
                continue
            key = _changes_list_entry_key(entry)
            existing = merged_by_key.get(key)
            if existing is None:
                merged_by_key[key] = entry
                key_order.append(key)
                continue
            # Prefer Editor-completed rows over older incomplete copies from Drive/mobile.
            if _entry_merge_rank(entry) > _entry_merge_rank(existing):
                merged_by_key[key] = entry
    return sort_changes_list_entries_newest_first([merged_by_key[key] for key in key_order])


def changes_list_entries_equal(left: object, right: object) -> bool:
    return merge_changes_list_entry_lists(left) == merge_changes_list_entry_lists(right)


def has_meaningful_changes_list_entries(entries: object) -> bool:
    if not isinstance(entries, list):
        return False
    return any(_entry_has_content(item) for item in entries if isinstance(item, dict))


def ensure_changes_list_entries() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS changes_list_entries (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              sort_order INTEGER NOT NULL,
              automatic_date TEXT NOT NULL DEFAULT '',
              name_event TEXT NOT NULL DEFAULT '',
              changes_text TEXT NOT NULL DEFAULT '',
              completed_by TEXT NOT NULL DEFAULT '',
              completed_at TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(changes_list_entries)").fetchall()
        }
        if "completed_by" not in columns:
            conn.execute("ALTER TABLE changes_list_entries ADD COLUMN completed_by TEXT NOT NULL DEFAULT ''")
        if "completed_at" not in columns:
            conn.execute("ALTER TABLE changes_list_entries ADD COLUMN completed_at TEXT NOT NULL DEFAULT ''")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_changes_list_entries_sort_order
            ON changes_list_entries (sort_order, id)
            """
        )
        blank_rows = conn.execute(
            """
            SELECT id
            FROM changes_list_entries
            WHERE automatic_date = '' AND name_event = '' AND changes_text = '' AND completed_by = ''
            ORDER BY sort_order, id
            """
        ).fetchall()
        for blank_row in blank_rows[1:]:
            conn.execute(
                "DELETE FROM changes_list_entries WHERE id = ?",
                (int(blank_row["id"]),),
            )
        top_row = conn.execute(
            """
            SELECT automatic_date, name_event, changes_text, completed_by
            FROM changes_list_entries
            ORDER BY sort_order, id
            LIMIT 1
            """
        ).fetchone()
        top_row_has_content = bool(
            top_row
            and (
                str(top_row["automatic_date"] or "").strip()
                or str(top_row["name_event"] or "").strip()
                or str(top_row["changes_text"] or "").strip()
                or str(top_row["completed_by"] or "").strip()
            )
        )
        if top_row_has_content:
            min_row = conn.execute(
                "SELECT COALESCE(MIN(sort_order), 0) AS min_sort_order FROM changes_list_entries"
            ).fetchone()
            next_sort_order = int(min_row["min_sort_order"] or 0) - 1
            conn.execute(
                """
                INSERT INTO changes_list_entries (sort_order)
                VALUES (?)
                """,
                (next_sort_order,),
            )
        elif not top_row:
            conn.execute(
                """
                INSERT INTO changes_list_entries (sort_order)
                VALUES (1)
                """
            )
        dated_rows = conn.execute(
            """
            SELECT id, automatic_date
            FROM changes_list_entries
            WHERE automatic_date != ''
            """
        ).fetchall()
        for dated_row in dated_rows:
            current_value = str(dated_row["automatic_date"] or "")
            normalized_value = _normalize_stored_timestamp(current_value)
            if normalized_value != current_value:
                conn.execute(
                    """
                    UPDATE changes_list_entries
                    SET automatic_date = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (normalized_value, _now_text(), int(dated_row["id"])),
                )
        conn.commit()


def list_changes_entries() -> list[dict]:
    ensure_changes_list_entries()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, sort_order, automatic_date, name_event, changes_text, completed_by, completed_at
            FROM changes_list_entries
            ORDER BY sort_order, id
            """
        ).fetchall()
    entries = [
        {
            "id": int(row["id"]),
            "sort_order": int(row["sort_order"] or 0),
            "automatic_date": str(row["automatic_date"] or ""),
            "name_event": str(row["name_event"] or ""),
            "changes_text": str(row["changes_text"] or ""),
            "completed_by": str(row["completed_by"] or ""),
            "completed_at": str(row["completed_at"] or ""),
        }
        for row in rows
    ]
    # Always show the blank "row 1" entry first, then newest dated edits.
    # Drive merge used to append mobile uploads at the bottom; this keeps the UI stable.
    blank_entries = [entry for entry in entries if not _entry_has_content(entry)]
    content_entries = sort_changes_list_entries_newest_first(
        [entry for entry in entries if _entry_has_content(entry)]
    )
    ordered = (blank_entries[:1] if blank_entries else []) + content_entries
    for index, entry in enumerate(ordered, start=1):
        entry["sort_order"] = index
    return ordered


def mark_changes_list_for_drive_export(conn=None) -> None:
    if conn is not None:
        conn.execute(
            """
            UPDATE google_sync_state
            SET changes_list_needs_drive_export = 1, updated_at = ?
            WHERE id = 1
            """,
            (_now_text(),),
        )
        return
    with get_connection() as managed_conn:
        mark_changes_list_for_drive_export(managed_conn)
        managed_conn.commit()


def record_contact_edits_to_pending_list(
    *,
    contact_name: str,
    edit_entries: list[dict],
    initials: str,
) -> dict:
    cleaned_initials = str(initials or "").strip()
    cleaned_name = str(contact_name or "").strip()
    if not cleaned_initials or not edit_entries:
        return {}

    lines: list[str] = []
    for entry in edit_entries:
        label = str(entry.get("label_name") or "").strip()
        original = str(entry.get("original_text") or "").strip()
        edited = str(entry.get("edited_text") or "").strip()
        if not label:
            continue
        if original and edited and original != edited:
            lines.append(
                f"{label}: {_escape_change_text(original)} → {_escape_change_text(edited)}"
            )
        elif edited:
            lines.append(f"{label}: {edited}")
        elif original:
            lines.append(f"{label}: removed {original}")
    if not lines:
        return {}

    entry = add_changes_entry()
    entry_id = int(entry.get("id") or 0)
    if not entry_id:
        return {}
    return update_changes_entry(
        entry_id,
        name_event=cleaned_name,
        changes_text="\n".join(lines),
        initials=cleaned_initials,
    )


def add_changes_entry() -> dict:
    ensure_changes_list_entries()
    with get_connection() as conn:
        top_row = conn.execute(
            """
            SELECT id, automatic_date, name_event, changes_text, completed_by
            FROM changes_list_entries
            ORDER BY sort_order, id
            LIMIT 1
            """
        ).fetchone()
        if top_row and not _entry_has_content(dict(top_row)):
            return get_changes_entry(int(top_row["id"]))
        row = conn.execute(
            "SELECT COALESCE(MIN(sort_order), 0) AS min_sort_order FROM changes_list_entries"
        ).fetchone()
        next_sort_order = int(row["min_sort_order"] or 0) - 1
        cursor = conn.execute(
            """
            INSERT INTO changes_list_entries (sort_order, updated_at)
            VALUES (?, ?)
            """,
            (next_sort_order, _now_text()),
        )
        entry_id = int(cursor.lastrowid)
        conn.commit()
    return get_changes_entry(entry_id)


def get_changes_entry(entry_id: int) -> dict:
    ensure_changes_list_entries()
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, sort_order, automatic_date, name_event, changes_text, completed_by, completed_at
            FROM changes_list_entries
            WHERE id = ?
            """,
            (int(entry_id),),
        ).fetchone()
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "sort_order": int(row["sort_order"] or 0),
        "automatic_date": str(row["automatic_date"] or ""),
        "name_event": str(row["name_event"] or ""),
        "changes_text": str(row["changes_text"] or ""),
        "completed_by": str(row["completed_by"] or ""),
        "completed_at": str(row["completed_at"] or ""),
    }


def update_changes_entry(entry_id: int, *, name_event: str, changes_text: str, initials: str) -> dict:
    ensure_changes_list_entries()
    cleaned_name_event = str(name_event or "").strip()
    cleaned_changes_text = str(changes_text or "").strip()
    cleaned_initials = str(initials or "").strip()
    if not cleaned_initials:
        return {}
    with get_connection() as conn:
        existing = conn.execute(
            """
            SELECT id, automatic_date
            FROM changes_list_entries
            WHERE id = ?
            """,
            (int(entry_id),),
        ).fetchone()
        if not existing:
            return {}
        automatic_date = str(existing["automatic_date"] or "")
        if not cleaned_name_event and not cleaned_changes_text:
            automatic_date = ""
        elif not automatic_date and (cleaned_name_event or cleaned_changes_text):
            automatic_date = _changes_timestamp(cleaned_initials)
        conn.execute(
            """
            UPDATE changes_list_entries
            SET automatic_date = ?, name_event = ?, changes_text = ?, updated_at = ?
            WHERE id = ?
            """,
            (automatic_date, cleaned_name_event, cleaned_changes_text, _now_text(), int(entry_id)),
        )
        mark_changes_list_for_drive_export(conn)
        conn.commit()
    return get_changes_entry(entry_id)


def complete_changes_entry(entry_id: int, *, initials: str) -> dict:
    ensure_changes_list_entries()
    cleaned_initials = str(initials or "").strip()
    if not cleaned_initials:
        return {}
    with get_connection() as conn:
        existing = conn.execute(
            """
            SELECT id, changes_text
            FROM changes_list_entries
            WHERE id = ?
            """,
            (int(entry_id),),
        ).fetchone()
        if not existing:
            return {}
        changes_text = str(existing["changes_text"] or "").strip()
        prefix = f"{cleaned_initials} - "
        if changes_text and not changes_text.lower().startswith(prefix.lower()):
            changes_text = f"{prefix}{changes_text}"
        elif not changes_text:
            changes_text = prefix.rstrip()
        conn.execute(
            """
            UPDATE changes_list_entries
            SET changes_text = ?, completed_by = ?, completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (changes_text, cleaned_initials, _now_text(), _now_text(), int(entry_id)),
        )
        mark_changes_list_for_drive_export(conn)
        conn.commit()
    return get_changes_entry(entry_id)


def _escape_change_text(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace("\n", "\\n")


def _unescape_change_text(value: str) -> str:
    text = str(value or "")
    parts: list[str] = []
    index = 0
    while index < len(text):
        if text.startswith("\\n", index):
            parts.append("\n")
            index += 2
            continue
        if text.startswith("\\\\", index):
            parts.append("\\")
            index += 2
            continue
        parts.append(text[index])
        index += 1
    return "".join(parts)


def _plain_changes_html(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return escape(text).replace("\n", "<br>")


def _highlight_text_html(value: str) -> str:
    return escape(value).replace("\n", "<br>")


def _tokenize_edit_text(value: str) -> list[str]:
    return [token for token in re.split(r"(\s+|[^\w\s]+)", str(value or "")) if token]


def _mark_old_html(value: str) -> str:
    if not value:
        return ""
    return f'<mark class="pending-edit-old">{_highlight_text_html(value)}</mark>'


def _mark_new_html(value: str) -> str:
    if not value:
        return ""
    return f'<mark class="pending-edit-new">{_highlight_text_html(value)}</mark>'


def _build_single_line_diff_html(original: str, edited: str) -> str:
    if original == edited:
        return _highlight_text_html(edited)

    original_tokens = _tokenize_edit_text(original)
    edited_tokens = _tokenize_edit_text(edited)
    matcher = SequenceMatcher(a=original_tokens, b=edited_tokens, autojunk=False)
    old_parts: list[str] = []
    new_parts: list[str] = []
    changed = False
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        original_segment = "".join(original_tokens[i1:i2])
        edited_segment = "".join(edited_tokens[j1:j2])
        if tag == "equal":
            old_parts.append(_highlight_text_html(original_segment))
            new_parts.append(_highlight_text_html(edited_segment))
            continue
        changed = True
        if tag in {"delete", "replace"} and original_segment:
            old_parts.append(_mark_old_html(original_segment))
        if tag in {"insert", "replace"} and edited_segment:
            new_parts.append(_mark_new_html(edited_segment))
        elif tag == "delete" and not edited_segment:
            old_parts.append(_mark_old_html(original_segment or "[deleted]"))

    if not changed:
        return _highlight_text_html(edited)
    return f'{"".join(old_parts)} <span class="pending-edit-arrow">→</span> {"".join(new_parts)}'


def _build_multiline_diff_html(original: str, edited: str, *, compact: bool = False) -> str:
    original_lines = original.splitlines()
    edited_lines = edited.splitlines()
    if len(original_lines) <= 1 and len(edited_lines) <= 1:
        return _build_single_line_diff_html(original, edited)

    line_count = max(len(original_lines), len(edited_lines))
    rendered_lines: list[str] = []
    changed = False
    for index in range(line_count):
        current_original = original_lines[index] if index < len(original_lines) else ""
        current_edited = edited_lines[index] if index < len(edited_lines) else ""
        rendered_lines.append(_build_single_line_diff_html(current_original, current_edited))
        if current_original != current_edited:
            changed = True
    if not changed:
        return _highlight_text_html(edited)
    separator = " " if compact else "<br>"
    return separator.join(rendered_lines)


def _split_completed_prefix(changes_text: str) -> tuple[str, str]:
    text = str(changes_text or "").strip()
    # Initials must include a letter so numbered edits like "1 - Meeting home..."
    # are not treated as a Completed Edit(s) prefix (that broke Drive merge keys).
    match = re.match(r"^([A-Za-z][A-Za-z0-9]{0,11}) - (.+)$", text, re.DOTALL)
    if not match:
        return "", text
    return match.group(1).strip(), match.group(2).strip()


def _changes_text_for_diff(changes_text: str) -> str:
    _initials, body = _split_completed_prefix(changes_text)
    return body or str(changes_text or "")


def _parse_change_line(line: str) -> tuple[str, str, str] | None:
    text = str(line or "").strip()
    if ": " not in text or " → " not in text:
        return None
    label, remainder = text.split(": ", 1)
    original, edited = remainder.split(" → ", 1)
    return label.strip(), _unescape_change_text(original), _unescape_change_text(edited)


def _parse_change_blocks(changes_text: str) -> list[tuple[str, str, str]]:
    text = str(changes_text or "").strip()
    if not text:
        return []

    lines = [line for line in text.split("\n") if line.strip()]
    parsed_lines = [_parse_change_line(line) for line in lines]
    if parsed_lines and all(parsed is not None for parsed in parsed_lines):
        return [parsed for parsed in parsed_lines if parsed]

    if ": " in text and " → " in text:
        label, remainder = text.split(": ", 1)
        original, edited = remainder.split(" → ", 1)
        return [(label.strip(), original, edited)]
    return []


def changes_text_has_diff_format(changes_text: str) -> bool:
    return bool(_parse_change_blocks(_changes_text_for_diff(changes_text)))


def _format_field_change_html(label: str, original: str, edited: str, *, compact: bool = False) -> str:
    diff_html = _build_multiline_diff_html(original, edited, compact=compact)
    safe_label = escape(label)
    if compact:
        return (
            f'<span class="pending-change-field pending-change-field-compact">'
            f'<span class="pending-change-label">{safe_label}:</span> '
            f'<span class="pending-change-diff">{diff_html}</span>'
            f"</span>"
        )
    return (
        f'<div class="pending-change-field">'
        f'<div class="pending-change-label">{safe_label}:</div>'
        f'<div class="pending-change-diff">{diff_html}</div>'
        f"</div>"
    )


def format_changes_text_html(changes_text: str, *, compact: bool = False) -> str:
    text = str(changes_text or "").strip()
    prefix_initials, diff_text = _split_completed_prefix(text)
    parse_text = diff_text if prefix_initials else text
    blocks = _parse_change_blocks(parse_text)
    parts: list[str] = []
    if prefix_initials:
        prefix_html = f"{escape(prefix_initials)} -"
        if compact:
            parts.append(f'<span class="pending-change-completed-prefix">{prefix_html}</span> ')
        else:
            parts.append(f'<div class="pending-change-completed-prefix">{prefix_html}</div>')
    if not blocks:
        plain = _plain_changes_html(text)
        if plain:
            wrapper = "span" if compact else "div"
            parts.append(f'<{wrapper} class="pending-change-plain">{plain}</{wrapper}>')
        return "".join(parts)
    parts.extend(
        _format_field_change_html(label, original, edited, compact=compact)
        for label, original, edited in blocks
    )
    if compact and len(parts) > 1:
        return "".join(parts)
    return "".join(parts)


def _contact_edit_url_for_id(contact_id: int, *, return_to: str = "/changes-list") -> str:
    safe_return_to = return_to if return_to.startswith("/") else "/changes-list"
    return f"/contacts/{int(contact_id)}/edit?return_to={quote(safe_return_to, safe='')}"


def enrich_changes_entry_for_display(
    entry: dict,
    *,
    compact: bool = False,
    contact_id_lookup: dict[str, int] | None = None,
    contact_edit_url_for_id: Callable[[int], str] | None = None,
) -> dict:
    enriched = dict(entry)
    changes_text = str(enriched.get("changes_text") or "")
    enriched["changes_text_has_diff"] = changes_text_has_diff_format(changes_text)
    enriched["changes_text_html"] = format_changes_text_html(changes_text, compact=compact)
    if contact_id_lookup is not None:
        normalized_name = _normalize_changes_list_name(str(enriched.get("name_event") or ""))
        contact_id = contact_id_lookup.get(normalized_name)
        if contact_id:
            enriched["contact_id"] = contact_id
            if contact_edit_url_for_id is not None:
                enriched["contact_edit_url"] = contact_edit_url_for_id(contact_id)
            else:
                enriched["contact_edit_url"] = _contact_edit_url_for_id(contact_id)
    return enriched


def enrich_changes_entries_for_display(
    entries: list[dict],
    *,
    compact: bool = False,
    contact_edit_url_for_id: Callable[[int], str] | None = None,
) -> list[dict]:
    from app.services.contact_service import build_contact_id_lookup_by_formatted_name

    contact_id_lookup = build_contact_id_lookup_by_formatted_name()
    return [
        enrich_changes_entry_for_display(
            entry,
            compact=compact,
            contact_id_lookup=contact_id_lookup,
            contact_edit_url_for_id=contact_edit_url_for_id,
        )
        for entry in entries
    ]


def _normalize_changes_list_name(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def find_pending_photo_location_for_contact(contact_name: str) -> dict | None:
    normalized_target = _normalize_changes_list_name(contact_name)
    if not normalized_target:
        return None
    # Prefer the newest pending photo row for this contact.
    candidates: list[tuple[tuple[int, float], dict]] = []
    for entry in list_changes_entries():
        if str(entry.get("completed_by") or "").strip():
            continue
        entry_name = _normalize_changes_list_name(str(entry.get("name_event") or ""))
        if entry_name != normalized_target:
            continue
        blocks = _parse_change_blocks(_changes_text_for_diff(str(entry.get("changes_text") or "")))
        for label, _original, edited in blocks:
            if label.strip().lower() == "photo location" and edited.strip():
                candidates.append(
                    (
                        _automatic_date_sort_key(str(entry.get("automatic_date") or "")),
                        {
                            "entry_id": int(entry.get("id") or 0),
                            "edited": edited.strip(),
                            "automatic_date": str(entry.get("automatic_date") or ""),
                        },
                    )
                )
                break
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def pending_photo_path_is_available(photo_path: str) -> bool:
    from app.services.google_sync_service import _contact_photo_local_path

    local_path = _contact_photo_local_path(photo_path)
    return local_path is not None and local_path.is_file()


def export_changes_list_payload() -> list[dict]:
    return list_changes_entries()


def dedupe_local_changes_list_entries() -> bool:
    """Collapse completed/incomplete duplicates left by older Drive merges (e.g. CR vs LF)."""
    ensure_changes_list_entries()
    current = [entry for entry in list_changes_entries() if _entry_has_content(entry)]
    merged = merge_changes_list_entry_lists(current)
    if changes_list_entries_equal(merged, current):
        return False
    return replace_changes_list_from_payload(merged)


def replace_changes_list_from_payload(entries: object) -> bool:
    if not isinstance(entries, list):
        return False
    normalized_entries: list[dict] = []
    for index, item in enumerate(entries, start=1):
        if not isinstance(item, dict):
            continue
        entry = normalize_changes_list_entry(item, sort_order=index)
        if _entry_has_content(entry):
            normalized_entries.append(entry)
    # Newest mobile/desktop edits belong near the top (after the blank entry row).
    normalized_entries = sort_changes_list_entries_newest_first(normalized_entries)
    normalized_entries.insert(
        0,
        {
            "sort_order": 1,
            "automatic_date": "",
            "name_event": "",
            "changes_text": "",
            "completed_by": "",
            "completed_at": "",
        },
    )
    with get_connection() as conn:
        conn.execute("DELETE FROM changes_list_entries")
        for index, entry in enumerate(normalized_entries, start=1):
            conn.execute(
                """
                INSERT INTO changes_list_entries (
                  sort_order,
                  automatic_date,
                  name_event,
                  changes_text,
                  completed_by,
                  completed_at,
                  updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(entry["sort_order"] or index),
                    entry["automatic_date"],
                    entry["name_event"],
                    entry["changes_text"],
                    entry["completed_by"],
                    entry["completed_at"],
                    _now_text(),
                ),
            )
        conn.commit()
    return True


def entry_needs_mobile_drive_upload(entry: dict) -> bool:
    if not _entry_has_content(entry):
        return False
    return not str(entry.get("completed_by") or "").strip()


def reconcile_pending_edits_upload_flags(conn) -> None:
    has_uploadable_changes = any(
        entry_needs_mobile_drive_upload(dict(row))
        for row in conn.execute(
            """
            SELECT automatic_date, name_event, changes_text, completed_by
            FROM changes_list_entries
            """
        ).fetchall()
    )
    if not has_uploadable_changes:
        conn.execute(
            """
            UPDATE google_sync_state
            SET changes_list_needs_drive_export = 0, updated_at = ?
            WHERE id = 1
            """,
            (_now_text(),),
        )

    for row in conn.execute(
        "SELECT id, photo FROM contacts WHERE photo_needs_export = 1"
    ).fetchall():
        if not pending_photo_path_is_available(str(row["photo"] or "")):
            conn.execute(
                "UPDATE contacts SET photo_needs_export = 0 WHERE id = ?",
                (int(row["id"]),),
            )


def pending_edits_has_drive_upload(*, conn=None) -> bool:
    def _evaluate(connection) -> bool:
        reconcile_pending_edits_upload_flags(connection)
        state = dict(
            connection.execute(
                "SELECT changes_list_needs_drive_export FROM google_sync_state WHERE id = 1"
            ).fetchone()
            or {}
        )
        if bool(state.get("changes_list_needs_drive_export")):
            return True
        for row in connection.execute(
            "SELECT photo FROM contacts WHERE photo_needs_export = 1"
        ).fetchall():
            if pending_photo_path_is_available(str(row["photo"] or "")):
                return True
        return False

    if conn is not None:
        return _evaluate(conn)
    with get_connection() as managed_conn:
        result = _evaluate(managed_conn)
        managed_conn.commit()
        return result
