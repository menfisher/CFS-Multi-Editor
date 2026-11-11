from __future__ import annotations

import calendar
import re
import sqlite3
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher
from html import escape, unescape
from html.parser import HTMLParser
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.database import fetch_all, fetch_one, get_connection
from app.services.contact_service import get_meeting_sections, is_assignment_menu_sentinel
from app.services.meeting_editor_model import (
    build_editor_section_from_legacy_section,
    editor_section_to_dict,
)
from app.services.print_order_service import (
    order_field_names,
    order_meeting_names,
    order_meetings_by_field,
    print_order_match_key,
    print_order_lookup,
)


MEETING_FONT_SIZE_OPTIONS = [
    "5.0",
    "5.5",
    "6.0",
    "6.5",
    "7.0",
    "7.5",
    "8.0",
    "8.5",
    "9.0",
    "9.5",
    "10.0",
    "10.5",
    "11.0",
    "11.5",
    "12.0",
]


DEFAULT_BOOK_PRESET = {
    "name": "Pocket Address Book",
    "book_title": "Address Book",
    "trim_width_in": 3.5,
    "trim_height_in": 5.0,
    "margin_left_in": 0.14,
    "margin_right_in": 0.14,
    "margin_top_in": 0.14,
    "margin_bottom_in": 0.14,
    "font_family": "Arial",
    "base_font_size_pt": 7.0,
    "title_font_size_pt": 12.0,
    "toc_font_size_pt": 10.0,
    "index_font_size_pt": 10.0,
    "line_height": 1.2,
    "meeting_table_column_count": 28,
    "meeting_table_column_gap_px": 6,
    "screen_preview_scale": 220.0,
    "meetingdata_title_bar_color": "#e3eff3",
    "meetingdata_primary_row_color": "#f8f3e3",
    "meetingdata_secondary_row_color": "#edf5f7",
    "meetingdata_highlight_row_color": "#ffe3a1",
    "shared_contacts_group_name": "TriState Separates (Shared)",
    "is_default": 1,
}

ROW_KIND_LAYOUT_TAGS = ("content", "blank", "divider", "column_flow")
ROW_KIND_MODIFIER_TAGS = ("underlined", "table_start", "table_end")
ROW_KIND_STORAGE_ORDER = (
    "content",
    "blank",
    "divider",
    "column_flow",
    "underlined",
    "table_start",
    "table_end",
)
ROW_KIND_DISPLAY_LABELS = {
    "content": "content",
    "blank": "blank",
    "divider": "divider",
    "column_flow": "column flow",
    "underlined": "underlined",
    "table_start": "table start",
    "table_end": "table end",
}

EDIT_LOG_DISPLAY_TIMEZONE = ZoneInfo("America/Chicago")
EDIT_LOG_RETENTION_MONTHS = 6


def _log_meeting_service(message: str) -> None:
    return None


def _split_row_kind_tags(raw_value) -> list[str]:
    if isinstance(raw_value, (list, tuple)):
        parts = [str(item or "").strip().lower() for item in raw_value]
    else:
        normalized = str(raw_value or "").replace(",", "|")
        parts = [part.strip().lower() for part in normalized.split("|")]

    tags = []
    for part in parts:
        if not part:
            continue
        if part == "group":
            part = "table_start"
        if part in ROW_KIND_LAYOUT_TAGS or part in ROW_KIND_MODIFIER_TAGS:
            if part not in tags:
                tags.append(part)
    return tags


def _primary_row_kind(tags: list[str]) -> str:
    for tag in tags:
        if tag in ROW_KIND_LAYOUT_TAGS:
            return tag
    return "content"


def _normalize_row_kind_value(raw_value) -> str:
    tags = _split_row_kind_tags(raw_value)
    primary = _primary_row_kind(tags)
    normalized = [primary]
    for tag in tags:
        if tag in ROW_KIND_MODIFIER_TAGS and tag not in normalized:
            normalized.append(tag)
    return "|".join(normalized)


def _row_has_tag(raw_value, tag: str) -> bool:
    return tag in _split_row_kind_tags(raw_value)


def _decorate_row_item(row_item: dict) -> dict:
    tags = _split_row_kind_tags(row_item.get("row_kind"))
    primary = _primary_row_kind(tags)
    row_item["row_kind"] = _normalize_row_kind_value(tags)
    row_item["row_kind_tags"] = tags if tags else [primary]
    row_item["row_kind_primary"] = primary
    row_item["row_kind_labels"] = [ROW_KIND_DISPLAY_LABELS.get(tag, tag) for tag in row_item["row_kind_tags"]]
    row_item["is_blank_row"] = primary == "blank"
    row_item["is_divider_row"] = primary == "divider"
    row_item["is_column_flow_row"] = primary == "column_flow"
    row_item["is_underlined_row"] = "underlined" in row_item["row_kind_tags"]
    row_item["is_table_start_row"] = "table_start" in row_item["row_kind_tags"]
    row_item["is_table_end_row"] = "table_end" in row_item["row_kind_tags"]
    row_item["group_with_table_below"] = bool(int(row_item.get("group_with_table_below") or 0)) and row_item["is_table_start_row"]
    row_item["no_meeting_association"] = bool(int(row_item.get("no_meeting_association") or 0)) and row_item["is_table_start_row"]
    row_item["print_new_page"] = bool(int(row_item.get("print_new_page") or 0)) and row_item["is_table_start_row"]
    return row_item


def _parse_font_size_code(raw_value) -> float | None:
    value = str(raw_value or "").strip().lower()
    if not value:
        return None
    if value.endswith("pt"):
        value = value[:-2].strip()
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return round(parsed, 1)


def _field_list_bible_study_base_font_size_pt() -> float:
    try:
        row = fetch_one("SELECT bible_study_font_size_pt FROM address_book_settings WHERE id = 1")
    except Exception:
        return 11.0
    parsed = _parse_font_size_code(row["bible_study_font_size_pt"] if row else None)
    return parsed if parsed is not None else 11.0


def _apply_row_font_size_overrides(
    rows: list[dict],
    base_font_size_pt: float,
) -> None:
    inherited_table_font_size_pt: float | None = None
    inside_table = False

    for row in rows:
        override_font_size_pt = _parse_font_size_code(row.get("format_code"))
        if row.get("is_table_start_row"):
            inside_table = True
            inherited_table_font_size_pt = override_font_size_pt

        effective_font_size_pt = (
            override_font_size_pt
            if override_font_size_pt is not None
            else inherited_table_font_size_pt
            if inside_table and inherited_table_font_size_pt is not None
            else base_font_size_pt
        )
        row["font_size_override_pt"] = override_font_size_pt
        row["effective_font_size_pt"] = round(float(effective_font_size_pt), 1)
        row["font_size_input_value"] = f"{override_font_size_pt:.1f}" if override_font_size_pt is not None else ""

        if row.get("is_table_end_row"):
            inside_table = False
            inherited_table_font_size_pt = None


def _build_meeting_print_blocks(rows: list[dict]) -> list[dict]:
    blocks: list[dict] = []
    current_table_rows: list[dict] = []

    def flush_table() -> None:
        nonlocal current_table_rows
        if not current_table_rows:
            return
        start_row = current_table_rows[0]
        blocks.append(
            {
                "kind": "table",
                "rows": current_table_rows,
                "group_with_next": bool(start_row.get("group_with_table_below")),
                "print_new_page": bool(start_row.get("print_new_page")),
            }
        )
        current_table_rows = []

    for row in rows:
        if row.get("is_table_start_row"):
            flush_table()
            current_table_rows = [row]
            if row.get("is_table_end_row"):
                flush_table()
            continue

        if current_table_rows:
            current_table_rows.append(row)
            if row.get("is_table_end_row"):
                flush_table()
            continue

        blocks.append(
            {
                "kind": "row",
                "rows": [row],
                "group_with_next": False,
                "print_new_page": False,
            }
        )

    flush_table()

    preview_blocks: list[dict] = []
    index = 0
    while index < len(blocks):
        block = blocks[index]
        next_block = blocks[index + 1] if index + 1 < len(blocks) else None
        if (
            block["kind"] == "table"
            and block.get("group_with_next")
            and next_block
            and next_block["kind"] == "table"
        ):
            preview_blocks.append(
                {
                    "kind": "paired_tables",
                    "items": [block, next_block],
                    "keep_together": True,
                    "print_new_page": bool(block.get("print_new_page")),
                }
            )
            index += 2
            continue

        preview_blocks.append(
            {
                "kind": "single",
                "items": [block],
                "keep_together": block["kind"] == "table",
                "print_new_page": bool(block.get("print_new_page")),
            }
        )
        index += 1

    return preview_blocks


def _inline_style_map(style: str) -> dict[str, str]:
    mapped: dict[str, str] = {}
    for part in str(style or "").split(";"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        mapped[key.strip().lower()] = value.strip().lower()
    return mapped


def _style_is_italic(style: str) -> bool:
    value = _inline_style_map(style).get("font-style", "")
    return value.startswith("italic") or value.startswith("oblique")


def _style_is_bold(style: str) -> bool:
    value = _inline_style_map(style).get("font-weight", "")
    if value in {"bold", "bolder"}:
        return True
    try:
        return int(value) >= 600
    except ValueError:
        return False


def _style_is_underline(style: str) -> bool:
    mapped = _inline_style_map(style)
    value = mapped.get("text-decoration") or mapped.get("text-decoration-line") or ""
    return "underline" in value


class _InlineMarkupSanitizer(HTMLParser):
    _ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "br"}
    _BLOCK_TAGS = {"div", "p"}
    _STYLE_TAGS = {"span", "font"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._style_stack: list[list[str]] = []

    def _open_style_tags(self, style: str) -> list[str]:
        opened: list[str] = []
        if _style_is_bold(style):
            self.parts.append("<strong>")
            opened.append("strong")
        if _style_is_italic(style):
            self.parts.append("<em>")
            opened.append("em")
        if _style_is_underline(style):
            self.parts.append("<u>")
            opened.append("u")
        return opened

    def handle_starttag(self, tag: str, attrs) -> None:
        normalized = tag.lower()
        attrs_dict = {str(key).lower(): str(value or "") for key, value in attrs}
        if normalized in self._ALLOWED_TAGS:
            self.parts.append("<br>" if normalized == "br" else f"<{normalized}>")
        elif normalized in self._STYLE_TAGS:
            self._style_stack.append(self._open_style_tags(attrs_dict.get("style", "")))
        elif normalized in self._BLOCK_TAGS and self.parts:
            self.parts.append("<br>")

    def handle_startendtag(self, tag: str, attrs) -> None:
        normalized = tag.lower()
        if normalized == "br":
            self.parts.append("<br>")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in {"b", "strong", "i", "em", "u"}:
            self.parts.append(f"</{normalized}>")
        elif normalized in self._STYLE_TAGS:
            opened = self._style_stack.pop() if self._style_stack else []
            for name in reversed(opened):
                self.parts.append(f"</{name}>")
        elif normalized in self._BLOCK_TAGS and self.parts and self.parts[-1] != "<br>":
            self.parts.append("<br>")

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(escape(data))

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def get_html(self, preserve_edge_breaks: bool = False) -> str:
        html = "".join(self.parts)
        if not preserve_edge_breaks:
            while html.startswith("<br>"):
                html = html[4:]
            while html.endswith("<br>"):
                html = html[:-4]
        return html


def sanitize_inline_markup(value: str, preserve_edge_breaks: bool = False) -> str:
    raw = str(value or "")
    if not raw.strip():
        return ""
    sanitizer = _InlineMarkupSanitizer()
    sanitizer.feed(raw)
    sanitizer.close()
    return sanitizer.get_html(preserve_edge_breaks=preserve_edge_breaks)


def _flow_editor_html(value: str) -> str:
    normalized = str(value or "")
    if not normalized:
        return ""
    lines = normalized.split("<br>")
    rendered_lines = [f"<div>{line or '<br>'}</div>" for line in lines]
    return "".join(rendered_lines)


def _flow_markup_line_count(value: str) -> int:
    normalized = sanitize_inline_markup(value, preserve_edge_breaks=True)
    if not normalized:
        return 1
    parts = re.split(r"<br\s*/?>", normalized, flags=re.IGNORECASE)
    return max(1, len(parts))


def _flow_editor_html_from_markup(value: str) -> str:
    normalized = sanitize_inline_markup(value, preserve_edge_breaks=True)
    if not normalized:
        return ""
    parts = re.split(r"<br\s*/?>", normalized, flags=re.IGNORECASE)
    rendered_lines = [f"<div>{part or '<br>'}</div>" for part in parts]
    return "".join(rendered_lines)


def _flow_cell_order_clause() -> str:
    return "col_start, CASE WHEN stack_under_cell_id IS NULL OR stack_under_cell_id = 0 THEN 0 ELSE 1 END, id"


def _partition_flow_layout_cells(cells: list[dict]) -> tuple[list[dict], dict[int, list[dict]]]:
    primaries: list[dict] = []
    stacked_by_parent: dict[int, list[dict]] = {}
    for cell in cells:
        parent_id = int(cell.get("stack_under_cell_id") or 0)
        if parent_id:
            stacked_by_parent.setdefault(parent_id, []).append(cell)
        else:
            primaries.append(cell)
    return primaries, stacked_by_parent


def _stacked_flow_column_markup(cell: dict) -> str:
    raw = str(cell.get("text_value") or "")
    return sanitize_inline_markup(raw, preserve_edge_breaks=True)


def _flow_column_from_cell(
    *,
    column_index: int,
    layout_cell: dict | None,
    items: list[dict],
    combined_text_override: str | None = None,
) -> dict:
    if combined_text_override is not None:
        combined_text = combined_text_override
        has_item = bool(combined_text)
        editor_html = _flow_editor_html_from_markup(combined_text)
        content_lines = max(2, _flow_markup_line_count(combined_text)) if combined_text else 2
    else:
        combined_text = "\n".join(
            str(item.get("text_value") or "")
            for item in items
            if str(item.get("text_value") or "").strip()
        )
        has_item = bool(items)
        editor_html = _flow_editor_html(combined_text)
        content_lines = max(2, combined_text.count("\n") + 1) if combined_text else 2
    flow_min_lines = 0
    if layout_cell:
        try:
            flow_min_lines = int(layout_cell.get("flow_min_lines") or 0)
        except (TypeError, ValueError):
            flow_min_lines = 0
    min_lines = max(flow_min_lines, content_lines) if flow_min_lines > 0 else content_lines
    return {
        "column_index": column_index,
        "layout_cell": layout_cell,
        "col_start": int(layout_cell["col_start"]) if layout_cell else 1,
        "col_span": int(layout_cell["col_span"]) if layout_cell else 1,
        "items": items,
        "combined_text": combined_text,
        "editor_html": editor_html,
        "text_rows": min_lines,
        "flow_min_lines": flow_min_lines,
        "has_item": has_item,
        "primary_item_id": int(items[0]["id"]) if items else None,
    }


def _group_flow_column_slots(flow_columns: list[dict]) -> list[dict]:
    if not flow_columns:
        return []
    primaries: list[dict] = []
    stacked_by_parent: dict[int, list[dict]] = {}
    for column in flow_columns:
        layout_cell = column.get("layout_cell") or {}
        parent_id = int(layout_cell.get("stack_under_cell_id") or 0)
        if parent_id:
            stacked_by_parent.setdefault(parent_id, []).append(column)
        else:
            primaries.append(column)
    slots: list[dict] = []
    for slot_index, primary in enumerate(primaries, start=1):
        cell_id = int((primary.get("layout_cell") or {}).get("id") or 0)
        stacked = stacked_by_parent.get(cell_id, [])
        slots.append(
            {
                "slot_index": slot_index,
                "col_start": int(primary.get("col_start") or 1),
                "col_span": int(primary.get("col_span") or 1),
                "columns": [primary, *stacked],
            }
        )
    return slots


def _attach_flow_column_layout(row_item: dict) -> None:
    flow_columns = list(row_item.get("flow_columns") or [])
    for column in flow_columns:
        layout_cell = column.get("layout_cell") or {}
        column["is_stacked"] = bool(int(layout_cell.get("stack_under_cell_id") or 0))
    row_item["flow_columns"] = flow_columns
    row_item["flow_column_slots"] = _group_flow_column_slots(flow_columns)
    row_item["flow_primary_columns"] = [column for column in flow_columns if not column.get("is_stacked")]
    from app.services.field_list_service import _flow_row_print_layout

    flow_print_layout = _flow_row_print_layout(flow_columns)
    row_item["flow_print_layout"] = flow_print_layout
    grid_by_cell_id = {
        int(column["cell_id"]): column
        for column in (flow_print_layout.get("columns") or [])
        + (flow_print_layout.get("stacked_columns") or [])
        if int(column.get("cell_id") or 0)
    }
    for column in flow_columns:
        layout_cell = column.get("layout_cell") or {}
        cell_id = int(layout_cell.get("id") or 0)
        grid_info = grid_by_cell_id.get(cell_id, {})
        column["grid_row_start"] = int(grid_info.get("grid_row_start") or 1)
        column["grid_row_span"] = int(grid_info.get("grid_row_span") or 1)


def _sync_stacked_cell_layout(conn, row_id: int) -> None:
    """Stacked cells keep their own col_start/col_span; no automatic sync."""
    return


def _load_flow_columns(conn, row_id: int, cell_records: list[dict] | None = None) -> list[dict]:
    cells = cell_records
    if cells is None:
        cells = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM meeting_row_cells_v2
                WHERE row_id = ?
                ORDER BY """
                + _flow_cell_order_clause()
                + """
                """,
                (row_id,),
            ).fetchall()
        ]

    flow_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM meeting_row_flow_items_v2
            WHERE row_id = ?
            ORDER BY column_index, item_order, id
            """,
            (row_id,),
        ).fetchall()
    ]

    primaries, stacked_by_parent = _partition_flow_layout_cells(cells)
    max_column_index = max([int(item["column_index"]) for item in flow_rows], default=0)
    total_columns = max(len(primaries), max_column_index)
    flow_columns = []
    for column_index in range(1, total_columns + 1):
        layout_cell = primaries[column_index - 1] if column_index - 1 < len(primaries) else None
        items = [item for item in flow_rows if int(item["column_index"]) == column_index]
        if layout_cell:
            flow_columns.append(_flow_column_from_cell(column_index=column_index, layout_cell=layout_cell, items=items))
            parent_id = int(layout_cell["id"])
            for stacked_cell in stacked_by_parent.get(parent_id, []):
                flow_columns.append(
                    _flow_column_from_cell(
                        column_index=column_index,
                        layout_cell=stacked_cell,
                        items=[],
                        combined_text_override=_stacked_flow_column_markup(stacked_cell),
                    )
                )
        elif items:
            flow_columns.append(_flow_column_from_cell(column_index=column_index, layout_cell=None, items=items))
    return flow_columns


