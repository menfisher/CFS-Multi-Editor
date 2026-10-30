from __future__ import annotations

from app.database import fetch_all, fetch_one, get_connection
from app.services.meeting_v2_service import DEFAULT_BOOK_PRESET, ensure_default_book_layout_preset


FONT_FAMILY_OPTIONS = [
    "Arial",
    "Helvetica",
    "Verdana",
    "Tahoma",
    "Times New Roman",
    "Georgia",
    "Courier New",
]

DEFAULT_SHARED_CONTACTS_GROUP_NAME = "TriState Separates (Shared)"


def _log_preset_service(message: str) -> None:
    return None


def _coerce_float(value, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _coerce_int(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _metrics_for_preset(preset: dict) -> dict:
    printable_width = max(
        0.1,
        float(preset["trim_width_in"]) - float(preset["margin_left_in"]) - float(preset["margin_right_in"]),
    )
    printable_height = max(
        0.1,
        float(preset["trim_height_in"]) - float(preset["margin_top_in"]) - float(preset["margin_bottom_in"]),
    )
    column_count = max(1, int(preset["meeting_table_column_count"]))
    column_gap_px = max(0, int(preset["meeting_table_column_gap_px"]))
    preview_width_px = printable_width * float(preset["screen_preview_scale"])
    total_gap_px = column_gap_px * max(0, column_count - 1)
    approx_column_width_px = max(1.0, (preview_width_px - total_gap_px) / column_count)
    approx_column_width_in = printable_width / column_count
    return {
        "printable_width_in": printable_width,
        "printable_height_in": printable_height,
        "preview_width_px": preview_width_px,
        "approx_column_width_px": approx_column_width_px,
        "approx_column_width_in": approx_column_width_in,
    }


def _default_preset_payload() -> dict:
    return {
        "id": None,
        "name": DEFAULT_BOOK_PRESET["name"],
        "book_title": "Address Book",
        "trim_width_in": DEFAULT_BOOK_PRESET["trim_width_in"],
        "trim_height_in": DEFAULT_BOOK_PRESET["trim_height_in"],
        "margin_left_in": DEFAULT_BOOK_PRESET["margin_left_in"],
        "margin_right_in": DEFAULT_BOOK_PRESET["margin_right_in"],
        "margin_top_in": DEFAULT_BOOK_PRESET["margin_top_in"],
        "margin_bottom_in": DEFAULT_BOOK_PRESET["margin_bottom_in"],
        "font_family": "Arial",
        "base_font_size_pt": DEFAULT_BOOK_PRESET["base_font_size_pt"],
        "title_font_size_pt": DEFAULT_BOOK_PRESET["title_font_size_pt"],
        "toc_font_size_pt": DEFAULT_BOOK_PRESET["toc_font_size_pt"],
        "index_font_size_pt": DEFAULT_BOOK_PRESET["index_font_size_pt"],
        "line_height": DEFAULT_BOOK_PRESET["line_height"],
        "meeting_table_column_count": DEFAULT_BOOK_PRESET["meeting_table_column_count"],
        "meeting_table_column_gap_px": DEFAULT_BOOK_PRESET["meeting_table_column_gap_px"],
        "screen_preview_scale": DEFAULT_BOOK_PRESET["screen_preview_scale"],
        "meetingdata_title_bar_color": DEFAULT_BOOK_PRESET["meetingdata_title_bar_color"],
        "meetingdata_primary_row_color": DEFAULT_BOOK_PRESET["meetingdata_primary_row_color"],
        "meetingdata_secondary_row_color": DEFAULT_BOOK_PRESET["meetingdata_secondary_row_color"],
        "meetingdata_highlight_row_color": DEFAULT_BOOK_PRESET["meetingdata_highlight_row_color"],
        "contacts_title_bar_color": DEFAULT_BOOK_PRESET["meetingdata_title_bar_color"],
        "contacts_primary_row_color": DEFAULT_BOOK_PRESET["meetingdata_primary_row_color"],
        "contacts_secondary_row_color": DEFAULT_BOOK_PRESET["meetingdata_secondary_row_color"],
        "contacts_highlight_row_color": DEFAULT_BOOK_PRESET["meetingdata_highlight_row_color"],
        "shared_contacts_group_name": DEFAULT_BOOK_PRESET["shared_contacts_group_name"],
        "is_default": 1,
    }


def _normalize_preset_row(row) -> dict:
    preset = dict(row)
    preset.setdefault("book_title", "Address Book")
    preset.setdefault("font_family", "Arial")
    preset.setdefault("meetingdata_title_bar_color", DEFAULT_BOOK_PRESET["meetingdata_title_bar_color"])
    preset.setdefault("meetingdata_primary_row_color", DEFAULT_BOOK_PRESET["meetingdata_primary_row_color"])
    preset.setdefault("meetingdata_secondary_row_color", DEFAULT_BOOK_PRESET["meetingdata_secondary_row_color"])
    preset.setdefault("meetingdata_highlight_row_color", DEFAULT_BOOK_PRESET["meetingdata_highlight_row_color"])
    preset.setdefault("contacts_title_bar_color", DEFAULT_BOOK_PRESET["meetingdata_title_bar_color"])
    preset.setdefault("contacts_primary_row_color", DEFAULT_BOOK_PRESET["meetingdata_primary_row_color"])
    preset.setdefault("contacts_secondary_row_color", DEFAULT_BOOK_PRESET["meetingdata_secondary_row_color"])
    preset.setdefault("contacts_highlight_row_color", DEFAULT_BOOK_PRESET["meetingdata_highlight_row_color"])
    preset.setdefault("shared_contacts_group_name", DEFAULT_SHARED_CONTACTS_GROUP_NAME)
    preset.setdefault("title_font_size_pt", DEFAULT_BOOK_PRESET["title_font_size_pt"])
    preset.setdefault("toc_font_size_pt", DEFAULT_BOOK_PRESET["toc_font_size_pt"])
    preset.setdefault("index_font_size_pt", DEFAULT_BOOK_PRESET["index_font_size_pt"])
    try:
        index_font_size = float(preset.get("index_font_size_pt") or 0)
    except (TypeError, ValueError):
        index_font_size = 0.0
    if index_font_size <= 0:
        preset["index_font_size_pt"] = preset.get("toc_font_size_pt") or DEFAULT_BOOK_PRESET["index_font_size_pt"]
    preset["metrics"] = _metrics_for_preset(preset)
    return preset


def _shared_group_name_for_save(preset_id: int | None, values: dict) -> str:
    """Keep the renamed catch-all group unless this save explicitly sets a new one.

    Address book and layout saves do not post the Settings field. Filling the
    missing value with the original TriState name was wiping a saved rename.
    """
    if "shared_contacts_group_name" in values:
        explicit_name = str(values.get("shared_contacts_group_name") or "").strip()
        if explicit_name:
            return explicit_name
    if preset_id:
        row = fetch_one(
            """
            SELECT shared_contacts_group_name
            FROM book_layout_presets
            WHERE id = ?
            """,
            (int(preset_id),),
        )
        existing_name = str((row or {}).get("shared_contacts_group_name") or "").strip()
        if existing_name:
            return existing_name
    return DEFAULT_SHARED_CONTACTS_GROUP_NAME


def get_shared_contacts_group_name() -> str:
    ensure_default_book_layout_preset()
    row = fetch_one(
        """
        SELECT shared_contacts_group_name
        FROM book_layout_presets
        WHERE is_default = 1
        ORDER BY id
        LIMIT 1
        """
    )
    name = str((row or {}).get("shared_contacts_group_name") or "").strip()
    return name or DEFAULT_SHARED_CONTACTS_GROUP_NAME


def list_book_layout_presets() -> list[dict]:
    _log_preset_service("list presets start")
    ensure_default_book_layout_preset()
    _log_preset_service("default preset ensured")
    rows = fetch_all(
        """
        SELECT *
        FROM book_layout_presets
        ORDER BY is_default DESC, name, id
        """
    )
    _log_preset_service(f"list presets rows count={len(rows)}")
    presets = [_normalize_preset_row(row) for row in rows]
    _log_preset_service("list presets complete")
    return presets


def get_book_layout_preset_editor(preset_id: int | None = None, new_preset: bool = False) -> dict:
    _log_preset_service(f"editor context start preset_id={preset_id} new={new_preset}")
    presets = list_book_layout_presets()
    _log_preset_service(f"editor context presets count={len(presets)}")
    selected = None
    if not new_preset and preset_id is not None:
        for preset in presets:
            if int(preset["id"]) == int(preset_id):
                selected = preset
                break
    if not new_preset and not selected and presets:
        selected = presets[0]
    if new_preset or not selected:
        selected = _default_preset_payload()
        selected["metrics"] = _metrics_for_preset(selected)
        selected["is_default"] = 0

    result = {
        "presets": presets,
        "selected_preset": selected,
        "font_families": FONT_FAMILY_OPTIONS,
        "is_new_preset": new_preset,
        "can_delete_selected_preset": bool(selected.get("id")) and len(presets) > 1,
    }
    _log_preset_service("editor context complete")
    return result


def save_book_layout_preset(preset_id: int | None, values: dict) -> int:
    ensure_default_book_layout_preset()
    name = str(values.get("name") or "").strip() or "Untitled Preset"
    book_title = str(values.get("book_title") or "").strip() or "Address Book"
    font_family = str(values.get("font_family") or "Arial").strip() or "Arial"
    if font_family not in FONT_FAMILY_OPTIONS:
        font_family = "Arial"

    trim_width_in = max(0.1, _coerce_float(values.get("trim_width_in"), 3.5))
    trim_height_in = max(0.1, _coerce_float(values.get("trim_height_in"), 5.0))
    margin_left_in = max(0.0, _coerce_float(values.get("margin_left_in"), 0.14))
    margin_right_in = max(0.0, _coerce_float(values.get("margin_right_in"), 0.14))
    margin_top_in = max(0.0, _coerce_float(values.get("margin_top_in"), 0.14))
    margin_bottom_in = max(0.0, _coerce_float(values.get("margin_bottom_in"), 0.14))
    existing_preset = None
    if preset_id:
        existing_preset = fetch_one("SELECT * FROM book_layout_presets WHERE id = ?", (int(preset_id),))
    base_font_size_pt = max(1.0, _coerce_float(values.get("base_font_size_pt"), 7.0))
    title_font_raw = values.get("title_font_size_pt")
    if title_font_raw is None or str(title_font_raw).strip() == "":
        title_font_size_pt = max(
            1.0,
            _coerce_float((existing_preset or {}).get("title_font_size_pt"), DEFAULT_BOOK_PRESET["title_font_size_pt"]),
        )
    else:
        title_font_size_pt = max(1.0, _coerce_float(title_font_raw, DEFAULT_BOOK_PRESET["title_font_size_pt"]))
    toc_font_raw = values.get("toc_font_size_pt")
    if toc_font_raw is None or str(toc_font_raw).strip() == "":
        toc_font_size_pt = max(
            1.0,
            _coerce_float((existing_preset or {}).get("toc_font_size_pt"), DEFAULT_BOOK_PRESET["toc_font_size_pt"]),
        )
    else:
        toc_font_size_pt = max(1.0, _coerce_float(toc_font_raw, DEFAULT_BOOK_PRESET["toc_font_size_pt"]))
    index_font_raw = values.get("index_font_size_pt")
    if index_font_raw is None or str(index_font_raw).strip() == "":
        index_font_size_pt = max(
            1.0,
            _coerce_float(
                (existing_preset or {}).get("index_font_size_pt"),
                _coerce_float(
                    (existing_preset or {}).get("toc_font_size_pt"),
                    DEFAULT_BOOK_PRESET["index_font_size_pt"],
                ),
            ),
        )
    else:
        index_font_size_pt = max(1.0, _coerce_float(index_font_raw, DEFAULT_BOOK_PRESET["index_font_size_pt"]))
    line_height = max(0.5, _coerce_float(values.get("line_height"), 1.2))
    column_count = max(8, _coerce_int(values.get("meeting_table_column_count"), 28))
    column_gap_px = max(0, _coerce_int(values.get("meeting_table_column_gap_px"), 6))
    screen_preview_scale = max(40.0, _coerce_float(values.get("screen_preview_scale"), 220.0))
    title_bar_color = str(values.get("meetingdata_title_bar_color") or DEFAULT_BOOK_PRESET["meetingdata_title_bar_color"]).strip() or DEFAULT_BOOK_PRESET["meetingdata_title_bar_color"]
    primary_row_color = str(values.get("meetingdata_primary_row_color") or DEFAULT_BOOK_PRESET["meetingdata_primary_row_color"]).strip() or DEFAULT_BOOK_PRESET["meetingdata_primary_row_color"]
    secondary_row_color = str(values.get("meetingdata_secondary_row_color") or DEFAULT_BOOK_PRESET["meetingdata_secondary_row_color"]).strip() or DEFAULT_BOOK_PRESET["meetingdata_secondary_row_color"]
    highlight_row_color = str(values.get("meetingdata_highlight_row_color") or DEFAULT_BOOK_PRESET["meetingdata_highlight_row_color"]).strip() or DEFAULT_BOOK_PRESET["meetingdata_highlight_row_color"]
    contacts_title_bar_color = str(values.get("contacts_title_bar_color") or DEFAULT_BOOK_PRESET["meetingdata_title_bar_color"]).strip() or DEFAULT_BOOK_PRESET["meetingdata_title_bar_color"]
    contacts_primary_row_color = str(values.get("contacts_primary_row_color") or DEFAULT_BOOK_PRESET["meetingdata_primary_row_color"]).strip() or DEFAULT_BOOK_PRESET["meetingdata_primary_row_color"]
    contacts_secondary_row_color = str(values.get("contacts_secondary_row_color") or DEFAULT_BOOK_PRESET["meetingdata_secondary_row_color"]).strip() or DEFAULT_BOOK_PRESET["meetingdata_secondary_row_color"]
    contacts_highlight_row_color = str(values.get("contacts_highlight_row_color") or DEFAULT_BOOK_PRESET["meetingdata_highlight_row_color"]).strip() or DEFAULT_BOOK_PRESET["meetingdata_highlight_row_color"]
    shared_contacts_group_name = _shared_group_name_for_save(preset_id, values)
    apply_shared_group_name_to_all_presets = "shared_contacts_group_name" in values and bool(str(values.get("shared_contacts_group_name") or "").strip())
    is_default = 1 if str(values.get("is_default") or "") in {"1", "true", "on", "yes"} else 0

    with get_connection() as conn:
        if is_default:
            conn.execute("UPDATE book_layout_presets SET is_default = 0")

        params = (
            name,
            book_title,
            trim_width_in,
            trim_height_in,
            margin_left_in,
            margin_right_in,
            margin_top_in,
            margin_bottom_in,
            font_family,
            base_font_size_pt,
            title_font_size_pt,
            toc_font_size_pt,
            index_font_size_pt,
            line_height,
            column_count,
            column_gap_px,
            screen_preview_scale,
            title_bar_color,
            primary_row_color,
            secondary_row_color,
            highlight_row_color,
            contacts_title_bar_color,
            contacts_primary_row_color,
            contacts_secondary_row_color,
            contacts_highlight_row_color,
            shared_contacts_group_name,
            is_default,
        )

        update_sql = """
            UPDATE book_layout_presets
            SET
              name = ?,
              book_title = ?,
              trim_width_in = ?,
              trim_height_in = ?,
              margin_left_in = ?,
              margin_right_in = ?,
              margin_top_in = ?,
              margin_bottom_in = ?,
              font_family = ?,
              base_font_size_pt = ?,
              title_font_size_pt = ?,
              toc_font_size_pt = ?,
              index_font_size_pt = ?,
              line_height = ?,
              meeting_table_column_count = ?,
              meeting_table_column_gap_px = ?,
              screen_preview_scale = ?,
              meetingdata_title_bar_color = ?,
              meetingdata_primary_row_color = ?,
              meetingdata_secondary_row_color = ?,
              meetingdata_highlight_row_color = ?,
              contacts_title_bar_color = ?,
              contacts_primary_row_color = ?,
              contacts_secondary_row_color = ?,
              contacts_highlight_row_color = ?,
              shared_contacts_group_name = ?,
              is_default = ?
            WHERE id = ?
            """

        if preset_id:
            conn.execute(
                update_sql,
                params + (int(preset_id),),
            )
            saved_id = int(preset_id)
        else:
            existing = conn.execute(
                """
                SELECT id
                FROM book_layout_presets
                WHERE name = ?
                """,
                (name,),
            ).fetchone()
            if existing:
                saved_id = int(existing["id"])
                conn.execute(update_sql, params + (saved_id,))
                if apply_shared_group_name_to_all_presets:
                    conn.execute(
                        "UPDATE book_layout_presets SET shared_contacts_group_name = ?",
                        (shared_contacts_group_name,),
                    )
                conn.commit()
                from app.services.meeting_v2_service import refresh_book_layout_preset_usage

                refresh_book_layout_preset_usage(saved_id)
                return saved_id

            cursor = conn.execute(
                """
                INSERT INTO book_layout_presets (
                  name,
                  book_title,
                  trim_width_in,
                  trim_height_in,
                  margin_left_in,
                  margin_right_in,
                  margin_top_in,
                  margin_bottom_in,
                  font_family,
                  base_font_size_pt,
                  title_font_size_pt,
                  toc_font_size_pt,
                  index_font_size_pt,
                  line_height,
                  meeting_table_column_count,
                  meeting_table_column_gap_px,
                  screen_preview_scale,
                  meetingdata_title_bar_color,
                  meetingdata_primary_row_color,
                  meetingdata_secondary_row_color,
                  meetingdata_highlight_row_color,
                  contacts_title_bar_color,
                  contacts_primary_row_color,
                  contacts_secondary_row_color,
                  contacts_highlight_row_color,
                  shared_contacts_group_name,
                  is_default
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                params,
            )
            saved_id = int(cursor.lastrowid)

        if apply_shared_group_name_to_all_presets:
            conn.execute(
                "UPDATE book_layout_presets SET shared_contacts_group_name = ?",
                (shared_contacts_group_name,),
            )
        conn.commit()
    from app.services.meeting_v2_service import refresh_book_layout_preset_usage

    refresh_book_layout_preset_usage(saved_id)
    return saved_id


def duplicate_book_layout_preset(preset_id: int) -> int:
    preset = fetch_one("SELECT * FROM book_layout_presets WHERE id = ?", (preset_id,))
    if not preset:
        return ensure_default_book_layout_preset()
    preset = dict(preset)
    base_name = str(preset["name"] or "Preset").strip() or "Preset"
    copy_name = f"{base_name} Copy"
    existing_names = {row["name"] for row in fetch_all("SELECT name FROM book_layout_presets")}
    counter = 2
    while copy_name in existing_names:
        copy_name = f"{base_name} Copy {counter}"
        counter += 1
    preset["name"] = copy_name
    preset["is_default"] = 0
    preset["id"] = None
    return save_book_layout_preset(None, preset)


def set_default_book_layout_preset(preset_id: int) -> int:
    with get_connection() as conn:
        conn.execute("UPDATE book_layout_presets SET is_default = 0")
        conn.execute(
            "UPDATE book_layout_presets SET is_default = 1 WHERE id = ?",
            (preset_id,),
        )
        conn.commit()
    return preset_id


def save_book_layout_grid_settings(
    preset_id: int | None,
    *,
    meeting_table_column_count,
    meeting_table_column_gap_px,
    screen_preview_scale,
) -> bool:
    if not preset_id:
        return False
    column_count = max(8, _coerce_int(meeting_table_column_count, 28))
    column_gap_px = max(0, _coerce_int(meeting_table_column_gap_px, 6))
    preview_scale = max(40.0, _coerce_float(screen_preview_scale, 220.0))
    with get_connection() as conn:
        existing = conn.execute(
            """
            SELECT meeting_table_column_count, meeting_table_column_gap_px, screen_preview_scale
            FROM book_layout_presets
            WHERE id = ?
            """,
            (int(preset_id),),
        ).fetchone()
        changed = bool(
            existing
            and (
                int(existing["meeting_table_column_count"] or 0) != column_count
                or int(existing["meeting_table_column_gap_px"] or 0) != column_gap_px
                or float(existing["screen_preview_scale"] or 0) != preview_scale
            )
        )
        conn.execute(
            """
            UPDATE book_layout_presets
            SET
              meeting_table_column_count = ?,
              meeting_table_column_gap_px = ?,
              screen_preview_scale = ?
            WHERE id = ?
            """,
            (column_count, column_gap_px, preview_scale, int(preset_id)),
        )
        conn.commit()
    from app.services.meeting_v2_service import refresh_book_layout_preset_usage

    refresh_book_layout_preset_usage(int(preset_id))
    return changed


def delete_book_layout_preset(preset_id: int) -> int:
    ensure_default_book_layout_preset()
    preset = fetch_one("SELECT * FROM book_layout_presets WHERE id = ?", (preset_id,))
    if not preset:
        return ensure_default_book_layout_preset()

    other_presets = fetch_all(
        """
        SELECT id, is_default
        FROM book_layout_presets
        WHERE id <> ?
        ORDER BY is_default DESC, id
        """,
        (preset_id,),
    )
    if not other_presets:
        return int(preset_id)

    fallback_preset_id = int(other_presets[0]["id"])

    with get_connection() as conn:
        conn.execute(
            """
            UPDATE meeting_section_layout_v2
            SET book_layout_preset_id = ?
            WHERE book_layout_preset_id = ?
            """,
            (fallback_preset_id, preset_id),
        )
        conn.execute(
            "DELETE FROM book_layout_presets WHERE id = ?",
            (preset_id,),
        )
        if int(preset["is_default"] or 0):
            conn.execute("UPDATE book_layout_presets SET is_default = 0")
            conn.execute(
                "UPDATE book_layout_presets SET is_default = 1 WHERE id = ?",
                (fallback_preset_id,),
            )
        conn.commit()

    return fallback_preset_id