def _renumber_flow_items(conn, row_id: int, column_index: int) -> None:
    items = conn.execute(
        """
        SELECT id
        FROM meeting_row_flow_items_v2
        WHERE row_id = ? AND column_index = ?
        ORDER BY item_order, id
        """,
        (row_id, column_index),
    ).fetchall()
    for offset, item in enumerate(items, start=1):
        conn.execute(
            """
            UPDATE meeting_row_flow_items_v2
            SET item_order = ?
            WHERE id = ?
            """,
            (100000 + offset, int(item["id"])),
        )
    for offset, item in enumerate(items, start=1):
        conn.execute(
            """
            UPDATE meeting_row_flow_items_v2
            SET item_order = ?
            WHERE id = ?
            """,
            (offset, int(item["id"])),
        )


def _assert_row_in_section(conn, section_id: int, row_id: int):
    return conn.execute(
        """
        SELECT *
        FROM meeting_section_rows_v2
        WHERE id = ? AND section_id = ?
        """,
        (row_id, section_id),
    ).fetchone()


def _usable_width_in(preset: dict) -> float:
    return max(
        0.1,
        float(preset["trim_width_in"]) - float(preset["margin_left_in"]) - float(preset["margin_right_in"]),
    )


def _layout_values_from_preset(preset: dict) -> dict:
    usable_width_in = _usable_width_in(preset)
    return _layout_values_from_grid(
        usable_width_in=usable_width_in,
        column_count=preset["meeting_table_column_count"],
        column_gap_px=preset["meeting_table_column_gap_px"],
        screen_preview_scale=preset["screen_preview_scale"],
    )


def _coerce_meeting_grid_int(raw_value, fallback: int, minimum: int) -> int:
    try:
        value = int(round(float(raw_value)))
    except (TypeError, ValueError):
        value = fallback
    return max(minimum, value)


def _coerce_meeting_grid_float(raw_value, fallback: float, minimum: float) -> float:
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        value = fallback
    return max(minimum, value)


def _coerce_meeting_line_height(raw_value, fallback: float = 1.2) -> float:
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        value = fallback
    if value <= 0:
        value = fallback
    return max(0.8, min(3.0, round(value, 2)))


def _layout_values_from_grid(
    *,
    usable_width_in: float,
    column_count,
    column_gap_px,
    screen_preview_scale,
) -> dict:
    column_count = _coerce_meeting_grid_int(column_count, 28, 8)
    column_gap_px = _coerce_meeting_grid_int(column_gap_px, 6, 0)
    screen_preview_scale = _coerce_meeting_grid_float(screen_preview_scale, 220.0, 40.0)
    usable_preview_width_px = usable_width_in * screen_preview_scale
    total_gap_px = column_gap_px * max(0, column_count - 1)
    column_width_px_preview = max(8.0, (usable_preview_width_px - total_gap_px) / max(1, column_count))
    return {
        "usable_width_in": usable_width_in,
        "column_count": column_count,
        "column_width_px_preview": column_width_px_preview,
        "column_gap_px": column_gap_px,
        "screen_preview_scale": screen_preview_scale,
    }


def ensure_default_book_layout_preset() -> int:
    existing = fetch_one(
        """
        SELECT id
        FROM book_layout_presets
        WHERE is_default = 1
        ORDER BY id
        LIMIT 1
        """
    )
    if existing:
        return int(existing["id"])

    any_existing = fetch_one(
        """
        SELECT id
        FROM book_layout_presets
        ORDER BY id
        LIMIT 1
        """
    )
    if any_existing:
        with get_connection() as conn:
            conn.execute(
                """
                UPDATE book_layout_presets
                SET is_default = 1
                WHERE id = ?
                """,
                (int(any_existing["id"]),),
            )
            conn.commit()
        return int(any_existing["id"])

    with get_connection() as conn:
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
              line_height,
              meeting_table_column_count,
              meeting_table_column_gap_px,
              screen_preview_scale,
              meetingdata_title_bar_color,
              meetingdata_primary_row_color,
              meetingdata_secondary_row_color,
              meetingdata_highlight_row_color,
              is_default
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                DEFAULT_BOOK_PRESET["name"],
                DEFAULT_BOOK_PRESET["book_title"],
                DEFAULT_BOOK_PRESET["trim_width_in"],
                DEFAULT_BOOK_PRESET["trim_height_in"],
                DEFAULT_BOOK_PRESET["margin_left_in"],
                DEFAULT_BOOK_PRESET["margin_right_in"],
                DEFAULT_BOOK_PRESET["margin_top_in"],
                DEFAULT_BOOK_PRESET["margin_bottom_in"],
                DEFAULT_BOOK_PRESET["font_family"],
                DEFAULT_BOOK_PRESET["base_font_size_pt"],
                DEFAULT_BOOK_PRESET["line_height"],
                DEFAULT_BOOK_PRESET["meeting_table_column_count"],
                DEFAULT_BOOK_PRESET["meeting_table_column_gap_px"],
                DEFAULT_BOOK_PRESET["screen_preview_scale"],
                DEFAULT_BOOK_PRESET["meetingdata_title_bar_color"],
                DEFAULT_BOOK_PRESET["meetingdata_primary_row_color"],
                DEFAULT_BOOK_PRESET["meetingdata_secondary_row_color"],
                DEFAULT_BOOK_PRESET["meetingdata_highlight_row_color"],
                DEFAULT_BOOK_PRESET["is_default"],
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def get_meeting_v2_row_colors() -> dict:
    default_preset_id = ensure_default_book_layout_preset()
    preset = fetch_one(
        """
        SELECT
          meetingdata_title_bar_color,
          meetingdata_primary_row_color,
          meetingdata_secondary_row_color,
          meetingdata_highlight_row_color
        FROM book_layout_presets
        WHERE id = ?
        """,
        (default_preset_id,),
    )
    if not preset:
        return {
            "title_bar": DEFAULT_BOOK_PRESET["meetingdata_title_bar_color"],
            "primary": DEFAULT_BOOK_PRESET["meetingdata_primary_row_color"],
            "secondary": DEFAULT_BOOK_PRESET["meetingdata_secondary_row_color"],
            "highlight": DEFAULT_BOOK_PRESET["meetingdata_highlight_row_color"],
        }
    return {
        "title_bar": str(preset["meetingdata_title_bar_color"] or DEFAULT_BOOK_PRESET["meetingdata_title_bar_color"]),
        "primary": str(preset["meetingdata_primary_row_color"] or DEFAULT_BOOK_PRESET["meetingdata_primary_row_color"]),
        "secondary": str(preset["meetingdata_secondary_row_color"] or DEFAULT_BOOK_PRESET["meetingdata_secondary_row_color"]),
        "highlight": str(preset["meetingdata_highlight_row_color"] or DEFAULT_BOOK_PRESET["meetingdata_highlight_row_color"]),
    }


def _format_edit_batch_timestamp_parts(value: object) -> dict[str, str]:
    timestamp_text = str(value or "").strip()
    if not timestamp_text:
        return {"date": "", "time": ""}

    normalized_value = timestamp_text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized_value)
    except ValueError:
        return {"date": timestamp_text, "time": ""}

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    localized = parsed.astimezone(EDIT_LOG_DISPLAY_TIMEZONE)
    hour = localized.hour % 12 or 12
    meridiem = "AM" if localized.hour < 12 else "PM"
    return {
        "date": f"{localized.month}/{localized.day}/{localized.year}",
        "time": f"{hour}:{localized.minute:02d} {meridiem}",
    }


def _meeting_markup_to_text(value: object) -> str:
    normalized = str(value or "")
    if not normalized:
        return ""
    normalized = re.sub(r"<br\s*/?>", "\n", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"</div>\s*<div>", "\n", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"</?div>", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"<[^>]+>", "", normalized)
    normalized = unescape(normalized).replace("\r\n", "\n").replace("\r", "\n")
    return normalized.strip()


_EDIT_LOG_BREAK = re.compile(r"<br\s*/?>|</(?:div|p)\s*>", re.IGNORECASE)
_EDIT_LOG_BLOCK_OPEN = re.compile(r"<(?:div|p)[^>]*>", re.IGNORECASE)


def _normalize_edit_log_line_breaks(value: object) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _EDIT_LOG_BLOCK_OPEN.sub("", text)
    return _EDIT_LOG_BREAK.sub("\n", text)


def _meeting_log_display_text(value: object) -> str:
    text = _normalize_edit_log_line_breaks(value).strip()
    return text if text else "blank"


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


def _build_meeting_edit_highlight_html(original_text: object, edited_text: object) -> str:
    original = _normalize_edit_log_line_breaks(original_text)
    edited = _normalize_edit_log_line_breaks(edited_text)
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
            rendered_line = _build_single_line_edit_highlight_html(current_original, current_edited)
            rendered_lines.append(rendered_line)
            if current_original != current_edited:
                changed = True
        if not changed:
            return _highlight_text_html(edited)
        return "<br>".join(rendered_lines)

    return _build_single_line_edit_highlight_html(original, edited)


def _meeting_row_kind_summary(raw_value: object) -> str:
    tags = _split_row_kind_tags(raw_value)
    labels = [ROW_KIND_DISPLAY_LABELS.get(tag, tag) for tag in tags]
    return ", ".join(labels) if labels else "content"


def _snapshot_meeting_section_for_edit_log(conn, section_id: int) -> dict | None:
    section_row = conn.execute(
        """
        SELECT id, field_name, meeting_name
        FROM meeting_sections_v2
        WHERE id = ?
        """,
        (section_id,),
    ).fetchone()
    if not section_row:
        return None

    section = dict(section_row)
    rows_by_id: dict[int, dict] = {}
    row_records = conn.execute(
        """
        SELECT *
        FROM meeting_section_rows_v2
        WHERE section_id = ?
        ORDER BY row_order, id
        """,
        (section_id,),
    ).fetchall()
    for row_record in row_records:
        row_item = _decorate_row_item(dict(row_record))
        cell_records = [
            dict(cell)
            for cell in conn.execute(
                """
                SELECT *
                FROM meeting_row_cells_v2
                WHERE row_id = ?
                ORDER BY """
                + _flow_cell_order_clause()
                + """
                """,
                (int(row_item["id"]),),
            ).fetchall()
        ]
        row_item["cells"] = cell_records
        row_item["cells_by_id"] = {int(cell["id"]): cell for cell in cell_records}
        row_item["flow_columns"] = (
            _load_flow_columns(conn, int(row_item["id"]), cell_records)
            if row_item["is_column_flow_row"]
            else []
        )
        row_item["flow_columns_by_index"] = {
            int(column["column_index"]): column
            for column in row_item["flow_columns"]
        }
        if row_item["is_column_flow_row"]:
            _attach_flow_column_layout(row_item)
        rows_by_id[int(row_item["id"])] = row_item

    section["rows_by_id"] = rows_by_id
    return section


def _ensure_meeting_edit_log_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meeting_edit_log_entries (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          batch_id TEXT NOT NULL,
          batch_created_at TEXT NOT NULL,
          batch_row_index INTEGER NOT NULL,
          editor_initials TEXT NOT NULL,
          section_id INTEGER NOT NULL,
          field_name TEXT NOT NULL,
          meeting_name TEXT NOT NULL,
          row_id INTEGER NOT NULL,
          row_number INTEGER NOT NULL,
          original_text TEXT NOT NULL DEFAULT '',
          edited_text TEXT NOT NULL DEFAULT '',
          FOREIGN KEY (section_id) REFERENCES meeting_sections_v2(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_meeting_edit_log_entries_batch
        ON meeting_edit_log_entries (batch_created_at DESC, batch_row_index ASC, id ASC)
        """
    )


def _collect_meeting_edit_entries(
    section_snapshot: dict | None,
    normalized_rows: list[dict],
    cell_updates: list[dict],
    flow_column_updates: list[dict],
) -> list[dict]:
    if not section_snapshot:
        return []

    rows_by_id = section_snapshot.get("rows_by_id") or {}
    next_rows_by_id = {int(row["id"]): row for row in normalized_rows}
    entries: list[dict] = []
    seen_keys: set[tuple[int, str, str]] = set()

    def append_entry(row_id: int, original_text: str, edited_text: str) -> None:
        key = (int(row_id), original_text, edited_text)
        if key in seen_keys:
            return
        seen_keys.add(key)
        next_row = next_rows_by_id.get(int(row_id)) or {}
        row_number = int(next_row.get("row_order") or rows_by_id.get(int(row_id), {}).get("row_order") or 0)
        entries.append(
            {
                "row_id": int(row_id),
                "row_number": row_number,
                "original_text": _meeting_log_display_text(original_text),
                "edited_text": str(edited_text or "").replace("\r\n", "\n").replace("\r", "\n").strip(),
            }
        )

    for row in normalized_rows:
        row_id = int(row["id"])
        existing_row = rows_by_id.get(row_id)
        if not existing_row:
            continue
        current_kind = _meeting_row_kind_summary(existing_row.get("row_kind"))
        next_kind = _meeting_row_kind_summary(row.get("row_kind"))
        if current_kind != next_kind:
            append_entry(row_id, f"Row kind: {current_kind}", f"Row kind: {next_kind}")

        current_format = str(existing_row.get("format_code") or "").strip()
        next_format = str(row.get("format_code") or "").strip()
        if current_format != next_format:
            append_entry(
                row_id,
                f"Font size: {current_format or 'default'}",
                f"Font size: {next_format or 'default'}",
            )

        current_notes = str(existing_row.get("notes") or "").strip()
        next_notes = str(row.get("notes") or "").strip()
        if current_notes != next_notes:
            append_entry(row_id, f"Row notes:\n{current_notes}", f"Row notes:\n{next_notes}")

    flow_row_ids = {int(column["row_id"]) for column in flow_column_updates}
    for cell in cell_updates:
        cell_id = int(cell["id"])
        row_id = next(
            (candidate_id for candidate_id, row in rows_by_id.items() if cell_id in (row.get("cells_by_id") or {})),
            None,
        )
        if row_id is None or row_id in flow_row_ids:
            continue
        existing_row = rows_by_id.get(row_id) or {}
        existing_cell = (existing_row.get("cells_by_id") or {}).get(cell_id)
        if not existing_cell:
            continue

        current_text = _meeting_markup_to_text(existing_cell.get("text_value"))
        next_text = _meeting_markup_to_text(sanitize_inline_markup(cell.get("text_value") or ""))
        if current_text != next_text:
            append_entry(row_id, current_text, next_text)

    for column in flow_column_updates:
        row_id = int(column["row_id"])
        existing_row = rows_by_id.get(row_id) or {}
        cell_id = int(column.get("cell_id") or 0)
        if cell_id:
            existing_column = next(
                (
                    flow_column
                    for flow_column in existing_row.get("flow_columns") or []
                    if int((flow_column.get("layout_cell") or {}).get("id") or 0) == cell_id
                ),
                None,
            )
            if existing_column:
                current_text = _meeting_markup_to_text(existing_column.get("combined_text"))
            else:
                existing_cell = (existing_row.get("cells_by_id") or {}).get(cell_id)
                current_text = _meeting_markup_to_text((existing_cell or {}).get("text_value"))
        else:
            column_index = int(column["column_index"])
            existing_column = (existing_row.get("flow_columns_by_index") or {}).get(column_index)
            current_text = _meeting_markup_to_text((existing_column or {}).get("combined_text"))
        next_text = _meeting_markup_to_text(column.get("text_value") or "")
        if current_text != next_text:
            append_entry(row_id, current_text, next_text)

    return sorted(entries, key=lambda item: (int(item["row_number"]), int(item["row_id"]), item["original_text"], item["edited_text"]))


def _store_meeting_edit_entries(
    conn,
    section_snapshot: dict | None,
    editor_initials: str,
    entries: list[dict],
) -> None:
    if not section_snapshot or not entries or not editor_initials:
        return

    batch_id = uuid4().hex
    batch_created_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    section_id = int(section_snapshot["id"])
    field_name = str(section_snapshot.get("field_name") or "")
    meeting_name = str(section_snapshot.get("meeting_name") or "")
    for batch_row_index, entry in enumerate(entries, start=1):
        conn.execute(
            """
            INSERT INTO meeting_edit_log_entries (
              batch_id,
              batch_created_at,
              batch_row_index,
              editor_initials,
              section_id,
              field_name,
              meeting_name,
              row_id,
              row_number,
              original_text,
              edited_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                batch_created_at,
                batch_row_index,
                editor_initials,
                section_id,
                field_name,
                meeting_name,
                int(entry["row_id"]),
                int(entry["row_number"]),
                str(entry["original_text"] or ""),
                str(entry["edited_text"] or ""),
            ),
        )
    _prune_old_meeting_edit_log_entries(conn)


def _edit_log_retention_cutoff() -> str:
    now = datetime.now(timezone.utc)
    month = now.month - EDIT_LOG_RETENTION_MONTHS
    year = now.year
    while month <= 0:
        month += 12
        year -= 1
    day = min(now.day, calendar.monthrange(year, month)[1])
    return now.replace(year=year, month=month, day=day).isoformat(timespec="microseconds")


def _prune_old_meeting_edit_log_entries(conn) -> int:
    cutoff = _edit_log_retention_cutoff()
    result = conn.execute(
        """
        DELETE FROM meeting_edit_log_entries
        WHERE batch_created_at < ?
        """,
        (cutoff,),
    )
    return int(result.rowcount or 0)


def list_meeting_v2_edit_batches() -> list[dict]:
    _log_meeting_service("list meeting edit batches start")
    pruned_count = 0
    with get_connection() as conn:
        _ensure_meeting_edit_log_table(conn)
        pruned_count = _prune_old_meeting_edit_log_entries(conn)
        rows = conn.execute(
            """
            SELECT
              batch_id,
              batch_created_at,
              batch_row_index,
              editor_initials,
              section_id,
              field_name,
              meeting_name,
              row_id,
              row_number,
              original_text,
              edited_text
            FROM meeting_edit_log_entries
            ORDER BY batch_created_at DESC, batch_row_index ASC, id ASC
            """
        ).fetchall()
        if pruned_count:
            conn.commit()
    if pruned_count:
        from app.services.google_sync_service import mark_meetingdata_for_drive_export

        mark_meetingdata_for_drive_export()
    _log_meeting_service(f"list meeting edit batches rows count={len(rows)}")

    batches: list[dict] = []
    batch_lookup: dict[str, dict] = {}
    for row in rows:
        batch_id = str(row["batch_id"])
        batch = batch_lookup.get(batch_id)
        if batch is None:
            timestamp_parts = _format_edit_batch_timestamp_parts(row["batch_created_at"])
            batch = {
                "batch_id": batch_id,
                "batch_created_at": str(row["batch_created_at"] or ""),
                "batch_created_on_display": timestamp_parts["date"],
                "batch_created_at_display": timestamp_parts["time"],
                "editor_initials": str(row["editor_initials"] or ""),
                "section_id": int(row["section_id"]),
                "field_name": str(row["field_name"] or ""),
                "meeting_name": str(row["meeting_name"] or ""),
                "entries": [],
            }
            batches.append(batch)
            batch_lookup[batch_id] = batch

        edited_text = str(row["edited_text"] or "")
        batch["entries"].append(
            {
                "row_id": int(row["row_id"]),
                "row_number": int(row["row_number"]),
                "original_text": _meeting_log_display_text(row["original_text"]),
                "edited_text_html": _build_meeting_edit_highlight_html(row["original_text"], edited_text),
            }
        )

    for batch in batches:
        batch["row_count"] = len(batch["entries"])
    _log_meeting_service(f"list meeting edit batches complete batches={len(batches)}")
    return batches


def _ensure_section_layout(section_id: int) -> None:
    try:
        resolved_section_id = int(section_id)
    except (TypeError, ValueError):
        return
    if resolved_section_id <= 0:
        return

    existing = fetch_one(
        """
        SELECT id
        FROM meeting_section_layout_v2
        WHERE section_id = ?
        """,
        (resolved_section_id,),
    )
    if existing:
        return

    section_exists = fetch_one(
        """
        SELECT id
        FROM meeting_sections_v2
        WHERE id = ?
        """,
        (resolved_section_id,),
    )
    if not section_exists:
        return

    default_preset_id = ensure_default_book_layout_preset()
    preset = fetch_one(
        """
        SELECT *
        FROM book_layout_presets
        WHERE id = ?
        """,
        (default_preset_id,),
    )
    if not preset:
        return
    preset_dict = dict(preset)
    layout_values = _layout_values_from_preset(preset_dict)

    with get_connection() as conn:
        parent_row = conn.execute(
            """
            SELECT
              (SELECT id FROM meeting_sections_v2 WHERE id = ?) AS section_id,
              (SELECT id FROM book_layout_presets WHERE id = ?) AS preset_id
            """,
            (resolved_section_id, default_preset_id),
        ).fetchone()
        if not parent_row or not parent_row["section_id"] or not parent_row["preset_id"]:
            return
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO meeting_section_layout_v2 (
                  section_id,
                  book_layout_preset_id,
                  usable_width_in,
                  column_count,
                  column_width_px_preview,
                  column_gap_px,
                  screen_preview_scale,
                  font_family,
                  base_font_size_pt,
                  snap_to_grid,
                  show_column_guides
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1)
                """,
                (
                    resolved_section_id,
                    default_preset_id,
                    layout_values["usable_width_in"],
                    layout_values["column_count"],
                    layout_values["column_width_px_preview"],
                    layout_values["column_gap_px"],
                    layout_values["screen_preview_scale"],
                    str(preset_dict.get("font_family") or ""),
                    _field_list_bible_study_base_font_size_pt(),
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return


def refresh_book_layout_preset_usage(preset_id: int) -> None:
    preset = fetch_one(
        """
        SELECT *
        FROM book_layout_presets
        WHERE id = ?
        """,
        (preset_id,),
    )
    if not preset:
        return
    preset_dict = dict(preset)
    usable_width_in = _usable_width_in(preset_dict)
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT section_id, column_count, column_gap_px, screen_preview_scale
            FROM meeting_section_layout_v2
            WHERE book_layout_preset_id = ?
            """,
            (preset_id,),
        ).fetchall()
        for row in rows:
            layout_values = _layout_values_from_grid(
                usable_width_in=usable_width_in,
                column_count=row["column_count"],
                column_gap_px=row["column_gap_px"],
                screen_preview_scale=row["screen_preview_scale"],
            )
            conn.execute(
                """
                UPDATE meeting_section_layout_v2
                SET
                  usable_width_in = ?,
                  column_width_px_preview = ?
                WHERE section_id = ?
                """,
                (
                    layout_values["usable_width_in"],
                    layout_values["column_width_px_preview"],
                    int(row["section_id"]),
                ),
            )
        conn.commit()


def _ensure_missing_section_layouts() -> None:
    default_preset_id = ensure_default_book_layout_preset()
    preset = fetch_one(
        """
        SELECT *
        FROM book_layout_presets
        WHERE id = ?
        """,
        (default_preset_id,),
    )
    if not preset:
        return
    preset_dict = dict(preset)
    layout_values = _layout_values_from_preset(preset_dict)
    missing_rows = fetch_all(
        """
        SELECT s.id
        FROM meeting_sections_v2 s
        LEFT JOIN meeting_section_layout_v2 sl ON sl.section_id = s.id
        WHERE sl.id IS NULL
        """
    )
    section_ids = [int(row["id"]) for row in missing_rows]
    if not section_ids:
        return
    with get_connection() as conn:
        conn.executemany(
            """
            INSERT OR IGNORE INTO meeting_section_layout_v2 (
              section_id,
              book_layout_preset_id,
              usable_width_in,
              column_count,
              column_width_px_preview,
              column_gap_px,
              screen_preview_scale,
              font_family,
              base_font_size_pt,
              snap_to_grid,
              show_column_guides
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1)
            """,
            [
                (
                    section_id,
                    default_preset_id,
                    layout_values["usable_width_in"],
                    layout_values["column_count"],
                    layout_values["column_width_px_preview"],
                    layout_values["column_gap_px"],
                    layout_values["screen_preview_scale"],
                    str(preset_dict.get("font_family") or ""),
                    _field_list_bible_study_base_font_size_pt(),
                )
                for section_id in section_ids
            ],
        )
        conn.commit()


def _ensure_meeting_v2_sections_once() -> None:
    ensure_default_book_layout_preset()
    with get_connection() as conn:
        row_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(meeting_section_rows_v2)").fetchall()
        }
        if row_columns and "group_with_table_below" not in row_columns:
            conn.execute(
                """
                ALTER TABLE meeting_section_rows_v2
                ADD COLUMN group_with_table_below INTEGER NOT NULL DEFAULT 0
                """
            )
            conn.commit()
        if row_columns and "no_meeting_association" not in row_columns:
            conn.execute(
                """
                ALTER TABLE meeting_section_rows_v2
                ADD COLUMN no_meeting_association INTEGER NOT NULL DEFAULT 0
                """
            )
            conn.commit()
        if row_columns and "print_new_page" not in row_columns:
            conn.execute(
                """
                ALTER TABLE meeting_section_rows_v2
                ADD COLUMN print_new_page INTEGER NOT NULL DEFAULT 0
                """
            )
            conn.commit()
        layout_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(meeting_section_layout_v2)").fetchall()
        }
        if layout_columns and "font_family" not in layout_columns:
            conn.execute(
                """
                ALTER TABLE meeting_section_layout_v2
                ADD COLUMN font_family TEXT NOT NULL DEFAULT ''
                """
            )
            conn.commit()
        if layout_columns and "base_font_size_pt" not in layout_columns:
            conn.execute(
                """
                ALTER TABLE meeting_section_layout_v2
                ADD COLUMN base_font_size_pt REAL NOT NULL DEFAULT 0
                """
            )
            conn.commit()
        if layout_columns and "screen_preview_scale" not in layout_columns:
            conn.execute(
                """
                ALTER TABLE meeting_section_layout_v2
                ADD COLUMN screen_preview_scale REAL NOT NULL DEFAULT 220.0
                """
            )
            conn.commit()
        if layout_columns and "line_height" not in layout_columns:
            conn.execute(
                """
                ALTER TABLE meeting_section_layout_v2
                ADD COLUMN line_height REAL NOT NULL DEFAULT 0
                """
            )
            conn.commit()
        cell_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(meeting_row_cells_v2)").fetchall()
        }
        if cell_columns and "stack_under_cell_id" not in cell_columns:
            conn.execute(
                """
                ALTER TABLE meeting_row_cells_v2
                ADD COLUMN stack_under_cell_id INTEGER
                """
            )
            conn.commit()
        if cell_columns and "flow_min_lines" not in cell_columns:
            conn.execute(
                """
                ALTER TABLE meeting_row_cells_v2
                ADD COLUMN flow_min_lines INTEGER
                """
            )
            conn.commit()
    v2_count = fetch_one("SELECT COUNT(*) AS count FROM meeting_sections_v2")
    if not v2_count or not v2_count["count"]:
        legacy_count = fetch_one("SELECT COUNT(*) AS count FROM meeting_data_rows")
        if legacy_count and legacy_count["count"]:
            convert_legacy_meetingdata_to_v2()
    _sync_contact_assignments_to_meeting_v2_sections()
    _ensure_missing_section_layouts()


def _split_assignment_text(value: str | None) -> list[str]:
    text = str(value or "").replace(":::", "\n").replace(";", "\n")
    values: list[str] = []
    seen: set[str] = set()
    for item in text.splitlines():
        cleaned = item.strip()
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            values.append(cleaned)
    return values


def delete_empty_orphan_meeting_v2_sections() -> int:
    ensure_meeting_v2_sections()
    with get_connection() as conn:
        valid_pairs: set[tuple[str, str]] = set()
        contact_rows = conn.execute(
            """
            SELECT fields_text, meetings_text
            FROM contacts
            WHERE TRIM(COALESCE(fields_text, '')) != ''
              AND TRIM(COALESCE(meetings_text, '')) != ''
            """
        ).fetchall()
        for row in contact_rows:
            for field_name in _split_assignment_text(row["fields_text"]):
                for meeting_name in _split_assignment_text(row["meetings_text"]):
                    if (
                        field_name
                        and meeting_name
                        and field_name.strip().lower() != "deleted"
                        and meeting_name.strip().lower() != "deleted"
                    ):
                        valid_pairs.add((field_name, meeting_name))

        section_rows = conn.execute(
            """
            SELECT s.id,
                   s.field_name,
                   s.meeting_name,
                   SUM(CASE WHEN TRIM(COALESCE(c.text_value, '')) != '' THEN 1 ELSE 0 END) AS nonempty_cell_count
            FROM meeting_sections_v2 s
            LEFT JOIN meeting_section_rows_v2 r ON r.section_id = s.id
            LEFT JOIN meeting_row_cells_v2 c ON c.row_id = r.id
            GROUP BY s.id
            """
        ).fetchall()
        from app.services.field_end_list_service import is_field_end_list

        removed_count = 0
        for row in section_rows:
            field_name = str(row["field_name"] or "").strip()
            meeting_name = str(row["meeting_name"] or "").strip()
            nonempty_cell_count = int(row["nonempty_cell_count"] or 0)
            if nonempty_cell_count > 0 or (field_name, meeting_name) in valid_pairs:
                continue
            if is_field_end_list(field_name, meeting_name, conn=conn):
                continue
            conn.execute("DELETE FROM meeting_sections_v2 WHERE id = ?", (int(row["id"]),))
            removed_count += 1

        for kind, column_name in (("field", "field_name"), ("meeting", "meeting_name")):
            conn.execute(
                f"""
                DELETE FROM meeting_section_name_options
                WHERE kind = ?
                  AND NOT EXISTS (
                    SELECT 1
                    FROM meeting_sections_v2
                    WHERE {column_name} = meeting_section_name_options.value
                  )
                """,
                (kind,),
            )
        conn.commit()
    return removed_count


def delete_empty_no_cell_meeting_v2_sections() -> int:
    ensure_meeting_v2_sections()
    with get_connection() as conn:
        section_rows = conn.execute(
            """
            SELECT s.id, s.field_name, s.meeting_name
            FROM meeting_sections_v2 s
            LEFT JOIN meeting_section_rows_v2 r ON r.section_id = s.id
            LEFT JOIN meeting_row_cells_v2 c ON c.row_id = r.id
            GROUP BY s.id
            HAVING COUNT(c.id) = 0
            """
        ).fetchall()
        removed_count = 0
        from app.services.field_end_list_service import is_field_end_list

        for row in section_rows:
            if is_field_end_list(row["field_name"], row["meeting_name"], conn=conn):
                continue
            if _delete_meeting_v2_section_in_conn(conn, int(row["id"])):
                removed_count += 1
        conn.commit()
    return removed_count


def _sync_contact_assignments_to_meeting_v2_sections() -> None:
    with get_connection() as conn:
        option_rows = conn.execute(
            """
            SELECT kind, value
            FROM contact_assignment_options
            WHERE kind IN ('field', 'meeting')
              AND TRIM(COALESCE(value, '')) != ''
            """
        ).fetchall()
        conn.executemany(
            """
            INSERT OR IGNORE INTO meeting_section_name_options (kind, value)
            VALUES (?, ?)
            """,
            [
                (str(row["kind"] or "").strip().lower(), str(row["value"] or "").strip())
                for row in option_rows
                if str(row["value"] or "").strip()
            ],
        )

        existing_pairs = {
            (
                str(row["field_name"] or "").strip().casefold(),
                str(row["meeting_name"] or "").strip().casefold(),
            )
            for row in conn.execute("SELECT field_name, meeting_name FROM meeting_sections_v2")
        }
        contact_rows = conn.execute(
            """
            SELECT DISTINCT fields_text, meetings_text
            FROM contacts
            WHERE TRIM(COALESCE(fields_text, '')) != ''
              AND TRIM(COALESCE(meetings_text, '')) != ''
            """
        ).fetchall()
        next_sort_order = int(
            (conn.execute("SELECT MAX(sort_order) AS max_sort_order FROM meeting_sections_v2").fetchone() or {})[
                "max_sort_order"
            ]
            or 0
        )
        pending: list[tuple[str, str, int]] = []
        for row in contact_rows:
            for field_name in _split_assignment_text(row["fields_text"]):
                for meeting_name in _split_assignment_text(row["meetings_text"]):
                    if (
                        not field_name
                        or not meeting_name
                        or field_name.strip().lower() == "deleted"
                        or meeting_name.strip().lower() == "deleted"
                    ):
                        continue
                    pair_key = (field_name.casefold(), meeting_name.casefold())
                    if pair_key in existing_pairs:
                        continue
                    existing_pairs.add(pair_key)
                    next_sort_order += 1
                    pending.append((field_name, meeting_name, next_sort_order))
        for field_name, meeting_name, sort_order in pending:
            cursor = conn.execute(
                """
                INSERT INTO meeting_sections_v2 (
                  field_name,
                  meeting_name,
                  sort_order,
                  source_legacy_start_row,
                  source_legacy_end_row,
                  notes
                ) VALUES (?, ?, ?, NULL, NULL, '')
                """,
                (field_name, meeting_name, sort_order),
            )
            section_id = int(cursor.lastrowid)
            conn.execute(
                """
                INSERT OR IGNORE INTO meeting_section_name_options (kind, value)
                VALUES ('field', ?)
                """,
                (field_name,),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO meeting_section_name_options (kind, value)
                VALUES ('meeting', ?)
                """,
                (meeting_name,),
            )
            conn.execute(
                """
                INSERT INTO meeting_section_rows_v2 (
                  section_id,
                  row_order,
                  row_kind,
                  group_with_table_below,
                  no_meeting_association,
                  format_code,
                  label_text,
                  notes
                ) VALUES (?, 1, 'content', 0, 0, '', '', '')
                """,
                (section_id,),
            )
        conn.commit()


def ensure_meeting_v2_sections() -> None:
    for attempt in range(6):
        try:
            _ensure_meeting_v2_sections_once()
            return
        except sqlite3.OperationalError as exc:
            if "database is locked" not in str(exc).lower() or attempt == 5:
                raise
            time.sleep(1.5 * (attempt + 1))


def reset_meeting_v2_tables() -> None:
    with get_connection() as conn:
        conn.executescript(
            """
            DELETE FROM meeting_layout_presets_v2;
            DELETE FROM meeting_row_flow_items_v2;
            DELETE FROM meeting_row_cells_v2;
            DELETE FROM meeting_section_rows_v2;
            DELETE FROM meeting_sections_v2;
            """
        )
        conn.commit()


def convert_legacy_meetingdata_to_v2() -> int:
    sections = get_meeting_sections()
    reset_meeting_v2_tables()

    with get_connection() as conn:
        for sort_order, legacy_section in enumerate(sections, start=1):
            editor_section = build_editor_section_from_legacy_section(
                legacy_section,
                sort_order=sort_order,
            )
            section_cursor = conn.execute(
                """
                INSERT INTO meeting_sections_v2 (
                  field_name,
                  meeting_name,
                  sort_order,
                  source_legacy_start_row,
                  source_legacy_end_row,
                  notes
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    editor_section.field_name,
                    editor_section.meeting_name,
                    editor_section.sort_order,
                    editor_section.source_legacy_start_row,
                    editor_section.source_legacy_end_row,
                    editor_section.notes,
                ),
            )
            section_id = section_cursor.lastrowid

            conn.execute(
                """
                INSERT INTO meeting_layout_presets_v2 (
                  section_id,
                  viewport_name,
                  column_count,
                  column_pixel_width,
                  row_gap_px,
                  cell_gap_px
                ) VALUES (?, 'screen', ?, ?, 6, 6)
                """,
                (
                    section_id,
                    editor_section.column_count,
                    editor_section.column_pixel_width,
                ),
            )

            for row in editor_section.rows:
                row_cursor = conn.execute(
                    """
                    INSERT INTO meeting_section_rows_v2 (
                      section_id,
                      row_order,
                      row_kind,
                      group_with_table_below,
                      no_meeting_association,
                      format_code,
                      label_text,
                      notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        section_id,
                        row.row_order,
                        row.row_kind,
                        0,
                        0,
                        row.format_code,
                        row.label_text,
                        row.notes,
                    ),
                )
                row_id = row_cursor.lastrowid

                for cell in row.cells:
                    conn.execute(
                        """
                        INSERT INTO meeting_row_cells_v2 (
                          row_id,
                          col_start,
                          col_span,
                          text_value,
                          text_align,
                          font_scale,
                          is_bold,
                          is_italic,
                          is_underlined,
                          is_title
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            row_id,
                            cell.col_start,
                            cell.col_span,
                            cell.text_value,
                            cell.style.text_align,
                            cell.style.font_scale,
                            int(cell.style.is_bold),
                            int(cell.style.is_italic),
                            int(cell.style.is_underlined),
                            int(cell.style.is_title),
                        ),
                    )

        conn.commit()

    section_rows = fetch_all("SELECT id FROM meeting_sections_v2")
    for row in section_rows:
        _ensure_section_layout(int(row["id"]))

    return len(sections)


def get_meeting_v2_filter_options(field_filter: str = "") -> dict:
    _log_meeting_service("filter options start")
    settings_row = fetch_one("SELECT print_order_json FROM address_book_settings WHERE id = 1")
    print_order_json = str(settings_row["print_order_json"] or "") if settings_row else ""
    fields = fetch_all(
        """
        SELECT DISTINCT field_name
        FROM meeting_sections_v2
        WHERE TRIM(COALESCE(field_name, '')) != ''
        ORDER BY field_name
        """
    )
    _log_meeting_service(f"filter options fields count={len(fields)}")

    params: list[str] = []
    meeting_where = ["TRIM(COALESCE(meeting_name, '')) != ''"]
    if str(field_filter or "").strip():
        meeting_where.append("field_name = ?")
        params.append(str(field_filter).strip())

    meetings = fetch_all(
        f"""
        SELECT DISTINCT meeting_name
        FROM meeting_sections_v2
        WHERE {" AND ".join(meeting_where)}
        ORDER BY meeting_name
        """,
        tuple(params),
    )
    _log_meeting_service(f"filter options meetings count={len(meetings)}")

    meeting_rows = fetch_all(
        """
        SELECT DISTINCT field_name, meeting_name
        FROM meeting_sections_v2
        WHERE TRIM(COALESCE(field_name, '')) != ''
          AND TRIM(COALESCE(meeting_name, '')) != ''
        ORDER BY field_name, meeting_name
        """
    )
    _log_meeting_service(f"filter options meeting rows count={len(meeting_rows)}")

    meetings_by_field: dict[str, list[str]] = {}
    for row in meeting_rows:
        field_name = str(row["field_name"])
        meeting_name = str(row["meeting_name"])
        if is_assignment_menu_sentinel(field_name) or is_assignment_menu_sentinel(meeting_name):
            continue
        meetings_by_field.setdefault(field_name, []).append(meeting_name)

    field_values = [str(row["field_name"]) for row in fields if not is_assignment_menu_sentinel(row["field_name"])]
    meeting_values = [str(row["meeting_name"]) for row in meetings if not is_assignment_menu_sentinel(row["meeting_name"])]

    saved_field_rows = fetch_all(
        """
        SELECT value
        FROM meeting_section_name_options
        WHERE kind = 'field' AND TRIM(COALESCE(value, '')) != ''
        ORDER BY value COLLATE NOCASE
        """
    )
    _log_meeting_service(f"filter options saved fields count={len(saved_field_rows)}")
    for row in saved_field_rows:
        value = str(row["value"] or "").strip()
        if value and not is_assignment_menu_sentinel(value) and value not in field_values:
            field_values.append(value)

    saved_meeting_rows = fetch_all(
        """
        SELECT value
        FROM meeting_section_name_options
        WHERE kind = 'meeting' AND TRIM(COALESCE(value, '')) != ''
        ORDER BY value COLLATE NOCASE
        """
    )
    _log_meeting_service(f"filter options saved meetings count={len(saved_meeting_rows)}")
    for row in saved_meeting_rows:
        value = str(row["value"] or "").strip()
        if value and not is_assignment_menu_sentinel(value) and value not in meeting_values:
            meeting_values.append(value)

    field_values = order_field_names(field_values, print_order_json)
    meeting_values = order_meeting_names(meeting_values, print_order_json, str(field_filter or "").strip())
    from app.services.field_end_list_service import field_end_list_names_by_field

    field_end_lists_by_field = field_end_list_names_by_field()
    for field_name, list_names in field_end_lists_by_field.items():
        field_meetings = meetings_by_field.setdefault(field_name, [])
        for list_name in list_names:
            if list_name not in field_meetings:
                field_meetings.append(list_name)
            if list_name not in meeting_values:
                meeting_values.append(list_name)

    meetings_by_field = order_meetings_by_field(meetings_by_field, print_order_json)

    result = {
        "fields": field_values,
        "meetings": meeting_values,
        "meetings_by_field": meetings_by_field,
        "field_end_lists_by_field": field_end_lists_by_field,
    }
    _log_meeting_service("filter options complete")
    return result


def add_meeting_v2_name_option(kind: str, value: str) -> str:
    ensure_meeting_v2_sections()
    normalized_kind = str(kind or "").strip().lower()
    normalized_value = str(value or "").strip()
    if normalized_kind not in {"field", "meeting"}:
        raise ValueError("Invalid MeetingData option kind.")
    if not normalized_value:
        raise ValueError("MeetingData option value is required.")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO meeting_section_name_options (kind, value)
            VALUES (?, ?)
            """,
            (normalized_kind, normalized_value),
        )
        conn.commit()
    return normalized_value


def create_meeting_v2_section(field_name: str, meeting_name: str) -> int:
    ensure_meeting_v2_sections()
    normalized_field = str(field_name or "").strip()
    normalized_meeting = str(meeting_name or "").strip()
    if not normalized_field:
        raise ValueError("Pick a Field first or create a new Field before creating a new Meeting.")
    if not normalized_meeting:
        raise ValueError("Meeting name is required.")

    reuse_section_id: int | None = None
    reuse_needs_row = False

    with get_connection() as conn:
        existing = conn.execute(
            """
            SELECT
              s.id AS id,
              COUNT(c.id) AS cell_count,
              COUNT(DISTINCT r.id) AS row_count
            FROM meeting_sections_v2 s
            LEFT JOIN meeting_section_rows_v2 r ON r.section_id = s.id
            LEFT JOIN meeting_row_cells_v2 c ON c.row_id = r.id
            WHERE s.field_name = ? AND s.meeting_name = ?
            GROUP BY s.id
            ORDER BY s.id
            LIMIT 1
            """,
            (normalized_field, normalized_meeting),
        ).fetchone()
        if existing:
            # A section that already holds content is a genuine duplicate.
            if int(existing["cell_count"] or 0) > 0:
                raise ValueError("That MeetingData Meeting already exists in the selected Field.")
            # An empty section here is an auto-generated/phantom placeholder (e.g. one
            # synced from a contact assignment) that would otherwise be purged on the
            # next MeetingData page load. Reuse it so "Add Meeting" opens its editor
            # instead of failing with a misleading "already exists" error.
            reuse_section_id = int(existing["id"])
            reuse_needs_row = int(existing["row_count"] or 0) == 0

        if reuse_section_id is None:
            current_max = conn.execute(
                """
                SELECT MAX(sort_order) AS max_sort_order
                FROM meeting_sections_v2
                """
            ).fetchone()
            next_sort_order = int(current_max["max_sort_order"] or 0) + 1

            cursor = conn.execute(
                """
                INSERT INTO meeting_sections_v2 (
                  field_name,
                  meeting_name,
                  sort_order,
                  source_legacy_start_row,
                  source_legacy_end_row,
                  notes
                ) VALUES (?, ?, ?, NULL, NULL, '')
                """,
                (normalized_field, normalized_meeting, next_sort_order),
            )
            section_id = int(cursor.lastrowid)
        else:
            section_id = reuse_section_id

        conn.execute(
            """
            INSERT OR IGNORE INTO meeting_section_name_options (kind, value)
            VALUES ('field', ?)
            """,
            (normalized_field,),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO meeting_section_name_options (kind, value)
            VALUES ('meeting', ?)
            """,
            (normalized_meeting,),
        )
        conn.commit()

    _ensure_section_layout(section_id)
    if reuse_section_id is None or reuse_needs_row:
        add_meeting_v2_row(section_id, row_kind="content")
    return section_id


def delete_meeting_v2_name_option(kind: str, value: str) -> str:
    ensure_meeting_v2_sections()
    normalized_kind = str(kind or "").strip().lower()
    normalized_value = str(value or "").strip()
    if normalized_kind not in {"field", "meeting"}:
        raise ValueError("Invalid MeetingData option kind.")
    if not normalized_value:
        raise ValueError("Select a value to delete first.")

    column_name = "field_name" if normalized_kind == "field" else "meeting_name"
    in_use = fetch_one(
        f"""
        SELECT COUNT(*) AS count
        FROM meeting_sections_v2
        WHERE {column_name} = ?
        """,
        (normalized_value,),
    )
    in_use_count = int(in_use["count"]) if in_use else 0
    if in_use_count > 0:
        label = "Field" if normalized_kind == "field" else "Meeting"
        raise ValueError(f"{label} groups must have no MeetingData sections before they can be deleted.")

    with get_connection() as conn:
        conn.execute(
            """
            DELETE FROM meeting_section_name_options
            WHERE kind = ? AND value = ?
            """,
            (normalized_kind, normalized_value),
        )
        conn.commit()
    return normalized_value


def rename_meeting_v2_name_option(kind: str, old_value: str, new_value: str) -> dict:
    ensure_meeting_v2_sections()
    normalized_kind = str(kind or "").strip().lower()
    normalized_old_value = str(old_value or "").strip()
    normalized_new_value = str(new_value or "").strip()
    if normalized_kind not in {"field", "meeting"}:
        raise ValueError("Invalid MeetingData option kind.")
    if not normalized_old_value:
        raise ValueError("Select a value to rename first.")
    if not normalized_new_value:
        raise ValueError("Enter a new name first.")
    if normalized_old_value == normalized_new_value:
        raise ValueError("Choose a different new name.")

    column_name = "field_name" if normalized_kind == "field" else "meeting_name"
    sections = fetch_all(
        f"""
        SELECT id, field_name, meeting_name
        FROM meeting_sections_v2
        WHERE {column_name} = ?
        ORDER BY id
        """,
        (normalized_old_value,),
    )

    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO meeting_section_name_options (kind, value)
            VALUES (?, ?)
            """,
            (normalized_kind, normalized_new_value),
        )

        affected_count = 0
        for row in sections:
            field_name = normalized_new_value if normalized_kind == "field" else str(row["field_name"])
            meeting_name = normalized_new_value if normalized_kind == "meeting" else str(row["meeting_name"])
            conflict = fetch_one(
                """
                SELECT id
                FROM meeting_sections_v2
                WHERE field_name = ? AND meeting_name = ? AND id != ?
                """,
                (field_name, meeting_name, int(row["id"])),
            )
            if conflict:
                raise ValueError("Rename would create a duplicate MeetingData Field/Meeting pair.")

            conn.execute(
                f"""
                UPDATE meeting_sections_v2
                SET {column_name} = ?
                WHERE id = ?
                """,
                (normalized_new_value, int(row["id"])),
            )
            affected_count += 1

        conn.execute(
            """
            DELETE FROM meeting_section_name_options
            WHERE kind = ? AND value = ?
            """,
            (normalized_kind, normalized_old_value),
        )
        conn.commit()

    return {
        "old_value": normalized_old_value,
        "new_value": normalized_new_value,
        "affected_count": affected_count,
    }


def list_meeting_v2_sections(search_text: str = "", field_filter: str = "", meeting_filter: str = "") -> list[dict]:
    ensure_meeting_v2_sections()
    from app.services.field_end_list_service import ensure_field_end_list_sections, field_end_list_names_by_field

    ensure_field_end_list_sections()
    where_clauses = ["1 = 1"]
    params: list[str] = []

    if str(search_text or "").strip():
        like_value = f"%{str(search_text).strip()}%"
        where_clauses.append("(s.field_name LIKE ? OR s.meeting_name LIKE ?)")
        params.extend([like_value, like_value])

    if str(field_filter or "").strip():
        where_clauses.append("s.field_name = ?")
        params.append(str(field_filter).strip())

    if str(meeting_filter or "").strip():
        where_clauses.append("s.meeting_name = ?")
        params.append(str(meeting_filter).strip())

    rows = fetch_all(
        f"""
        SELECT
          s.id,
          s.field_name,
          s.meeting_name,
          s.sort_order,
          s.source_legacy_start_row,
          s.source_legacy_end_row,
          COUNT(DISTINCT r.id) AS row_count,
          COUNT(c.id) AS cell_count
        FROM meeting_sections_v2 s
        LEFT JOIN meeting_section_rows_v2 r ON r.section_id = s.id
        LEFT JOIN meeting_row_cells_v2 c ON c.row_id = r.id
        WHERE {" AND ".join(where_clauses)}
        GROUP BY
          s.id,
          s.field_name,
          s.meeting_name,
          s.sort_order,
          s.source_legacy_start_row,
          s.source_legacy_end_row
        ORDER BY lower(s.field_name), lower(s.meeting_name), s.id
        """,
        tuple(params),
    )
    settings_row = fetch_one("SELECT print_order_json FROM address_book_settings WHERE id = 1")
    print_order_json = str(settings_row["print_order_json"] or "") if settings_row else ""
    field_orders, meeting_orders = print_order_lookup(print_order_json)
    sections = [dict(row) for row in rows]
    field_end_names_by_field = field_end_list_names_by_field()

    def _field_end_sort_parts(section: dict) -> tuple[int, int]:
        field_name = str(section.get("field_name") or "").strip()
        meeting_name = str(section.get("meeting_name") or "").strip()
        names = field_end_names_by_field.get(field_name) or []
        if meeting_name in names:
            return (1, names.index(meeting_name))
        return (0, 0)

    return sorted(
        sections,
        key=lambda section: (
            field_orders.get(print_order_match_key(section.get("field_name")), 1_000_000),
            str(section.get("field_name") or "").strip().lower(),
            *_field_end_sort_parts(section),
            meeting_orders.get(
                (
                    print_order_match_key(section.get("field_name")),
                    print_order_match_key(section.get("meeting_name")),
                ),
                1_000_000,
            ),
            str(section.get("meeting_name") or "").strip().lower(),
            int(section.get("id") or 0),
        ),
    )


def get_meeting_v2_section_detail(
    section_id: int,
    *,
    ensure: bool = True,
    include_editor_extras: bool = True,
) -> dict | None:
    if ensure:
        ensure_meeting_v2_sections()
    section_row = fetch_one(
        """
        SELECT *
        FROM meeting_sections_v2
        WHERE id = ?
        """,
        (section_id,),
    )
    if not section_row:
        return None
    _ensure_section_layout(int(section_row["id"]))

    section = dict(section_row)
    preset_row = fetch_one(
        """
        SELECT *
        FROM meeting_layout_presets_v2
        WHERE section_id = ?
        ORDER BY id
        LIMIT 1
        """,
        (section_id,),
    )
    section["layout"] = dict(preset_row) if preset_row else {
        "column_count": 28,
        "column_pixel_width": 16,
        "row_gap_px": 6,
        "cell_gap_px": 6,
    }
    section_layout = fetch_one(
        """
        SELECT
          sl.id,
          sl.section_id,
          sl.book_layout_preset_id,
          sl.usable_width_in,
          sl.column_count,
          sl.column_width_px_preview,
          sl.column_gap_px,
          COALESCE(NULLIF(sl.screen_preview_scale, 0), bp.screen_preview_scale) AS screen_preview_scale,
          sl.font_family AS section_font_family,
          sl.base_font_size_pt AS section_base_font_size_pt,
          sl.line_height AS section_line_height,
          COALESCE(NULLIF(sl.line_height, 0), bp.line_height) AS line_height,
          COALESCE(NULLIF(sl.font_family, ''), bp.font_family) AS font_family,
          sl.snap_to_grid,
          sl.show_column_guides,
          bp.name AS preset_name,
          bp.book_title,
          bp.trim_width_in,
          bp.trim_height_in,
          bp.margin_left_in,
          bp.margin_right_in,
          bp.margin_top_in,
          bp.margin_bottom_in,
          bp.font_family AS preset_font_family,
          bp.base_font_size_pt,
          bp.line_height AS preset_line_height,
          bp.meeting_table_column_count,
          bp.meeting_table_column_gap_px,
          bp.screen_preview_scale AS preset_screen_preview_scale,
          bp.meetingdata_title_bar_color,
          bp.meetingdata_primary_row_color,
          bp.meetingdata_secondary_row_color,
          bp.meetingdata_highlight_row_color
        FROM meeting_section_layout_v2 sl
        JOIN book_layout_presets bp ON bp.id = sl.book_layout_preset_id
        WHERE sl.section_id = ?
        LIMIT 1
        """,
        (section_id,),
    )
    section["book_layout"] = dict(section_layout) if section_layout else None
    if include_editor_extras:
        section["book_layout_presets"] = [dict(row) for row in fetch_all("SELECT * FROM book_layout_presets ORDER BY is_default DESC, name")]
        section["font_size_options"] = MEETING_FONT_SIZE_OPTIONS
    else:
        section["book_layout_presets"] = []
        section["font_size_options"] = []

    rows = []
    with get_connection() as conn:
        row_records = conn.execute(
            """
            SELECT *
            FROM meeting_section_rows_v2
            WHERE section_id = ?
            ORDER BY row_order, id
            """,
            (section_id,),
        ).fetchall()
        row_ids = [int(row_record["id"]) for row_record in row_records]
        cells_by_row: dict[int, list[dict]] = {}
        if row_ids:
            placeholders = ",".join("?" for _ in row_ids)
            cell_order = _flow_cell_order_clause()
            for cell in conn.execute(
                f"""
                SELECT *
                FROM meeting_row_cells_v2
                WHERE row_id IN ({placeholders})
                ORDER BY row_id, {cell_order}
                """,
                tuple(row_ids),
            ).fetchall():
                cells_by_row.setdefault(int(cell["row_id"]), []).append(dict(cell))
        for row_record in row_records:
            row_item = _decorate_row_item(dict(row_record))
            cell_records = cells_by_row.get(int(row_item["id"]), [])
            row_item["cells"] = cell_records
            row_item["flow_columns"] = (
                _load_flow_columns(conn, row_item["id"], cell_records)
                if row_item["is_column_flow_row"]
                else []
            )
            if row_item["is_column_flow_row"]:
                _attach_flow_column_layout(row_item)
            rows.append(row_item)

    layout_base_font_size_pt = _parse_font_size_code((section.get("book_layout") or {}).get("section_base_font_size_pt"))
    base_font_size_pt = layout_base_font_size_pt or _field_list_bible_study_base_font_size_pt()
    _apply_row_font_size_overrides(rows, base_font_size_pt)
    section["rows"] = rows
    if include_editor_extras:
        section["editor_model"] = editor_section_to_dict(
            build_editor_section_from_legacy_section(
                {
                    "field_name": section["field_name"],
                    "meeting_name": section["meeting_name"],
                    "rows": [
                        {
                            "sheet_row_number": row["row_order"],
                            "format_code": row.get("format_code") or "",
                            "cells": [
                                {
                                    "grid_column": cell["col_start"],
                                    "grid_span": cell["col_span"],
                                    "value": cell["text_value"],
                                    "is_full_width": bool(cell["is_title"]),
                                }
                                for cell in row["cells"]
                            ],
                        }
                        for row in rows
                    ],
                },
                sort_order=section["sort_order"],
            )
        )
    return section


def save_meeting_v2_section(
    section_id: int,
    section_notes: str,
    book_layout_preset_id: int | None,
    base_font_size_pt: str | None,
    editor_initials: str,
    row_updates: list[dict],
    cell_updates: list[dict],
    flow_item_updates: list[dict] | None = None,
    flow_column_updates: list[dict] | None = None,
    meeting_table_column_count=None,
    meeting_table_column_gap_px=None,
    screen_preview_scale=None,
    line_height=None,
) -> None:
    ensure_meeting_v2_sections()
    flow_item_updates = flow_item_updates or []
    flow_column_updates = flow_column_updates or []
    saved_flow_column_updates: list[dict] = []
    normalized_editor_initials = re.sub(r"[^A-Za-z]", "", str(editor_initials or ""))[:2]
    normalized_base_font_size_pt = _parse_font_size_code(base_font_size_pt) or _field_list_bible_study_base_font_size_pt()
    section_identity = fetch_one(
        """
        SELECT field_name, meeting_name
        FROM meeting_sections_v2
        WHERE id = ?
        """,
        (section_id,),
    )
    from app.services.field_end_list_service import is_field_end_list

    field_list_editor = bool(
        section_identity
        and is_field_end_list(section_identity.get("field_name"), section_identity.get("meeting_name"))
    )
    normalized_rows = []
    sortable_rows = []
    for index, row in enumerate(row_updates):
        sortable_rows.append(
            (
                int(row.get("row_order") or 0),
                index,
                {
                    "id": int(row["id"]),
                    "row_kind": _normalize_row_kind_value(row.get("row_kind") or "content"),
                    "group_with_table_below": int(row.get("group_with_table_below") or 0),
                    "no_meeting_association": 0 if field_list_editor else int(row.get("no_meeting_association") or 0),
                    "print_new_page": int(row.get("print_new_page") or 0) if field_list_editor else 0,
                    "format_code": str(row.get("format_code") or ""),
                    "label_text": str(row.get("label_text") or ""),
                    "notes": str(row.get("notes") or ""),
                },
            )
        )
    for next_order, (_, _, row) in enumerate(sorted(sortable_rows, key=lambda item: (item[0], item[1])), start=1):
        normalized_rows.append(
            {
                "id": row["id"],
                "row_order": next_order,
                "row_kind": _normalize_row_kind_value(row["row_kind"]),
                "group_with_table_below": int(row.get("group_with_table_below") or 0),
                "no_meeting_association": int(row.get("no_meeting_association") or 0),
                "print_new_page": int(row.get("print_new_page") or 0),
                "format_code": row["format_code"],
                "label_text": row["label_text"],
                "notes": row["notes"],
            }
        )

    with get_connection() as conn:
        _ensure_meeting_edit_log_table(conn)
        section_snapshot = _snapshot_meeting_section_for_edit_log(conn, section_id)
        existing_row_kind_by_id = {
            int(row["id"]): str(row["row_kind"] or "content")
            for row in conn.execute(
                """
                SELECT id, row_kind
                FROM meeting_section_rows_v2
                WHERE section_id = ?
                """,
                (section_id,),
            ).fetchall()
        }

        conn.execute(
            """
            UPDATE meeting_sections_v2
            SET notes = ?
            WHERE id = ?
            """,
            (section_notes or "", section_id),
        )
        preset = None
        if book_layout_preset_id:
            preset_row = conn.execute(
                """
                SELECT *
                FROM book_layout_presets
                WHERE id = ?
                """,
                (book_layout_preset_id,),
            ).fetchone()
            preset = dict(preset_row) if preset_row else None

        current_layout = conn.execute(
            """
            SELECT usable_width_in, column_count, column_gap_px, screen_preview_scale, line_height
            FROM meeting_section_layout_v2
            WHERE section_id = ?
            """,
            (section_id,),
        ).fetchone()
        current_layout_dict = dict(current_layout or {})
        usable_width_in = _usable_width_in(preset) if preset else float(current_layout_dict.get("usable_width_in") or 3.22)
        layout_values = _layout_values_from_grid(
            usable_width_in=usable_width_in,
            column_count=meeting_table_column_count if meeting_table_column_count is not None else current_layout_dict.get("column_count"),
            column_gap_px=meeting_table_column_gap_px if meeting_table_column_gap_px is not None else current_layout_dict.get("column_gap_px"),
            screen_preview_scale=screen_preview_scale if screen_preview_scale is not None else current_layout_dict.get("screen_preview_scale"),
        )
        current_line_height = current_layout_dict.get("line_height")
        if line_height is not None:
            normalized_line_height = _coerce_meeting_line_height(
                line_height,
                float(current_line_height or 0) or float((preset or {}).get("line_height") or 1.2),
            )
        else:
            normalized_line_height = float(current_line_height or 0)
        if preset:
            conn.execute(
                """
                UPDATE meeting_section_layout_v2
                SET
                  book_layout_preset_id = ?,
                  usable_width_in = ?,
                  column_count = ?,
                  column_width_px_preview = ?,
                  column_gap_px = ?,
                  screen_preview_scale = ?,
                  base_font_size_pt = ?,
                  line_height = ?
                WHERE section_id = ?
                """,
                (
                    book_layout_preset_id,
                    layout_values["usable_width_in"],
                    layout_values["column_count"],
                    layout_values["column_width_px_preview"],
                    layout_values["column_gap_px"],
                    layout_values["screen_preview_scale"],
                    normalized_base_font_size_pt,
                    normalized_line_height,
                    section_id,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE meeting_section_layout_v2
                SET
                  usable_width_in = ?,
                  column_count = ?,
                  column_width_px_preview = ?,
                  column_gap_px = ?,
                  screen_preview_scale = ?,
                  base_font_size_pt = ?,
                  line_height = ?
                WHERE section_id = ?
                """,
                (
                    layout_values["usable_width_in"],
                    layout_values["column_count"],
                    layout_values["column_width_px_preview"],
                    layout_values["column_gap_px"],
                    layout_values["screen_preview_scale"],
                    normalized_base_font_size_pt,
                    normalized_line_height,
                    section_id,
                ),
            )

        # Move row orders out of the way first so reordering does not trip the
        # unique(section_id, row_order) constraint during intermediate updates.
        for index, row in enumerate(normalized_rows, start=1):
            conn.execute(
                """
                UPDATE meeting_section_rows_v2
                SET row_order = ?
                WHERE id = ? AND section_id = ?
                """,
                (100000 + index, int(row["id"]), section_id),
            )

        for row in normalized_rows:
            conn.execute(
                """
                UPDATE meeting_section_rows_v2
                SET row_order = ?, row_kind = ?, group_with_table_below = ?, no_meeting_association = ?, print_new_page = ?, format_code = ?, label_text = ?, notes = ?
                WHERE id = ? AND section_id = ?
                """,
                (
                    int(row["row_order"]),
                    _normalize_row_kind_value(row["row_kind"]),
                    1 if _row_has_tag(row["row_kind"], "table_start") and int(row.get("group_with_table_below") or 0) else 0,
                    1 if _row_has_tag(row["row_kind"], "table_start") and int(row.get("no_meeting_association") or 0) else 0,
                    1 if _row_has_tag(row["row_kind"], "table_start") and int(row.get("print_new_page") or 0) else 0,
                    row.get("format_code") or "",
                    row.get("label_text") or "",
                    row.get("notes") or "",
                    int(row["id"]),
                    section_id,
                ),
            )

        for cell in cell_updates:
            flow_min_lines_raw = cell.get("flow_min_lines")
            flow_min_lines = None
            if flow_min_lines_raw not in (None, ""):
                try:
                    parsed = int(flow_min_lines_raw)
                    if parsed > 0:
                        flow_min_lines = parsed
                except (TypeError, ValueError):
                    flow_min_lines = None
            conn.execute(
                """
                UPDATE meeting_row_cells_v2
                SET col_start = ?, col_span = ?, text_value = ?, text_align = ?, is_bold = ?, is_italic = ?, is_underlined = ?, flow_min_lines = ?
                WHERE id = ?
                """,
                (
                    int(cell["col_start"]),
                    int(cell["col_span"]),
                    sanitize_inline_markup(cell.get("text_value") or ""),
                    cell.get("text_align") or "auto",
                    int(cell.get("is_bold") or 0),
                    int(cell.get("is_italic") or 0),
                    int(cell.get("is_underlined") or 0),
                    flow_min_lines,
                    int(cell["id"]),
                ),
            )

        synced_flow_row_ids: set[int] = set()
        for column in flow_column_updates:
            synced_flow_row_ids.add(int(column["row_id"]))
        for row in normalized_rows:
            if _primary_row_kind(_split_row_kind_tags(row.get("row_kind") or "")) == "column_flow":
                synced_flow_row_ids.add(int(row["id"]))
        for row_id in synced_flow_row_ids:
            _sync_stacked_cell_layout(conn, row_id)

        for item in flow_item_updates:
            conn.execute(
                """
                UPDATE meeting_row_flow_items_v2
                SET text_value = ?, text_align = ?, is_bold = ?, is_italic = ?, is_underlined = ?, font_scale = ?
                WHERE id = ?
                """,
                (
                    sanitize_inline_markup(item.get("text_value") or ""),
                    item.get("text_align") or "left",
                    int(item.get("is_bold") or 0),
                    int(item.get("is_italic") or 0),
                    int(item.get("is_underlined") or 0),
                    float(item.get("font_scale") or 1.0),
                    int(item["id"]),
                ),
            )

        flow_column_cell_updates_by_row: dict[int, dict[int, dict]] = {}
        legacy_flow_column_updates: list[dict] = []
        for column in flow_column_updates:
            row_id = int(column["row_id"])
            cell_id = int(column.get("cell_id") or 0)
            if cell_id:
                flow_column_cell_updates_by_row.setdefault(row_id, {})[cell_id] = column
            else:
                legacy_flow_column_updates.append(column)

        for row_id, updates_by_cell_id in flow_column_cell_updates_by_row.items():
            ordered_cells = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id, stack_under_cell_id
                    FROM meeting_row_cells_v2
                    WHERE row_id = ?
                    ORDER BY """
                    + _flow_cell_order_clause()
                    + """
                    """,
                    (row_id,),
                ).fetchall()
            ]
            primaries, _stacked_by_parent = _partition_flow_layout_cells(ordered_cells)
            stacked_cells = [
                cell for cell in ordered_cells if int(cell.get("stack_under_cell_id") or 0)
            ]
            conn.execute(
                """
                DELETE FROM meeting_row_flow_items_v2
                WHERE row_id = ?
                """,
                (row_id,),
            )
            for column_index, cell in enumerate(primaries, start=1):
                column = updates_by_cell_id.get(int(cell["id"]))
                if not column:
                    continue
                text_value = str(column.get("text_value") or "").strip()
                saved_flow_column_updates.append(
                    {
                        "row_id": row_id,
                        "column_index": column_index,
                        "text_value": text_value,
                    }
                )
                if not text_value:
                    continue
                conn.execute(
                    """
                    INSERT INTO meeting_row_flow_items_v2 (
                      row_id,
                      column_index,
                      item_order,
                      text_value,
                      text_align,
                      font_scale,
                      is_bold,
                      is_italic,
                      is_underlined
                    ) VALUES (?, ?, 1, ?, 'left', 1.0, 0, 0, 0)
                    """,
                    (row_id, column_index, sanitize_inline_markup(text_value, preserve_edge_breaks=True)),
                )
            for cell in stacked_cells:
                column = updates_by_cell_id.get(int(cell["id"]))
                if not column:
                    continue
                text_value = str(column.get("text_value") or "")
                saved_flow_column_updates.append(
                    {
                        "row_id": row_id,
                        "cell_id": int(cell["id"]),
                        "text_value": text_value,
                    }
                )
                conn.execute(
                    """
                    UPDATE meeting_row_cells_v2
                    SET text_value = ?
                    WHERE id = ? AND row_id = ?
                    """,
                    (
                        sanitize_inline_markup(text_value, preserve_edge_breaks=True),
                        int(cell["id"]),
                        row_id,
                    ),
                )

        for column in legacy_flow_column_updates:
            row_id = int(column["row_id"])
            if row_id in flow_column_cell_updates_by_row:
                continue
            column_index = int(column["column_index"])
            text_value = str(column.get("text_value") or "").strip()
            saved_flow_column_updates.append(
                {
                    "row_id": row_id,
                    "column_index": column_index,
                    "text_value": text_value,
                }
            )
            conn.execute(
                """
                DELETE FROM meeting_row_flow_items_v2
                WHERE row_id = ? AND column_index = ?
                """,
                (row_id, column_index),
            )
            if text_value:
                conn.execute(
                    """
                    INSERT INTO meeting_row_flow_items_v2 (
                      row_id,
                      column_index,
                      item_order,
                      text_value,
                      text_align,
                      font_scale,
                      is_bold,
                      is_italic,
                      is_underlined
                    ) VALUES (?, ?, 1, ?, 'left', 1.0, 0, 0, 0)
                    """,
                    (row_id, column_index, sanitize_inline_markup(text_value, preserve_edge_breaks=True)),
                )

        for row in normalized_rows:
            row_id = int(row["id"])
            previous_primary = _primary_row_kind(
                _split_row_kind_tags(existing_row_kind_by_id.get(row_id, "content"))
            )
            current_primary = _primary_row_kind(_split_row_kind_tags(row["row_kind"]))
            if previous_primary == current_primary:
                continue
            if current_primary == "column_flow":
                _convert_row_to_column_flow(conn, row_id)
            elif previous_primary == "column_flow":
                _convert_row_from_column_flow(conn, row_id)

        meeting_edit_entries = _collect_meeting_edit_entries(
            section_snapshot,
            normalized_rows,
            cell_updates,
            saved_flow_column_updates,
        )
        _store_meeting_edit_entries(
            conn,
            section_snapshot,
            normalized_editor_initials,
            meeting_edit_entries,
        )
        conn.commit()


def _ensure_flow_layout_cell(conn, row_id: int) -> None:
    existing_cell = conn.execute(
        """
        SELECT id
        FROM meeting_row_cells_v2
        WHERE row_id = ?
        ORDER BY col_start, id
        LIMIT 1
        """,
        (row_id,),
    ).fetchone()
    if existing_cell:
        return
    conn.execute(
        """
        INSERT INTO meeting_row_cells_v2 (
          row_id,
          col_start,
          col_span,
          text_value,
          text_align,
          font_scale,
          is_bold,
          is_italic,
          is_underlined,
          is_title
        ) VALUES (?, 1, 2, '', 'left', 1.0, 0, 0, 0, 0)
        """,
        (row_id,),
    )


def _convert_row_to_column_flow(conn, row_id: int) -> None:
    cell_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM meeting_row_cells_v2
            WHERE row_id = ?
              AND (stack_under_cell_id IS NULL OR stack_under_cell_id = 0)
            ORDER BY col_start, id
            """,
            (row_id,),
        ).fetchall()
    ]

    if not cell_rows:
        _ensure_flow_layout_cell(conn, row_id)
        return

    conn.execute(
        """
        DELETE FROM meeting_row_flow_items_v2
        WHERE row_id = ?
        """,
        (row_id,),
    )

    for column_index, cell in enumerate(cell_rows, start=1):
        text_value = str(cell.get("text_value") or "").strip()
        if not text_value:
            continue
        conn.execute(
            """
            INSERT INTO meeting_row_flow_items_v2 (
              row_id,
              column_index,
              item_order,
              text_value,
              text_align,
              font_scale,
              is_bold,
              is_italic,
              is_underlined
            ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_id,
                column_index,
                sanitize_inline_markup(text_value, preserve_edge_breaks=True),
                str(cell.get("text_align") or "left"),
                float(cell.get("font_scale") or 1.0),
                int(cell.get("is_bold") or 0),
                int(cell.get("is_italic") or 0),
                int(cell.get("is_underlined") or 0),
            ),
        )


def _convert_row_from_column_flow(conn, row_id: int) -> None:
    cell_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM meeting_row_cells_v2
            WHERE row_id = ?
            ORDER BY col_start, id
            """,
            (row_id,),
        ).fetchall()
    ]
    if not cell_rows:
        _ensure_flow_layout_cell(conn, row_id)
        cell_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM meeting_row_cells_v2
                WHERE row_id = ?
                ORDER BY col_start, id
                """,
                (row_id,),
            ).fetchall()
        ]

    flow_columns = _load_flow_columns(conn, row_id, cell_rows)
    for index, cell in enumerate(cell_rows):
        combined_text = ""
        if index < len(flow_columns):
            combined_text = str(flow_columns[index].get("combined_text") or "")
        conn.execute(
            """
            UPDATE meeting_row_cells_v2
            SET text_value = ?
            WHERE id = ?
            """,
            (sanitize_inline_markup(combined_text, preserve_edge_breaks=True), int(cell["id"])),
        )

    conn.execute(
        """
        DELETE FROM meeting_row_flow_items_v2
        WHERE row_id = ?
        """,
        (row_id,),
    )


def add_meeting_v2_row(
    section_id: int,
    after_row_id: int | None = None,
    row_kind: str = "content",
    clone_from_row_id: int | None = None,
) -> int:
    ensure_meeting_v2_sections()
    normalized_row_kind = _normalize_row_kind_value(row_kind)
    primary_row_kind = _primary_row_kind(_split_row_kind_tags(normalized_row_kind))

    with get_connection() as conn:
        if after_row_id:
            anchor_row = conn.execute(
                """
                SELECT row_order
                FROM meeting_section_rows_v2
                WHERE id = ? AND section_id = ?
                """,
                (after_row_id, section_id),
            ).fetchone()
            if anchor_row:
                next_row_order = int(anchor_row["row_order"]) + 1
                rows_to_shift = conn.execute(
                    """
                    SELECT id
                    FROM meeting_section_rows_v2
                    WHERE section_id = ? AND row_order >= ?
                    ORDER BY row_order DESC, id DESC
                    """,
                    (section_id, next_row_order),
                ).fetchall()
                for offset, row in enumerate(rows_to_shift, start=1):
                    conn.execute(
                        """
                        UPDATE meeting_section_rows_v2
                        SET row_order = ?
                        WHERE id = ? AND section_id = ?
                        """,
                        (200000 + offset, int(row["id"]), section_id),
                    )
                for offset, row in enumerate(reversed(rows_to_shift), start=1):
                    conn.execute(
                        """
                        UPDATE meeting_section_rows_v2
                        SET row_order = ?
                        WHERE id = ? AND section_id = ?
                        """,
                        (next_row_order + offset, int(row["id"]), section_id),
                    )
            else:
                current_max = conn.execute(
                    """
                    SELECT MAX(row_order) AS max_row_order
                    FROM meeting_section_rows_v2
                    WHERE section_id = ?
                    """,
                    (section_id,),
                ).fetchone()
                next_row_order = int(current_max["max_row_order"] or 0) + 1
        else:
            current_max = conn.execute(
                """
                SELECT MAX(row_order) AS max_row_order
                FROM meeting_section_rows_v2
                WHERE section_id = ?
                """,
                (section_id,),
            ).fetchone()
            next_row_order = int(current_max["max_row_order"] or 0) + 1

        row_cursor = conn.execute(
            """
            INSERT INTO meeting_section_rows_v2 (
              section_id,
              row_order,
              row_kind,
              group_with_table_below,
              no_meeting_association,
              format_code,
              label_text,
              notes
            ) VALUES (?, ?, ?, 0, 0, '', '', '')
            """,
            (section_id, next_row_order, normalized_row_kind),
        )
        row_id = row_cursor.lastrowid
        if clone_from_row_id:
            source_row = conn.execute(
                """
                SELECT row_kind, group_with_table_below, no_meeting_association, print_new_page, format_code
                FROM meeting_section_rows_v2
                WHERE id = ? AND section_id = ?
                """,
                (clone_from_row_id, section_id),
            ).fetchone()
            if source_row:
                source_kind = _normalize_row_kind_value(source_row["row_kind"] or "content")
                conn.execute(
                    """
                    UPDATE meeting_section_rows_v2
                    SET row_kind = ?, group_with_table_below = ?, no_meeting_association = ?, print_new_page = ?, format_code = ?
                    WHERE id = ? AND section_id = ?
                    """,
                    (
                        source_kind,
                        int(source_row["group_with_table_below"] or 0),
                        int(source_row["no_meeting_association"] or 0),
                        int(source_row["print_new_page"] or 0),
                        str(source_row["format_code"] or ""),
                        row_id,
                        section_id,
                    ),
                )
                source_cells = conn.execute(
                    """
                    SELECT
                      col_start,
                      col_span,
                      text_align,
                      font_scale,
                      is_bold,
                      is_italic,
                      is_underlined,
                      is_title
                    FROM meeting_row_cells_v2
                    WHERE row_id = ?
                    ORDER BY col_start, id
                    """,
                    (clone_from_row_id,),
                ).fetchall()
                for source_cell in source_cells:
                    conn.execute(
                        """
                        INSERT INTO meeting_row_cells_v2 (
                          row_id,
                          col_start,
                          col_span,
                          text_value,
                          text_align,
                          font_scale,
                          is_bold,
                          is_italic,
                          is_underlined,
                          is_title
                        ) VALUES (?, ?, ?, '', ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            row_id,
                            int(source_cell["col_start"]),
                            int(source_cell["col_span"]),
                            str(source_cell["text_align"] or "left"),
                            float(source_cell["font_scale"] or 1.0),
                            int(source_cell["is_bold"] or 0),
                            int(source_cell["is_italic"] or 0),
                            int(source_cell["is_underlined"] or 0),
                            int(source_cell["is_title"] or 0),
                        ),
                    )
        elif primary_row_kind not in {"blank", "divider"}:
            layout_row = conn.execute(
                """
                SELECT column_count
                FROM meeting_section_layout_v2
                WHERE section_id = ?
                LIMIT 1
                """,
                (section_id,),
            ).fetchone()
            default_col_span = _coerce_meeting_grid_int(
                layout_row["column_count"] if layout_row else None,
                28,
                1,
            )
            conn.execute(
                """
                INSERT INTO meeting_row_cells_v2 (
                  row_id,
                  col_start,
                  col_span,
                  text_value,
                  text_align,
                  font_scale,
                  is_bold,
                  is_italic,
                  is_underlined,
                  is_title
                ) VALUES (?, 1, ?, '', 'auto', 1.0, 0, 0, 0, 0)
                """,
                (row_id, default_col_span),
            )
        row_ids = conn.execute(
            """
            SELECT id
            FROM meeting_section_rows_v2
            WHERE section_id = ?
            ORDER BY row_order, id
            """,
            (section_id,),
        ).fetchall()
        for offset, row in enumerate(row_ids, start=1):
            conn.execute(
                """
                UPDATE meeting_section_rows_v2
                SET row_order = ?
                WHERE id = ? AND section_id = ?
                """,
                (300000 + offset, int(row["id"]), section_id),
            )
        for next_order, row in enumerate(row_ids, start=1):
            conn.execute(
                """
                UPDATE meeting_section_rows_v2
                SET row_order = ?
                WHERE id = ? AND section_id = ?
                """,
                (next_order, int(row["id"]), section_id),
            )
        conn.commit()
    return row_id


def delete_meeting_v2_row(section_id: int, row_id: int) -> None:
    ensure_meeting_v2_sections()
    with get_connection() as conn:
        section = conn.execute(
            """
            SELECT id
            FROM meeting_sections_v2
            WHERE id = ?
            LIMIT 1
            """,
            (section_id,),
        ).fetchone()
        if not section:
            return
        conn.execute(
            """
            DELETE FROM meeting_section_rows_v2
            WHERE id = ? AND section_id = ?
            """,
            (row_id, section_id),
        )
        row_ids = conn.execute(
            """
            SELECT id
            FROM meeting_section_rows_v2
            WHERE section_id = ?
            ORDER BY row_order, id
            """,
            (section_id,),
        ).fetchall()
        if not row_ids:
            _delete_meeting_v2_section_in_conn(conn, section_id)
            conn.commit()
            return
        if _delete_meeting_v2_section_if_no_cells(conn, section_id):
            conn.commit()
            return
        for offset, row in enumerate(row_ids, start=1):
            conn.execute(
                """
                UPDATE meeting_section_rows_v2
                SET row_order = ?
                WHERE id = ? AND section_id = ?
                """,
                (300000 + offset, int(row["id"]), section_id),
            )
        for next_order, row in enumerate(row_ids, start=1):
            conn.execute(
                """
                UPDATE meeting_section_rows_v2
                SET row_order = ?
                WHERE id = ? AND section_id = ?
                """,
                (next_order, int(row["id"]), section_id),
            )
        conn.commit()


def _delete_meeting_v2_section_in_conn(conn, section_id: int) -> bool:
    section = conn.execute(
        """
        SELECT field_name, meeting_name
        FROM meeting_sections_v2
        WHERE id = ?
        LIMIT 1
        """,
        (section_id,),
    ).fetchone()
    if not section:
        return False

    field_name = str(section["field_name"] or "").strip()
    meeting_name = str(section["meeting_name"] or "").strip()

    conn.execute(
        """
        DELETE FROM meeting_sections_v2
        WHERE id = ?
        """,
        (section_id,),
    )

    remaining_field = conn.execute(
        """
        SELECT 1
        FROM meeting_sections_v2
        WHERE field_name = ?
        LIMIT 1
        """,
        (field_name,),
    ).fetchone()
    if not remaining_field and field_name:
        conn.execute(
            """
            DELETE FROM meeting_section_name_options
            WHERE kind = 'field' AND value = ?
            """,
            (field_name,),
        )

    remaining_meeting = conn.execute(
        """
        SELECT 1
        FROM meeting_sections_v2
        WHERE meeting_name = ?
        LIMIT 1
        """,
        (meeting_name,),
    ).fetchone()
    if not remaining_meeting and meeting_name:
        conn.execute(
            """
            DELETE FROM meeting_section_name_options
            WHERE kind = 'meeting' AND value = ?
            """,
            (meeting_name,),
        )
    return True


def _delete_meeting_v2_section_if_no_cells(conn, section_id: int) -> bool:
    remaining_cells = conn.execute(
        """
        SELECT COUNT(c.id) AS cell_count
        FROM meeting_section_rows_v2 r
        LEFT JOIN meeting_row_cells_v2 c ON c.row_id = r.id
        WHERE r.section_id = ?
        """,
        (section_id,),
    ).fetchone()
    if int(remaining_cells["cell_count"] or 0) > 0:
        return False
    return _delete_meeting_v2_section_in_conn(conn, section_id)


def delete_meeting_v2_section(section_id: int) -> None:
    ensure_meeting_v2_sections()
    with get_connection() as conn:
        _delete_meeting_v2_section_in_conn(conn, section_id)
        conn.commit()


def add_meeting_v2_cell(section_id: int, row_id: int) -> int | None:
    ensure_meeting_v2_sections()
    with get_connection() as conn:
        row_record = conn.execute(
            """
            SELECT id, row_kind
            FROM meeting_section_rows_v2
            WHERE id = ? AND section_id = ?
            """,
            (row_id, section_id),
        ).fetchone()
        if not row_record:
            return None

        cell_rows = conn.execute(
            """
            SELECT col_start, col_span, stack_under_cell_id
            FROM meeting_row_cells_v2
            WHERE row_id = ?
            ORDER BY """
            + _flow_cell_order_clause()
            + """
            """,
            (row_id,),
        ).fetchall()
        default_span = 2
        next_start = 1
        occupied_ranges = []
        for cell_row in cell_rows:
            if int(cell_row["stack_under_cell_id"] or 0):
                continue
            start = int(cell_row["col_start"])
            end = start + int(cell_row["col_span"]) - 1
            occupied_ranges.append((start, end))

        candidate = 1
        for start, end in occupied_ranges:
            if candidate + default_span - 1 < start:
                break
            candidate = max(candidate, end + 1)

        if candidate + default_span - 1 <= 28:
            next_start = candidate
        elif occupied_ranges:
            next_start = min(28, occupied_ranges[-1][1] + 1)

        cursor = conn.execute(
            """
            INSERT INTO meeting_row_cells_v2 (
              row_id,
              col_start,
              col_span,
              text_value,
              text_align,
              font_scale,
              is_bold,
              is_italic,
              is_underlined,
              is_title
            ) VALUES (?, ?, ?, '', 'left', 1.0, 0, 0, 0, 0)
            """,
            (row_id, next_start, default_span),
        )
        conn.commit()
        return int(cursor.lastrowid)


def add_meeting_v2_stacked_cell(section_id: int, row_id: int, under_cell_id: int) -> int | None:
    ensure_meeting_v2_sections()
    with get_connection() as conn:
        row_record = _assert_row_in_section(conn, section_id, row_id)
        if not row_record:
            return None
        under_cell = conn.execute(
            """
            SELECT id, col_start, col_span, stack_under_cell_id
            FROM meeting_row_cells_v2
            WHERE id = ? AND row_id = ?
            """,
            (under_cell_id, row_id),
        ).fetchone()
        if not under_cell or int(under_cell["stack_under_cell_id"] or 0):
            return None
        cursor = conn.execute(
            """
            INSERT INTO meeting_row_cells_v2 (
              row_id,
              col_start,
              col_span,
              text_value,
              text_align,
              font_scale,
              is_bold,
              is_italic,
              is_underlined,
              is_title,
              stack_under_cell_id
            ) VALUES (?, ?, ?, '', 'left', 1.0, 0, 0, 0, 0, ?)
            """,
            (
                row_id,
                int(under_cell["col_start"]),
                int(under_cell["col_span"]),
                int(under_cell["id"]),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def update_meeting_v2_row_kind(section_id: int, row_id: int, row_kind: str) -> None:
    ensure_meeting_v2_sections()
    normalized_row_kind = _normalize_row_kind_value(row_kind)
    with get_connection() as conn:
        row_record = _assert_row_in_section(conn, section_id, row_id)
        if not row_record:
            return
        conn.execute(
            """
            UPDATE meeting_section_rows_v2
            SET
              row_kind = ?,
              group_with_table_below = CASE WHEN instr(?, 'table_start') > 0 THEN group_with_table_below ELSE 0 END,
              no_meeting_association = CASE WHEN instr(?, 'table_start') > 0 THEN no_meeting_association ELSE 0 END,
              print_new_page = CASE WHEN instr(?, 'table_start') > 0 THEN print_new_page ELSE 0 END
            WHERE id = ? AND section_id = ?
            """,
            (normalized_row_kind, normalized_row_kind, normalized_row_kind, normalized_row_kind, row_id, section_id),
        )
        conn.commit()


def add_meeting_v2_flow_item(
    section_id: int,
    row_id: int,
    column_index: int,
    *,
    after_item_id: int | None = None,
    before_item_id: int | None = None,
) -> int | None:
    ensure_meeting_v2_sections()
    with get_connection() as conn:
        row_record = _assert_row_in_section(conn, section_id, row_id)
        if not row_record:
            return None

        existing_items = [
            dict(item)
            for item in conn.execute(
                """
                SELECT id
                FROM meeting_row_flow_items_v2
                WHERE row_id = ? AND column_index = ?
                ORDER BY item_order, id
                """,
                (row_id, column_index),
            ).fetchall()
        ]

        if after_item_id is None and before_item_id is None and existing_items:
            return int(existing_items[0]["id"])

        insert_index = len(existing_items)
        if before_item_id:
            for index, item in enumerate(existing_items):
                if int(item["id"]) == before_item_id:
                    insert_index = index
                    break
        elif after_item_id:
            for index, item in enumerate(existing_items):
                if int(item["id"]) == after_item_id:
                    insert_index = index + 1
                    break

        cursor = conn.execute(
            """
            INSERT INTO meeting_row_flow_items_v2 (
              row_id,
              column_index,
              item_order,
              text_value,
              text_align,
              font_scale,
              is_bold,
              is_italic,
              is_underlined
            ) VALUES (?, ?, ?, '', 'left', 1.0, 0, 0, 0)
            """,
            (row_id, column_index, 1000000),
        )
        new_item_id = int(cursor.lastrowid)
        ordered_ids = [int(item["id"]) for item in existing_items]
        ordered_ids.insert(insert_index, new_item_id)
        for offset, item_id in enumerate(ordered_ids, start=1):
            conn.execute(
                """
                UPDATE meeting_row_flow_items_v2
                SET item_order = ?
                WHERE id = ?
                """,
                (offset, item_id),
            )
        conn.commit()
        return new_item_id


def delete_meeting_v2_flow_item(section_id: int, row_id: int, item_id: int) -> None:
    ensure_meeting_v2_sections()
    with get_connection() as conn:
        row_record = _assert_row_in_section(conn, section_id, row_id)
        if not row_record:
            return
        item = conn.execute(
            """
            SELECT column_index
            FROM meeting_row_flow_items_v2
            WHERE id = ? AND row_id = ?
            """,
            (item_id, row_id),
        ).fetchone()
        if not item:
            return
        column_index = int(item["column_index"])
        conn.execute(
            """
            DELETE FROM meeting_row_flow_items_v2
            WHERE id = ? AND row_id = ?
            """,
            (item_id, row_id),
        )
        _renumber_flow_items(conn, row_id, column_index)
        conn.commit()


def move_meeting_v2_flow_item(section_id: int, row_id: int, item_id: int, direction: str) -> None:
    ensure_meeting_v2_sections()
    with get_connection() as conn:
        row_record = _assert_row_in_section(conn, section_id, row_id)
        if not row_record:
            return
        item = conn.execute(
            """
            SELECT column_index
            FROM meeting_row_flow_items_v2
            WHERE id = ? AND row_id = ?
            """,
            (item_id, row_id),
        ).fetchone()
        if not item:
            return
        column_index = int(item["column_index"])
        ordered_ids = [
            int(row["id"])
            for row in conn.execute(
                """
                SELECT id
                FROM meeting_row_flow_items_v2
                WHERE row_id = ? AND column_index = ?
                ORDER BY item_order, id
                """,
                (row_id, column_index),
            ).fetchall()
        ]
        try:
            index = ordered_ids.index(item_id)
        except ValueError:
            return
        if direction == "up" and index > 0:
            ordered_ids[index - 1], ordered_ids[index] = ordered_ids[index], ordered_ids[index - 1]
        elif direction == "down" and index < len(ordered_ids) - 1:
            ordered_ids[index + 1], ordered_ids[index] = ordered_ids[index], ordered_ids[index + 1]
        else:
            return
        for offset, current_id in enumerate(ordered_ids, start=1):
            conn.execute(
                """
                UPDATE meeting_row_flow_items_v2
                SET item_order = ?
                WHERE id = ?
                """,
                (100000 + offset, current_id),
            )
        for offset, current_id in enumerate(ordered_ids, start=1):
            conn.execute(
                """
                UPDATE meeting_row_flow_items_v2
                SET item_order = ?
                WHERE id = ?
                """,
                (offset, current_id),
            )
        conn.commit()


def delete_meeting_v2_cell(section_id: int, row_id: int, cell_id: int) -> None:
    ensure_meeting_v2_sections()
    with get_connection() as conn:
        conn.execute(
            """
            DELETE FROM meeting_row_cells_v2
            WHERE id = ? AND row_id = ? AND row_id IN (
              SELECT id FROM meeting_section_rows_v2 WHERE id = ? AND section_id = ?
            )
            """,
            (cell_id, row_id, row_id, section_id),
        )
        _delete_meeting_v2_section_if_no_cells(conn, section_id)
        conn.commit()


def _delete_flow_column_cell(conn, row_id: int, cell_id: int) -> None:
    stacked_ids = [
        int(row["id"])
        for row in conn.execute(
            """
            SELECT id
            FROM meeting_row_cells_v2
            WHERE row_id = ? AND stack_under_cell_id = ?
            ORDER BY id
            """,
            (row_id, cell_id),
        ).fetchall()
    ]
    for stacked_id in stacked_ids:
        _delete_flow_column_cell(conn, row_id, stacked_id)

    cells = conn.execute(
        """
        SELECT id, stack_under_cell_id
        FROM meeting_row_cells_v2
        WHERE row_id = ?
        ORDER BY """
        + _flow_cell_order_clause()
        + """
        """,
        (row_id,),
    ).fetchall()
    ordered = [dict(cell) for cell in cells]
    target = next((cell for cell in ordered if int(cell["id"]) == cell_id), None)
    if not target:
        return
    if int(target.get("stack_under_cell_id") or 0):
        conn.execute(
            """
            DELETE FROM meeting_row_cells_v2
            WHERE id = ? AND row_id = ?
            """,
            (cell_id, row_id),
        )
        return
    primaries = [cell for cell in ordered if not int(cell.get("stack_under_cell_id") or 0)]
    column_index = next(
        index for index, cell in enumerate(primaries, start=1) if int(cell["id"]) == cell_id
    )
    conn.execute(
        """
        DELETE FROM meeting_row_cells_v2
        WHERE id = ? AND row_id = ?
        """,
        (cell_id, row_id),
    )
    conn.execute(
        """
        DELETE FROM meeting_row_flow_items_v2
        WHERE row_id = ? AND column_index = ?
        """,
        (row_id, column_index),
    )
    conn.execute(
        """
        UPDATE meeting_row_flow_items_v2
        SET column_index = column_index - 1
        WHERE row_id = ? AND column_index > ?
        """,
        (row_id, column_index),
    )


def move_meeting_v2_flow_column(section_id: int, row_id: int, cell_id: int, direction: str) -> None:
    ensure_meeting_v2_sections()
    with get_connection() as conn:
        row_record = _assert_row_in_section(conn, section_id, row_id)
        if not row_record:
            return
        cells = conn.execute(
            """
            SELECT id, col_start, col_span, stack_under_cell_id
            FROM meeting_row_cells_v2
            WHERE row_id = ?
            ORDER BY """
            + _flow_cell_order_clause()
            + """
            """,
            (row_id,),
        ).fetchall()
        ordered = [dict(cell) for cell in cells]
        primary_cells = [
            cell for cell in ordered if not int(cell.get("stack_under_cell_id") or 0)
        ]
        try:
            primary_index = next(
                index for index, cell in enumerate(primary_cells) if int(cell["id"]) == cell_id
            )
        except StopIteration:
            return
        if direction == "left":
            target_index = primary_index - 1
        else:
            target_index = primary_index + 1
        if target_index < 0 or target_index >= len(primary_cells):
            return
        current = primary_cells[primary_index]
        target = primary_cells[target_index]
        conn.execute(
            """
            UPDATE meeting_row_cells_v2
            SET col_start = ?, col_span = ?
            WHERE id = ?
            """,
            (int(target["col_start"]), int(target["col_span"]), int(current["id"])),
        )
        conn.execute(
            """
            UPDATE meeting_row_cells_v2
            SET col_start = ?, col_span = ?
            WHERE id = ?
            """,
            (int(current["col_start"]), int(current["col_span"]), int(target["id"])),
        )
        _sync_stacked_cell_layout(conn, row_id)
        conn.commit()


def delete_meeting_v2_flow_column(section_id: int, row_id: int, cell_id: int) -> None:
    ensure_meeting_v2_sections()
    with get_connection() as conn:
        row_record = _assert_row_in_section(conn, section_id, row_id)
        if not row_record:
            return
        _delete_flow_column_cell(conn, row_id, cell_id)
        _delete_meeting_v2_section_if_no_cells(conn, section_id)
        conn.commit()


def get_meeting_v2_section_editor(section_id: int) -> dict | None:
    section = get_meeting_v2_section_detail(section_id)
    if not section:
        return None

    layout = section.get("book_layout") or {}
    column_count = int(layout.get("column_count") or 28)
    column_width_px_preview = float(layout.get("column_width_px_preview") or 16)
    column_gap_px = int(layout.get("column_gap_px") or 6)
    usable_width_in = float(layout.get("usable_width_in") or 3.22)
    preview_width_px = (column_count * column_width_px_preview) + (max(0, column_count - 1) * column_gap_px)
    base_font_size_pt = _parse_font_size_code(layout.get("section_base_font_size_pt")) or _field_list_bible_study_base_font_size_pt()

    section["editor_canvas"] = {
        "column_count": column_count,
        "column_width_px_preview": column_width_px_preview,
        "column_gap_px": column_gap_px,
        "usable_width_in": usable_width_in,
        "preview_width_px": preview_width_px,
        "trim_width_in": float(layout.get("trim_width_in") or 3.5),
        "font_family": str(layout.get("font_family") or "Arial"),
        "base_font_size_pt": base_font_size_pt,
        "line_height": _coerce_meeting_line_height(layout.get("line_height"), 1.2),
        "screen_preview_scale": float(layout.get("screen_preview_scale") or 220.0),
    }
    from app.services.field_end_list_service import is_field_end_list

    section["is_field_end_list"] = is_field_end_list(section.get("field_name"), section.get("meeting_name"))
    return section


def get_meeting_v2_section_print_preview(section_id: int, *, ensure: bool = True) -> dict | None:
    section = get_meeting_v2_section_detail(
        section_id,
        ensure=ensure,
        include_editor_extras=False,
    )
    if not section:
        return None

    layout = section.get("book_layout") or {}
    trim_width_in = float(layout.get("trim_width_in") or 3.5)
    trim_height_in = float(layout.get("trim_height_in") or 5.0)
    margin_left_in = float(layout.get("margin_left_in") or 0.14)
    margin_right_in = float(layout.get("margin_right_in") or 0.14)
    margin_top_in = float(layout.get("margin_top_in") or 0.14)
    margin_bottom_in = float(layout.get("margin_bottom_in") or 0.14)
    printable_width_in = max(0.1, trim_width_in - margin_left_in - margin_right_in)
    printable_height_in = max(0.1, trim_height_in - margin_top_in - margin_bottom_in)
    column_count = int(layout.get("column_count") or layout.get("meeting_table_column_count") or 28)
    column_gap_px = int(layout.get("column_gap_px") or layout.get("meeting_table_column_gap_px") or 6)
    screen_preview_scale = float(layout.get("screen_preview_scale") or 220.0)
    base_font_size_pt = _parse_font_size_code(layout.get("section_base_font_size_pt")) or _field_list_bible_study_base_font_size_pt()
    preview_page_width_px = trim_width_in * screen_preview_scale
    preview_page_height_px = trim_height_in * screen_preview_scale
    preview_printable_width_px = printable_width_in * screen_preview_scale

    section["print_preview"] = {
        "trim_width_in": trim_width_in,
        "trim_height_in": trim_height_in,
        "margin_left_in": margin_left_in,
        "margin_right_in": margin_right_in,
        "margin_top_in": margin_top_in,
        "margin_bottom_in": margin_bottom_in,
        "printable_width_in": printable_width_in,
        "printable_height_in": printable_height_in,
        "column_count": column_count,
        "column_gap_px": column_gap_px,
        "screen_preview_scale": screen_preview_scale,
        "preview_page_width_px": preview_page_width_px,
        "preview_page_height_px": preview_page_height_px,
        "preview_printable_width_px": preview_printable_width_px,
        "base_font_size_pt": base_font_size_pt,
        "font_family": str(layout.get("font_family") or "Arial"),
        "line_height": _coerce_meeting_line_height(layout.get("line_height"), 1.2),
        "preset_name": str(layout.get("preset_name") or ""),
    }
    section["print_blocks"] = _build_meeting_print_blocks(section.get("rows") or [])
    return section
