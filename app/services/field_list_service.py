from __future__ import annotations

import json
import math
import re
from datetime import datetime
from html import escape, unescape
from html.parser import HTMLParser
from typing import Any

from markupsafe import Markup

from app.services.address_format import address_display_lines, enrich_address_parts, split_address_type_prefix
from app.database import _ensure_field_list_tables, fetch_all, get_connection
from app.services.preset_service import FONT_FAMILY_OPTIONS
from app.services.print_order_service import order_field_names, order_meeting_names


PAGE_WIDTH_IN = 8.5
PAGE_HEIGHT_IN = 11.0
DEFAULT_TEMPLATE_NAME = "Field List 1"
SHARED_FIELD_LIST_TEMPLATE_NAMES = {DEFAULT_TEMPLATE_NAME.lower()}
FIELD_LIST_ITEM_TYPES = [
    {"type": "field_name", "label": "Field name", "number": 1, "default_height_in": 0.42, "default_width_in": 2.0},
    {"type": "meeting_name", "label": "Meeting name", "number": 2, "default_height_in": 0.42, "default_width_in": 2.2},
    {"type": "contacts", "label": "Contacts", "number": 3, "default_height_in": 0.68, "default_width_in": 7.5},
    {"type": "meetings", "label": "Meetings", "number": 4, "default_height_in": 2.6, "default_width_in": 7.5},
    {"type": "bible_study_union", "label": "Bible study/Union meetings", "number": 5, "default_height_in": 2.0, "default_width_in": 7.5},
    {"type": "page_number", "label": "Page number", "number": 6, "default_height_in": 0.42, "default_width_in": 1.2},
]
FIELD_LIST_ITEM_TYPES_BY_KEY = {item["type"]: item for item in FIELD_LIST_ITEM_TYPES}
FIELD_LIST_PREVIEW_CONTACT_LIMIT = 100
ADDRESS_BOOK_SETTING_KEYS = (
    "margin_left_in",
    "margin_right_in",
    "margin_top_in",
    "margin_bottom_in",
    "font_family",
    "base_font_size_pt",
    "base_font_bold",
    "base_font_italic",
    "base_font_underline",
    "line_height",
    "preview_scale",
    "page_column_count",
    "include_bible_study_union_info",
    "two_column_gap_ch",
    "column_width_in",
    "address_align",
    "address_map_provider",
    "address_italic",
    "title_font_family",
    "title_font_size_pt",
    "title_font_bold",
    "title_font_italic",
    "title_font_underline",
    "title_align",
    "meeting_name_font_family",
    "meeting_name_font_size_pt",
    "meeting_name_font_bold",
    "meeting_name_font_italic",
    "meeting_name_font_underline",
    "meeting_name_align",
    "bible_study_font_size_pt",
    "bible_study_union_align",
    "separator_lines",
    "separator_vertical_lines",
    "manual_palette_items",
    "print_order_mode",
    "print_order_json",
    "meeting_page_mode",
    "field_separator_pages",
    "double_column_index",
    "selected_field_name",
    "selected_meeting_name",
    "selected_contact_id",
)


def _now_text() -> str:
    from app.services.google_sync_service import _now_text as google_now_text

    return google_now_text()


def _clean(value: object) -> str:
    return str(value or "").strip()


def _norm_lookup(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _format_font_size(value: float) -> str:
    return f"{float(value):.1f}"


def _parse_font_size_value(value: object) -> float | None:
    cleaned = _clean(value).lower()
    if cleaned.endswith("pt"):
        cleaned = cleaned[:-2].strip()
    try:
        parsed = float(cleaned)
    except (TypeError, ValueError):
        return None
    return round(parsed, 1) if parsed > 0 else None


def _coerce_float(value: object, fallback: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return min(max(number, minimum), maximum)


def _coerce_int(value: object, fallback: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return min(max(number, minimum), maximum)


def _default_address_book_settings() -> dict[str, Any]:
    return {
        "id": 1,
        "margin_left_in": 0.35,
        "margin_right_in": 0.35,
        "margin_top_in": 0.35,
        "margin_bottom_in": 0.35,
        "font_family": "Arial",
        "base_font_size_pt": 10.0,
        "base_font_bold": 0,
        "base_font_italic": 0,
        "base_font_underline": 0,
        "line_height": 1.2,
        "preview_scale": 1.0,
        "page_column_count": 1,
        "include_bible_study_union_info": 1,
        "two_column_gap_ch": 6.0,
        "column_width_in": 4.0,
        "address_align": "right",
        "address_map_provider": "google",
        "address_italic": 1,
        "title_font_family": "Arial",
        "title_font_size_pt": 13.0,
        "title_font_bold": 1,
        "title_font_italic": 0,
        "title_font_underline": 0,
        "title_align": "center",
        "meeting_name_font_family": "Arial",
        "meeting_name_font_size_pt": 11.0,
        "meeting_name_font_bold": 1,
        "meeting_name_font_italic": 0,
        "meeting_name_font_underline": 0,
        "meeting_name_align": "center",
        "bible_study_font_size_pt": 8.5,
        "bible_study_union_align": "center",
        "separator_lines": 1,
        "separator_vertical_lines": 1,
        "manual_palette_items": 0,
        "print_order_mode": "alphabetical",
        "print_order_json": "",
        "meeting_page_mode": "one_meeting",
        "field_separator_pages": 0,
        "double_column_index": 0,
        "selected_field_name": "",
        "selected_meeting_name": "",
        "selected_contact_id": 0,
    }


def _setting_value(values: dict[str, Any], fallback: dict[str, Any], key: str) -> Any:
    return values[key] if key in values else fallback.get(key)


def _setting_flag(values: dict[str, Any], fallback: dict[str, Any], key: str) -> int:
    if key not in values:
        return 1 if bool(fallback.get(key)) else 0
    return 1 if str(values.get(key) or "").lower() in {"1", "true", "yes", "on"} else 0


def _normalize_address_book_settings_values(values: dict[str, Any] | None, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    defaults = _default_address_book_settings()
    fallback_values = {**defaults, **(fallback or {})}
    source = values or {}
    normalized = {
        "margin_left_in": _coerce_float(_setting_value(source, fallback_values, "margin_left_in"), fallback_values["margin_left_in"], 0.1, 2.0),
        "margin_right_in": _coerce_float(_setting_value(source, fallback_values, "margin_right_in"), fallback_values["margin_right_in"], 0.1, 2.0),
        "margin_top_in": _coerce_float(_setting_value(source, fallback_values, "margin_top_in"), fallback_values["margin_top_in"], 0.1, 2.0),
        "margin_bottom_in": _coerce_float(_setting_value(source, fallback_values, "margin_bottom_in"), fallback_values["margin_bottom_in"], 0.1, 2.0),
        "font_family": _clean(_setting_value(source, fallback_values, "font_family")) or fallback_values["font_family"],
        "base_font_size_pt": _coerce_float(_setting_value(source, fallback_values, "base_font_size_pt"), fallback_values["base_font_size_pt"], 8.0, 36.0),
        "base_font_bold": _setting_flag(source, fallback_values, "base_font_bold"),
        "base_font_italic": _setting_flag(source, fallback_values, "base_font_italic"),
        "base_font_underline": _setting_flag(source, fallback_values, "base_font_underline"),
        "line_height": _coerce_float(_setting_value(source, fallback_values, "line_height"), fallback_values["line_height"], 0.8, 3.0),
        "preview_scale": 1.0,
        "page_column_count": _coerce_int(_setting_value(source, fallback_values, "page_column_count"), fallback_values["page_column_count"], 1, 2),
        "include_bible_study_union_info": _setting_flag(source, fallback_values, "include_bible_study_union_info"),
        "two_column_gap_ch": _coerce_float(_setting_value(source, fallback_values, "two_column_gap_ch"), fallback_values["two_column_gap_ch"], 0.0, 30.0),
        "column_width_in": _coerce_float(_setting_value(source, fallback_values, "column_width_in"), fallback_values["column_width_in"], 1.0, PAGE_WIDTH_IN),
        "address_align": _clean(_setting_value(source, fallback_values, "address_align")).lower(),
        "address_map_provider": _clean(_setting_value(source, fallback_values, "address_map_provider")).lower(),
        "address_italic": _setting_flag(source, fallback_values, "address_italic"),
        "title_font_family": _clean(_setting_value(source, fallback_values, "title_font_family")) or fallback_values["title_font_family"],
        "title_font_size_pt": _coerce_float(_setting_value(source, fallback_values, "title_font_size_pt"), fallback_values["title_font_size_pt"], 6.0, 36.0),
        "title_font_bold": _setting_flag(source, fallback_values, "title_font_bold"),
        "title_font_italic": _setting_flag(source, fallback_values, "title_font_italic"),
        "title_font_underline": _setting_flag(source, fallback_values, "title_font_underline"),
        "title_align": _clean(_setting_value(source, fallback_values, "title_align")).lower(),
        "meeting_name_font_family": _clean(_setting_value(source, fallback_values, "meeting_name_font_family")) or fallback_values["meeting_name_font_family"],
        "meeting_name_font_size_pt": _coerce_float(_setting_value(source, fallback_values, "meeting_name_font_size_pt"), fallback_values["meeting_name_font_size_pt"], 6.0, 36.0),
        "meeting_name_font_bold": _setting_flag(source, fallback_values, "meeting_name_font_bold"),
        "meeting_name_font_italic": _setting_flag(source, fallback_values, "meeting_name_font_italic"),
        "meeting_name_font_underline": _setting_flag(source, fallback_values, "meeting_name_font_underline"),
        "meeting_name_align": _clean(_setting_value(source, fallback_values, "meeting_name_align")).lower(),
        "bible_study_font_size_pt": _coerce_float(_setting_value(source, fallback_values, "bible_study_font_size_pt"), fallback_values["bible_study_font_size_pt"], 6.0, 36.0),
        "bible_study_union_align": _clean(_setting_value(source, fallback_values, "bible_study_union_align")).lower(),
        "separator_lines": _setting_flag(source, fallback_values, "separator_lines"),
        "separator_vertical_lines": _setting_flag(source, fallback_values, "separator_vertical_lines"),
        "manual_palette_items": _setting_flag(source, fallback_values, "manual_palette_items"),
        "print_order_mode": _clean(_setting_value(source, fallback_values, "print_order_mode")).lower(),
        "print_order_json": _clean(_setting_value(source, fallback_values, "print_order_json")),
        "meeting_page_mode": _clean(_setting_value(source, fallback_values, "meeting_page_mode")).lower(),
        "field_separator_pages": _setting_flag(source, fallback_values, "field_separator_pages"),
        "double_column_index": _setting_flag(source, fallback_values, "double_column_index"),
        "selected_field_name": _clean(_setting_value(source, fallback_values, "selected_field_name")),
        "selected_meeting_name": _clean(_setting_value(source, fallback_values, "selected_meeting_name")),
        "selected_contact_id": _coerce_int(_setting_value(source, fallback_values, "selected_contact_id"), fallback_values["selected_contact_id"], 0, 10_000_000),
    }
    if normalized["address_align"] not in {"left", "right"}:
        normalized["address_align"] = fallback_values["address_align"]
    if normalized["address_map_provider"] not in {"google", "apple"}:
        normalized["address_map_provider"] = fallback_values["address_map_provider"]
    if normalized["title_align"] not in {"left", "center"}:
        normalized["title_align"] = fallback_values["title_align"]
    if normalized["meeting_name_align"] not in {"left", "center"}:
        normalized["meeting_name_align"] = fallback_values["meeting_name_align"]
    if normalized["bible_study_union_align"] not in {"left", "center"}:
        normalized["bible_study_union_align"] = fallback_values["bible_study_union_align"]
    if normalized["print_order_mode"] not in {"alphabetical", "custom"}:
        normalized["print_order_mode"] = fallback_values["print_order_mode"]
    if normalized["meeting_page_mode"] not in {"one_meeting", "complete_meetings"}:
        normalized["meeting_page_mode"] = fallback_values["meeting_page_mode"]
    normalized["id"] = 1
    return normalized


def _settings_json(settings: dict[str, Any]) -> str:
    return json.dumps({key: settings.get(key) for key in ADDRESS_BOOK_SETTING_KEYS})


def _settings_from_json(raw_json: object, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(str(raw_json or "{}"))
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return _normalize_address_book_settings_values(payload, fallback)


def _default_template_items() -> list[dict[str, Any]]:
    return [
        {"item_type": "field_name", "label_number": 1, "x_in": 0.3, "y_in": 0.4, "width_in": 2.2, "height_in": 0.42, "font_size_pt": 11.0, "sort_order": 1},
        {"item_type": "meeting_name", "label_number": 2, "x_in": 2.7, "y_in": 0.4, "width_in": 2.3, "height_in": 0.42, "font_size_pt": 11.0, "sort_order": 2},
        {"item_type": "meetings", "label_number": 4, "x_in": 0.3, "y_in": 1.0, "width_in": 7.5, "height_in": 2.6, "font_size_pt": 11.0, "sort_order": 3},
        {"item_type": "page_number", "label_number": 6, "x_in": 6.45, "y_in": 9.7, "width_in": 0.9, "height_in": 0.42, "font_size_pt": 11.0, "sort_order": 4},
    ]


def ensure_field_list_runtime_schema() -> None:
    with get_connection() as conn:
        _ensure_field_list_tables(conn)
        conn.execute("INSERT OR IGNORE INTO address_book_settings (id) VALUES (1)")
        conn.commit()


def ensure_address_book_settings() -> None:
    ensure_field_list_runtime_schema()
    defaults = _default_address_book_settings()
    with get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO address_book_settings (id) VALUES (1)")
        settings_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(address_book_settings)").fetchall()
        }
        if "meeting_name_font_family" not in settings_columns:
            conn.execute(
                """
                ALTER TABLE address_book_settings
                ADD COLUMN meeting_name_font_family TEXT NOT NULL DEFAULT 'Arial'
                """
            )
        if "separator_vertical_lines" not in settings_columns:
            conn.execute(
                """
                ALTER TABLE address_book_settings
                ADD COLUMN separator_vertical_lines INTEGER NOT NULL DEFAULT 1
                """
            )
        if "manual_palette_items" not in settings_columns:
            conn.execute(
                """
                ALTER TABLE address_book_settings
                ADD COLUMN manual_palette_items INTEGER NOT NULL DEFAULT 0
                """
            )
        if "bible_study_font_size_pt" not in settings_columns:
            conn.execute(
                """
                ALTER TABLE address_book_settings
                ADD COLUMN bible_study_font_size_pt REAL NOT NULL DEFAULT 8.5
                """
            )
        if "address_map_provider" not in settings_columns:
            conn.execute(
                """
                ALTER TABLE address_book_settings
                ADD COLUMN address_map_provider TEXT NOT NULL DEFAULT 'google'
                """
            )
        if "address_italic" not in settings_columns:
            conn.execute(
                """
                ALTER TABLE address_book_settings
                ADD COLUMN address_italic INTEGER NOT NULL DEFAULT 1
                """
            )
        if "print_order_mode" not in settings_columns:
            conn.execute(
                """
                ALTER TABLE address_book_settings
                ADD COLUMN print_order_mode TEXT NOT NULL DEFAULT 'alphabetical'
                """
            )
        if "print_order_json" not in settings_columns:
            conn.execute(
                """
                ALTER TABLE address_book_settings
                ADD COLUMN print_order_json TEXT NOT NULL DEFAULT ''
                """
            )
        if "meeting_page_mode" not in settings_columns:
            conn.execute(
                """
                ALTER TABLE address_book_settings
                ADD COLUMN meeting_page_mode TEXT NOT NULL DEFAULT 'one_meeting'
                """
            )
        if "field_separator_pages" not in settings_columns:
            conn.execute(
                """
                ALTER TABLE address_book_settings
                ADD COLUMN field_separator_pages INTEGER NOT NULL DEFAULT 0
                """
            )
        if "double_column_index" not in settings_columns:
            conn.execute(
                """
                ALTER TABLE address_book_settings
                ADD COLUMN double_column_index INTEGER NOT NULL DEFAULT 0
                """
            )
        conn.execute(
            """
            UPDATE address_book_settings
            SET
              margin_left_in = COALESCE(NULLIF(margin_left_in, 0), ?),
              margin_right_in = COALESCE(NULLIF(margin_right_in, 0), ?),
              margin_top_in = COALESCE(NULLIF(margin_top_in, 0), ?),
              margin_bottom_in = COALESCE(NULLIF(margin_bottom_in, 0), ?),
              font_family = CASE WHEN TRIM(font_family) = '' THEN ? ELSE font_family END,
              base_font_size_pt = COALESCE(NULLIF(base_font_size_pt, 0), ?),
              base_font_bold = COALESCE(base_font_bold, ?),
              base_font_italic = COALESCE(base_font_italic, ?),
              base_font_underline = COALESCE(base_font_underline, ?),
              line_height = COALESCE(NULLIF(line_height, 0), ?),
              preview_scale = COALESCE(NULLIF(preview_scale, 0), ?),
              page_column_count = COALESCE(NULLIF(page_column_count, 0), ?),
              two_column_gap_ch = COALESCE(NULLIF(two_column_gap_ch, 0), ?),
              column_width_in = COALESCE(NULLIF(column_width_in, 0), ?),
              address_align = CASE WHEN TRIM(address_align) = '' THEN ? ELSE address_align END,
              address_map_provider = CASE WHEN TRIM(address_map_provider) = '' THEN ? ELSE address_map_provider END,
              address_italic = COALESCE(address_italic, ?),
              title_font_family = CASE WHEN TRIM(title_font_family) = '' THEN ? ELSE title_font_family END,
              title_font_size_pt = COALESCE(NULLIF(title_font_size_pt, 0), ?),
              title_font_bold = COALESCE(title_font_bold, ?),
              title_font_italic = COALESCE(title_font_italic, ?),
              title_font_underline = COALESCE(title_font_underline, ?),
              title_align = CASE WHEN TRIM(title_align) = '' THEN ? ELSE title_align END,
              meeting_name_font_family = CASE WHEN TRIM(meeting_name_font_family) = '' THEN ? ELSE meeting_name_font_family END,
              meeting_name_font_size_pt = COALESCE(NULLIF(meeting_name_font_size_pt, 0), ?),
              meeting_name_font_bold = COALESCE(meeting_name_font_bold, ?),
              meeting_name_font_italic = COALESCE(meeting_name_font_italic, ?),
              meeting_name_font_underline = COALESCE(meeting_name_font_underline, ?),
              meeting_name_align = CASE WHEN TRIM(meeting_name_align) = '' THEN ? ELSE meeting_name_align END,
              bible_study_font_size_pt = COALESCE(NULLIF(bible_study_font_size_pt, 0), ?),
              bible_study_union_align = CASE WHEN TRIM(bible_study_union_align) = '' THEN ? ELSE bible_study_union_align END,
              separator_lines = COALESCE(separator_lines, ?),
              separator_vertical_lines = COALESCE(separator_vertical_lines, ?),
              manual_palette_items = COALESCE(manual_palette_items, ?),
              print_order_mode = CASE WHEN TRIM(print_order_mode) = '' THEN ? ELSE print_order_mode END,
              print_order_json = COALESCE(print_order_json, ?),
              meeting_page_mode = CASE WHEN TRIM(meeting_page_mode) = '' THEN ? ELSE meeting_page_mode END,
              updated_at = ?
            WHERE id = 1
            """,
            (
                defaults["margin_left_in"],
                defaults["margin_right_in"],
                defaults["margin_top_in"],
                defaults["margin_bottom_in"],
                defaults["font_family"],
                defaults["base_font_size_pt"],
                defaults["base_font_bold"],
                defaults["base_font_italic"],
                defaults["base_font_underline"],
                defaults["line_height"],
                defaults["preview_scale"],
                defaults["page_column_count"],
                defaults["two_column_gap_ch"],
                defaults["column_width_in"],
                defaults["address_align"],
                defaults["address_map_provider"],
                defaults["address_italic"],
                defaults["title_font_family"],
                defaults["title_font_size_pt"],
                defaults["title_font_bold"],
                defaults["title_font_italic"],
                defaults["title_font_underline"],
                defaults["title_align"],
                defaults["meeting_name_font_family"],
                defaults["meeting_name_font_size_pt"],
                defaults["meeting_name_font_bold"],
                defaults["meeting_name_font_italic"],
                defaults["meeting_name_font_underline"],
                defaults["meeting_name_align"],
                defaults["bible_study_font_size_pt"],
                defaults["bible_study_union_align"],
                defaults["separator_lines"],
                defaults["separator_vertical_lines"],
                defaults["manual_palette_items"],
                defaults["print_order_mode"],
                defaults["print_order_json"],
                defaults["meeting_page_mode"],
                _now_text(),
            ),
        )
        conn.commit()


def ensure_default_field_list_template() -> int:
    ensure_address_book_settings()
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id
            FROM field_list_templates
            WHERE LOWER(name) = LOWER(?)
            ORDER BY id ASC
            LIMIT 1
            """,
            (DEFAULT_TEMPLATE_NAME,),
        ).fetchone()
        if row:
            template_id = int(row["id"])
            conn.execute(
                """
                UPDATE field_list_templates
                SET is_default = CASE WHEN id = ? THEN 1 ELSE 0 END, updated_at = ?
                """,
                (template_id, _now_text()),
            )
            conn.commit()
            return template_id

        existing_default = conn.execute(
            """
            SELECT id
            FROM field_list_templates
            WHERE is_default = 1
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()
        if existing_default:
            conn.execute(
                """
                UPDATE field_list_templates
                SET is_default = 0, updated_at = ?
                WHERE is_default = 1
                """,
                (_now_text(),),
            )

        created = conn.execute(
            """
            INSERT INTO field_list_templates (name, is_default, settings_json, created_at, updated_at)
            VALUES (?, 1, ?, ?, ?)
            """,
            (DEFAULT_TEMPLATE_NAME, _settings_json(_default_address_book_settings()), _now_text(), _now_text()),
        )
        template_id = int(created.lastrowid)
        for item in _default_template_items():
            conn.execute(
                """
                INSERT INTO field_list_template_items (
                  template_id,
                  item_type,
                  label_number,
                  x_in,
                  y_in,
                  width_in,
                  height_in,
                  font_size_pt,
                  sort_order,
                  created_at,
                  updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    template_id,
                    item["item_type"],
                    item["label_number"],
                    item["x_in"],
                    item["y_in"],
                    item["width_in"],
                    item["height_in"],
                    item["font_size_pt"],
                    item["sort_order"],
                    _now_text(),
                    _now_text(),
                ),
            )
        conn.commit()
        return template_id


def _load_address_book_settings() -> dict[str, Any]:
    ensure_address_book_settings()
    defaults = _default_address_book_settings()
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM address_book_settings WHERE id = 1").fetchone()
    settings = _normalize_address_book_settings_values(dict(row) if row else {}, defaults)
    settings["page_width_in"] = PAGE_WIDTH_IN
    settings["page_height_in"] = PAGE_HEIGHT_IN
    return settings


def get_address_book_settings() -> dict[str, Any]:
    return _load_address_book_settings()


def _row_to_template(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": _clean(row["name"]) or DEFAULT_TEMPLATE_NAME,
        "is_default": bool(row["is_default"] or 0),
        "settings_json": _clean(row["settings_json"]) if "settings_json" in row.keys() else "",
    }


def list_field_list_templates() -> list[dict[str, Any]]:
    ensure_default_field_list_template()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, name, is_default, settings_json
            FROM field_list_templates
            ORDER BY is_default DESC, LOWER(name) ASC, id ASC
            """
        ).fetchall()
    return [_row_to_template(row) for row in rows]


def has_field_list_user_templates() -> bool:
    ensure_default_field_list_template()
    with get_connection() as conn:
        count = int(
            conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM field_list_templates
                WHERE is_default = 0
                """
            ).fetchone()["count"] or 0
        )
    return count > 0


def _load_template(template_id: int | None = None) -> dict[str, Any]:
    selected_id = int(template_id or 0) or ensure_default_field_list_template()
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, name, is_default, settings_json
            FROM field_list_templates
            WHERE id = ?
            """,
            (selected_id,),
        ).fetchone()
        if row is None:
            fallback_id = ensure_default_field_list_template()
            row = conn.execute(
                """
                SELECT id, name, is_default, settings_json
                FROM field_list_templates
                WHERE id = ?
                """,
                (fallback_id,),
            ).fetchone()
        items = conn.execute(
            """
            SELECT *
            FROM field_list_template_items
            WHERE template_id = ?
            ORDER BY sort_order ASC, id ASC
            """,
            (int(row["id"]),),
        ).fetchall()
    return {
        **_row_to_template(row),
        "items": [
            _normalize_template_item(dict(item), index + 1)
            for index, item in enumerate(items)
            if _is_supported_item_type(dict(item))
        ],
    }


def _default_field_list_template() -> dict[str, Any]:
    return {
        "id": 0,
        "name": "",
        "is_default": True,
        "settings_json": _settings_json(_default_address_book_settings()),
        "items": [
            _normalize_template_item(item, index + 1)
            for index, item in enumerate(_default_template_items())
        ],
    }


def _item_display_label(item_type: str, label_number: int) -> str:
    item_def = FIELD_LIST_ITEM_TYPES_BY_KEY.get(item_type, {})
    return f'{int(label_number)} {item_def.get("label") or "Field"}'


def _is_supported_item_type(item: dict[str, Any]) -> bool:
    return _clean(item.get("item_type")) in FIELD_LIST_ITEM_TYPES_BY_KEY


def _min_phone_width_in(font_size_pt: float) -> float:
    return max((12 * max(font_size_pt, 8.0) * 0.58) / 72.0, 1.2)


def _normalize_template_item(raw_item: dict[str, Any], sort_order: int) -> dict[str, Any]:
    item_type = _clean(raw_item.get("item_type"))
    item_def = FIELD_LIST_ITEM_TYPES_BY_KEY.get(item_type) or FIELD_LIST_ITEM_TYPES[0]
    label_number = _coerce_int(raw_item.get("label_number"), int(item_def["number"]), 1, 99)
    font_size_pt = _coerce_float(raw_item.get("font_size_pt"), 11.0, 8.0, 36.0)
    width_in = _coerce_float(raw_item.get("width_in"), float(item_def["default_width_in"]), 0.4, PAGE_WIDTH_IN)
    if item_type == "phone_number":
        width_in = max(width_in, _min_phone_width_in(font_size_pt))
    height_in = _coerce_float(raw_item.get("height_in"), float(item_def["default_height_in"]), 0.28, PAGE_HEIGHT_IN)
    return {
        "id": _coerce_int(raw_item.get("id"), 0, 0, 10_000_000),
        "item_type": item_type or str(item_def["type"]),
        "label_number": label_number,
        "x_in": _coerce_float(raw_item.get("x_in"), 0.25, 0.0, PAGE_WIDTH_IN),
        "y_in": _coerce_float(raw_item.get("y_in"), 0.25, 0.0, PAGE_HEIGHT_IN),
        "width_in": width_in,
        "height_in": height_in,
        "font_size_pt": font_size_pt,
        "sort_order": _coerce_int(raw_item.get("sort_order"), sort_order, 1, 10_000),
        "display_label": _item_display_label(item_type or str(item_def["type"]), label_number),
    }


def can_edit_field_list() -> bool:
    from app.services.google_sync_service import get_google_sync_summary, is_non_editor_access_role

    summary = get_google_sync_summary()
    state = summary.get("state") or {}
    if bool(state.get("signin_sync_in_progress") or 0) or _clean(state.get("signin_sync_error")):
        return False
    if str(state.get("bootstrap_status") or "") in {"running", "failed"}:
        return False
    if is_non_editor_access_role():
        return True
    if not bool(state.get("multi_editor_enabled")):
        return True
    return bool(state.get("editor_session_id")) and str(state.get("editor_mode") or "") == "edit"


def _normalize_phone_type(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _clean(value).lower()).strip()


def _known_phone_code(phone_type: object) -> str | None:
    normalized = _normalize_phone_type(phone_type)
    if normalized in {"home", "house"}:
        return ""
    if normalized in {"mobile", "cell", "cellular", "cell phone"}:
        return "c"
    if normalized in {"nursing home", "nh"}:
        return "nh"
    if normalized in {"work", "wk"}:
        return "wk"
    return None


def _phone_label_letters(value: object) -> str:
    return re.sub(r"[^A-Za-z]+", "", _clean(value))


def _quoted_phone_nickname(value: object) -> str:
    text = _clean(value)
    match = re.search(r'["\u201c\u201d\']([^"\u201c\u201d\']+)["\u201c\u201d\']', text)
    return _clean(match.group(1)) if match else ""


def _phone_code_label(value: object) -> str:
    text = _clean(value)
    if not text:
        return ""
    nickname = _quoted_phone_nickname(text)
    return nickname or text


def _phone_code_label_for_contact(contact: dict[str, Any], label: object) -> str:
    cleaned = _clean(label)
    if not cleaned:
        return ""
    direct = _phone_code_label(cleaned)
    if direct != cleaned:
        return direct
    base_letters = _phone_label_letters(cleaned).lower()
    if not base_letters:
        return cleaned
    for name_label in _contact_name_line_labels(contact):
        nickname = _quoted_phone_nickname(name_label)
        if not nickname:
            continue
        label_letters = _phone_label_letters(name_label).lower()
        if label_letters.startswith(base_letters) or base_letters.startswith(label_letters):
            return nickname
    return cleaned


def _contact_given_phone_label(contact: dict[str, Any]) -> str:
    return _clean(contact.get("given_name"))


def _names_from_display_name(name: object) -> list[str]:
    text = _clean(name)
    if not text:
        return []
    names: list[str] = []
    for segment in text.split(";"):
        segment = segment.strip()
        if not segment:
            continue
        for match in re.finditer(r"[^:,;]+:\s*([^,;]+)", segment):
            value = _clean(match.group(1))
            if not value:
                continue
            for part in value.split(","):
                part = part.strip()
                if part:
                    names.append(part)
        if "," in segment:
            tail = segment.split(",", 1)[1].strip()
        else:
            tail = segment.strip()
        tail = re.sub(r",\s*[^:,;]+:\s*$", "", tail).strip()
        tail = re.sub(r",\s*[^:,;]+:\s*[^,;]+", "", tail).strip()
        tail = re.sub(r",\s*[^:,;]+:\s*$", "", tail).strip()
        if tail:
            for part in re.split(r"\s*&\s*", tail):
                part = part.strip().strip(",")
                if not part or part.endswith(":"):
                    continue
                if re.fullmatch(r"[A-Z]{2,}", part):
                    continue
                names.append(part)
        elif "," not in segment and "&" not in segment and ":" not in segment:
            names.append(segment.strip())
    unique_names: list[str] = []
    seen: set[str] = set()
    for label in names:
        key = _phone_label_letters(label).lower()
        if key and key not in seen:
            seen.add(key)
            unique_names.append(label)
    return unique_names


def _contact_name_line_labels(contact: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    given = _contact_given_phone_label(contact)
    if given:
        labels.append(given)
    for relation in contact.get("relationships", []):
        relation_type = _clean(relation.get("relation_type")).lower()
        relation_value = _clean(relation.get("relation_value"))
        if not relation_value:
            continue
        if relation_type == "children":
            labels.extend(part.strip() for part in relation_value.split(",") if part.strip())
        else:
            labels.append(relation_value)
    for label in _names_from_display_name(contact.get("name") or contact.get("label")):
        labels.append(label)
    unique_labels: list[str] = []
    seen: set[str] = set()
    for label in labels:
        key = _phone_label_letters(label).lower()
        if key and key not in seen:
            seen.add(key)
            unique_labels.append(label)
    return unique_labels


def _phone_type_matches_name_line(phone_type: object, contact: dict[str, Any]) -> bool:
    phone_key = _phone_label_letters(phone_type).lower()
    if not phone_key:
        return False
    return any(_phone_label_letters(label).lower() == phone_key for label in _contact_name_line_labels(contact))


def _household_phone_labels(contact: dict[str, Any], raw_phones: list[dict[str, Any]]) -> list[str]:
    labels = list(_contact_name_line_labels(contact))
    for phone in raw_phones:
        phone_type = _clean(phone.get("phone_type"))
        if phone_type and _known_phone_code(phone_type) is None:
            labels.append(phone_type)
    unique_labels = []
    seen = set()
    for label in labels:
        key = _phone_label_letters(label).lower()
        if key and key not in seen:
            seen.add(key)
            unique_labels.append(label)
    return unique_labels


def _is_mobile_phone_type(phone_type: object) -> bool:
    return _normalize_phone_type(phone_type) in {"mobile", "cell", "cellular", "cell phone"}


def _format_raw_phone_type_code(label: object) -> str:
    letters = _phone_label_letters(label)
    if not letters:
        return ""
    if len(letters) == 1:
        return letters.upper()
    return letters[:1].upper() + letters[1:2].lower()


def _phone_code_person_label(
    phone_type: object,
    contact: dict[str, Any],
    *,
    owner_given_name: str = "",
) -> str:
    owner = _clean(owner_given_name)
    if owner:
        return owner
    phone_type_value = _clean(phone_type)
    if phone_type_value and _known_phone_code(phone_type) is None:
        if _phone_type_matches_name_line(phone_type_value, contact):
            return phone_type_value
        if (
            _phone_label_letters(phone_type_value)
            and not _is_home_phone_type(phone_type)
            and not _is_work_phone_type(phone_type)
            and not _is_mobile_phone_type(phone_type)
        ):
            return phone_type_value
    return _contact_given_phone_label(contact)


def _resolve_phone_owner_labels(contact: dict[str, Any], raw_phones: list[dict[str, Any]]) -> list[str]:
    household = _contact_name_line_labels(contact)
    resolved: list[str] = []

    for item in raw_phones:
        owner = _clean(item.get("owner_given_name"))
        if owner:
            resolved.append(owner)
            continue
        phone_type = item.get("phone_type")
        phone_type_value = _clean(phone_type)
        if phone_type_value and _known_phone_code(phone_type) is None:
            if _phone_type_matches_name_line(phone_type_value, contact):
                resolved.append(phone_type_value)
                continue
            if (
                _phone_label_letters(phone_type_value)
                and not _is_home_phone_type(phone_type)
                and not _is_work_phone_type(phone_type)
                and not _is_mobile_phone_type(phone_type)
            ):
                resolved.append(phone_type_value)
                continue
        resolved.append("")

    if len(household) <= 1:
        return resolved

    for matcher in (_is_mobile_phone_type, _is_work_phone_type):
        assigned_keys = {
            _phone_label_letters(resolved[index]).lower()
            for index, item in enumerate(raw_phones)
            if resolved[index] and matcher(item.get("phone_type"))
        }
        available = [
            name
            for name in household
            if _phone_label_letters(name).lower() not in assigned_keys
        ]
        for index, item in enumerate(raw_phones):
            if resolved[index] or not matcher(item.get("phone_type")):
                continue
            if available:
                resolved[index] = available.pop(0)
            elif household:
                resolved[index] = household[0]
    return resolved


def _phone_code_conflict_labels(
    contact: dict[str, Any],
    raw_phones: list[dict[str, Any]],
    owner_labels: list[str],
) -> list[str]:
    """Names that compete for phone initials — mirrors legacy buildConflictNames_."""
    labels: list[str] = []
    seen: set[str] = set()

    def add_label(value: object) -> None:
        label = _phone_code_label_for_contact(contact, value)
        key = _phone_label_letters(label).lower()
        if not key or key in seen:
            return
        seen.add(key)
        labels.append(label)

    add_label(_contact_given_phone_label(contact))
    for relation in contact.get("relationships") or []:
        relation_type = _clean(relation.get("relation_type")).lower()
        if relation_type in {"spouse", "wife", "husband", "partner"}:
            add_label(relation.get("relation_value"))

    for index, phone in enumerate(raw_phones):
        owner = _clean(owner_labels[index]) if index < len(owner_labels) else ""
        if owner:
            add_label(owner)
            continue
        phone_type_value = _clean(phone.get("phone_type"))
        if phone_type_value and _known_phone_code(phone_type_value) is None:
            if _phone_type_matches_name_line(phone_type_value, contact):
                add_label(phone_type_value)

    return labels


def _format_custom_phone_code(label: object, all_custom_labels: list[str], is_first_phone: bool, uppercase: bool = False) -> str:
    letters = _phone_label_letters(label)
    if not letters:
        return ""
    lower_labels = [_phone_label_letters(item).lower() for item in all_custom_labels if _phone_label_letters(item)]
    lower_value = letters.lower()
    # Grow the prefix until unique among household labels.
    # First phone stays in the fixed 2-letter slot; later phones may use 3 letters
    # (they sit on address lines where there is usually more room).
    prefix_length = 1
    max_prefix = min(2 if is_first_phone else 3, len(lower_value))
    while prefix_length < max_prefix:
        prefix = lower_value[:prefix_length]
        if sum(1 for item in lower_labels if item.startswith(prefix)) <= 1:
            break
        prefix_length += 1
    prefix = letters[:prefix_length]
    if uppercase:
        return prefix.upper()
    return prefix[:1].upper() + prefix[1:].lower()


def _is_home_phone_type(phone_type: object) -> bool:
    return _normalize_phone_type(phone_type) in {"home", "house"}


def _is_work_phone_type(phone_type: object) -> bool:
    return _normalize_phone_type(phone_type) in {"work", "wk"}


def _format_contact_phones(contact: dict[str, Any]) -> list[dict[str, str]]:
    raw_phones = [
        item
        for item in contact.get("phones", [])
        if _clean(item.get("phone_value"))
    ]
    household_labels = _household_phone_labels(contact, raw_phones)
    has_household_people = len(household_labels) > 1
    owner_labels = _resolve_phone_owner_labels(contact, raw_phones)
    conflict_labels = _phone_code_conflict_labels(contact, raw_phones, owner_labels)
    formatted = []
    for index, item in enumerate(raw_phones):
        phone_type = item.get("phone_type")
        known_code = _known_phone_code(phone_type)
        phone_type_value = _clean(phone_type)
        is_custom_type = known_code is None and bool(phone_type_value)
        person_label = _phone_code_person_label(
            phone_type,
            contact,
            owner_given_name=owner_labels[index],
        )
        phone_code_label = _phone_code_label_for_contact(contact, person_label)
        if _is_home_phone_type(phone_type):
            code = ""
        elif _is_mobile_phone_type(phone_type):
            if has_household_people:
                code = _format_custom_phone_code(
                    phone_code_label,
                    conflict_labels,
                    index == 0,
                    uppercase=False,
                )
            else:
                code = "c"
        elif _is_work_phone_type(phone_type):
            if has_household_people:
                person_code = _format_custom_phone_code(
                    phone_code_label,
                    conflict_labels,
                    index == 0,
                    uppercase=True,
                )
                code = f"{person_code}w" if person_code else "wk"
            else:
                code = "wk"
        elif known_code is not None:
            code = known_code
        elif has_household_people:
            label = _phone_code_label_for_contact(
                contact,
                phone_type_value if is_custom_type else phone_code_label,
            )
            code = _format_custom_phone_code(label, conflict_labels, index == 0, uppercase=False)
        elif is_custom_type and not _phone_type_matches_name_line(phone_type_value, contact):
            code = _format_raw_phone_type_code(phone_type_value)
        else:
            code = ""
        custom_label = ""
        if is_custom_type and not _phone_type_matches_name_line(phone_type_value, contact):
            custom_label = phone_type_value
        formatted.append(
            {
                "value": _clean(item.get("phone_value")),
                "code": code,
                "type": _clean(phone_type),
                "custom_label": custom_label,
                "owner_given_name": _clean(owner_labels[index]) or person_label,
            }
        )
    return formatted


def _reformat_contact_phones_from_payload(contact: dict[str, Any]) -> list[dict[str, str]]:
    relationships: list[dict[str, str]] = []
    spouse = _clean(contact.get("spouse_name"))
    if spouse:
        relationships.append({"relation_type": "spouse", "relation_value": spouse})
    for child in contact.get("children") or []:
        child_name = _clean(child)
        if child_name:
            relationships.append({"relation_type": "child", "relation_value": child_name})
    for item in contact.get("other_relationships") or []:
        relation_value = _clean(item.get("relation_value"))
        if relation_value:
            relationships.append(
                {
                    "relation_type": _clean(item.get("relation_type")),
                    "relation_value": relation_value,
                }
            )
    source = {
        "given_name": contact.get("given_name"),
        "name": contact.get("name") or contact.get("label"),
        "relationships": relationships or list(contact.get("relationships") or []),
        "phones": [
            {
                "phone_type": phone.get("type"),
                "phone_value": phone.get("value"),
                "owner_given_name": phone.get("owner_given_name"),
            }
            for phone in contact.get("phones") or []
            if _clean(phone.get("value"))
        ],
    }
    return _format_contact_phones(source)


def _field_list_display_address(address: dict[str, Any], include_home_label: bool = False) -> str:
    if not isinstance(address, dict):
        formatted = _clean(address)
    else:
        enriched = enrich_address_parts(
            street_address=address.get("street_address"),
            extended_address=address.get("extended_address"),
            city=address.get("city"),
            region=address.get("region"),
            postal_code=address.get("postal_code"),
            formatted_address=address.get("formatted_address"),
        )
        formatted = _clean(enriched.get("formatted_address"))
    if not formatted:
        return ""
    address_type = _clean(address.get("address_type")) if isinstance(address, dict) else ""
    if address_type and (include_home_label or address_type.lower() not in {"home"}):
        return f"{address_type}: {formatted}"
    return formatted


def _field_list_address_column_width_pt(settings: dict[str, Any]) -> float:
    """Width available for name/address text after the reserved phone column."""
    from app.services.address_book_pdf_service import _text_width

    font_size = float(settings.get("base_font_size_pt") or 10.0)
    font_family = str(settings.get("font_family") or "Arial")
    column_pt = float(settings.get("column_width_in") or 4.0) * 72.0
    # Match CSS/JS/PDF: number + space + 2-letter code + space before the name.
    phone_pt = (
        _text_width("000-000-0000", font_size, font_family)
        + _text_width(" ", font_size, font_family)
        + _text_width("MM", font_size, font_family)
    )
    gap_pt = _text_width(" ", font_size, font_family)
    return max(72.0, column_pt - phone_pt - gap_pt)


def _field_list_address_display_lines(
    addresses: list[Any],
    *,
    max_width: float | None = None,
    text_width=None,
) -> list[str]:
    """Print address lines; keep street 1 and street 2 separate when both are set."""
    lines: list[str] = []
    for item in addresses or []:
        if isinstance(item, dict):
            raw_text = item.get("text")
            if raw_text is None:
                raw_text = _field_list_display_address(item)
            address_type = _clean(item.get("address_type"))
            formatted_only = ""
            if not (item.get("street_address") or item.get("extended_address") or item.get("city")):
                parsed_label, formatted_only = split_address_type_prefix(str(raw_text or ""))
                if not address_type:
                    address_type = parsed_label
            display_lines = address_display_lines(
                formatted_only,
                street_address=item.get("street_address") or "",
                extended_address=item.get("extended_address") or "",
                city=item.get("city") or "",
                region=item.get("region") or "",
                postal_code=item.get("postal_code") or "",
                label=address_type,
                max_width=max_width,
                text_width=text_width,
            )
        else:
            display_lines = address_display_lines(
                str(item or ""),
                max_width=max_width,
                text_width=text_width,
            )
        lines.extend(display_lines)
    return lines


_CHILD_RELATION_TYPES = {"son", "daughter", "child", "children"}
_SPOUSE_RELATION_TYPES = {"wife", "husband", "spouse"}


def _split_relation_names(relation_type: str, relation_value: str) -> list[str]:
    value = _clean(relation_value)
    if not value:
        return []
    if _clean(relation_type).lower() == "children":
        return [part.strip() for part in value.split(",") if part.strip()]
    return [value]


def _collect_contact_relationship_parts(
    relationships: list[dict[str, Any]],
) -> tuple[str, str, list[str], list[tuple[str, str]]]:
    spouse = ""
    spouse_relation_type = ""
    children: list[str] = []
    others: list[tuple[str, str]] = []
    for relation in relationships:
        relation_type = _clean(relation.get("relation_type")).lower()
        relation_value = _clean(relation.get("relation_value"))
        if not relation_value:
            continue
        if relation_type in _SPOUSE_RELATION_TYPES:
            spouse = relation_value
            spouse_relation_type = relation_type
        elif relation_type in _CHILD_RELATION_TYPES:
            children.extend(_split_relation_names(relation_type, relation_value))
        else:
            others.append((_clean(relation.get("relation_type")), relation_value))
    return spouse, spouse_relation_type, list(dict.fromkeys(children)), others


def _format_labeled_relationship(relation_type: str, relation_value: str) -> str:
    label = _clean(relation_type)
    value = _clean(relation_value)
    if not value:
        return ""
    if not label:
        return value
    return f"{label}: {value}"


def _format_relationship_suffix(children: list[str], others: list[tuple[str, str]]) -> str:
    parts: list[str] = []
    parts.extend(dict.fromkeys(children))
    seen_other_keys: set[tuple[str, str]] = set()
    for relation_type, relation_value in others:
        key = (relation_type.lower(), relation_value.lower())
        if key in seen_other_keys:
            continue
        seen_other_keys.add(key)
        formatted = _format_labeled_relationship(relation_type, relation_value)
        if formatted:
            parts.append(formatted)
    return ", ".join(parts)


def _format_name_suffix_for_parts(children: list[str], others: list[tuple[str, str]]) -> str:
    child_text = ", ".join(dict.fromkeys([_clean(child) for child in children if _clean(child)]))
    labeled_text = _format_relationship_suffix([], others)
    if child_text and labeled_text:
        return f"; {child_text}, {labeled_text}"
    if child_text:
        return f"; {child_text}"
    if labeled_text:
        return f", {labeled_text}"
    return ""


def _relationship_suffix_typed_segments(contact: dict[str, Any]) -> list[tuple[str, str]]:
    segments: list[tuple[str, str]] = []
    for child in contact.get("children") or []:
        child_name = _clean(child)
        if child_name:
            segments.append(("child", child_name))
    for item in contact.get("other_relationships") or []:
        label = _clean(item.get("relation_type"))
        name = _clean(item.get("relation_value"))
        if not name:
            continue
        formatted = _format_labeled_relationship(label, name) if label else name
        segments.append(("labeled", formatted))
    return segments


def _relationship_suffix_segments(contact: dict[str, Any]) -> list[str]:
    return [text for _, text in _relationship_suffix_typed_segments(contact)]


def _wrap_relationship_text(text: str, measure_width, max_width: float) -> list[str]:
    value = _clean(text)
    if not value:
        return []
    if measure_width(value) <= max_width:
        return [value]
    comma_parts = [part.strip() for part in value.split(",") if part.strip()]
    if len(comma_parts) > 1:
        lines: list[str] = []
        current = ""
        for part in comma_parts:
            candidate = part if not current else f"{current}, {part}"
            if current and measure_width(candidate) > max_width:
                wrapped_line = current if current.endswith(",") else f"{current},"
                lines.append(wrapped_line)
                if measure_width(part) > max_width:
                    lines.extend(_wrap_text_by_words(part, measure_width, max_width))
                    current = ""
                else:
                    current = part
            elif not current and measure_width(part) > max_width:
                lines.extend(_wrap_text_by_words(part, measure_width, max_width))
                current = ""
            else:
                current = candidate
        if current:
            lines.append(current)
        if lines:
            return lines
    return _wrap_text_by_words(value, measure_width, max_width)


def _pack_typed_suffix_lines(
    typed_segments: list[tuple[str, str]],
    measure_width,
    max_width: float,
    *,
    semicolon_on_previous_line: bool = False,
) -> list[str]:
    if not typed_segments:
        return []

    def append_wrapped(text: str) -> None:
        lines.extend(_wrap_relationship_text(text, measure_width, max_width))

    lines: list[str] = []
    current = ""
    is_first_suffix_line = True

    for kind, text in typed_segments:
        if not current:
            if semicolon_on_previous_line and is_first_suffix_line:
                prefix = ""
            elif kind == "child" and is_first_suffix_line:
                prefix = "; "
            elif is_first_suffix_line:
                prefix = " "
            else:
                prefix = ", "
            piece = f"{prefix}{text}"
        else:
            piece = f"{current}, {text}"

        if current and measure_width(piece) > max_width:
            is_first_suffix_line = False
            if kind == "child":
                lines.append(current if current.endswith(",") else f"{current},")
                next_text = text
            else:
                lines.append(current)
                next_text = f" {text}"
            if measure_width(next_text) > max_width:
                append_wrapped(next_text.lstrip())
                current = ""
            else:
                current = next_text
        elif not current:
            if measure_width(piece) > max_width:
                append_wrapped(piece.lstrip())
                is_first_suffix_line = False
                current = ""
            else:
                current = piece
        else:
            current = piece

    if current:
        if measure_width(current) > max_width:
            append_wrapped(current)
        else:
            lines.append(current)
    return lines


def _contact_name_head(contact: dict[str, Any]) -> str:
    value = _clean(contact.get("name") or contact.get("label") or "CONTACT, Name")
    semicolon_index = value.find("; ")
    if semicolon_index >= 0:
        return value[:semicolon_index]
    children = [_clean(child) for child in contact.get("children") or [] if _clean(child)]
    suffix = _format_name_suffix_for_parts(
        children,
        [
            (_clean(item.get("relation_type")), _clean(item.get("relation_value")))
            for item in contact.get("other_relationships") or []
        ],
    )
    if suffix and value.endswith(suffix):
        return value[: -len(suffix)]
    if suffix and ", " in suffix and value.endswith(suffix.lstrip(";")):
        return value[: -len(suffix.lstrip(";"))]
    return value


def _wrap_contact_name_lines(
    contact: dict[str, Any],
    measure_width,
    max_width: float,
) -> list[str]:
    typed_segments = _relationship_suffix_typed_segments(contact)
    head = _contact_name_head(contact)
    if not typed_segments:
        if measure_width(head) <= max_width:
            return [head]
        return _wrap_text_by_words(head, measure_width, max_width)
    children = [_clean(child) for child in contact.get("children") or [] if _clean(child)]
    others = [
        (_clean(item.get("relation_type")), _clean(item.get("relation_value")))
        for item in contact.get("other_relationships") or []
    ]
    suffix = _format_name_suffix_for_parts(children, others)
    full = f"{head}{suffix}"
    if measure_width(full) <= max_width:
        return [full]
    head_line = head
    semicolon_on_previous_line = False
    if typed_segments and typed_segments[0][0] == "child":
        head_with_semi = f"{head};"
        if measure_width(head_with_semi) <= max_width:
            head_line = head_with_semi
            semicolon_on_previous_line = True
    lines = [head_line]
    lines.extend(
        _pack_typed_suffix_lines(
            typed_segments,
            measure_width,
            max_width,
            semicolon_on_previous_line=semicolon_on_previous_line,
        )
    )
    return lines


def _wrap_text_by_words(text: str, measure_width, max_width: float) -> list[str]:
    words = _clean(text).split()
    if not words:
        return []
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and measure_width(candidate) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    if lines:
        return lines
    if measure_width(text) <= max_width:
        return [text]
    return _wrap_relationship_text(text, measure_width, max_width)


def _build_contact_display_name(
    family: str,
    given: str,
    spouse: str,
    children: list[str],
    others: list[tuple[str, str]],
) -> str:
    name = f"{family.upper()}, {given}".strip(", ") if family else given
    if spouse and given and spouse.lower() not in given.lower():
        name = f"{family.upper()}, {given} & {spouse}".strip(", ") if family else f"{given} & {spouse}"
    suffix = _format_name_suffix_for_parts(children, others)
    if suffix:
        name = f"{name}{suffix}" if name else suffix.lstrip("; ")
    return name or "CONTACT, Name"


def _preview_relationship_name(contact: dict[str, Any]) -> str:
    family = _clean(contact.get("family_name"))
    given = _clean(contact.get("given_name"))
    spouse, _, children, others = _collect_contact_relationship_parts(contact.get("relationships", []))
    return _build_contact_display_name(family, given, spouse, children, others)


def _contact_sort_label(contact: dict[str, Any]) -> str:
    return " ".join(
        part
        for part in (
            _clean(contact.get("family_name")).upper(),
            _clean(contact.get("given_name")),
        )
        if part
    ) or f"Contact {contact.get('id')}"


def _meeting_role_sort_key(contact: dict[str, Any], has_meeting_home: bool) -> tuple[int, str, int]:
    flag = _clean(contact.get("mtg_home_elder_flag"))
    if flag.startswith("1"):
        rank = 0
    elif flag.startswith("2"):
        rank = 1 if has_meeting_home else 0
    else:
        rank = 2 if has_meeting_home else 1
    return (rank, _clean(contact.get("sort_label")).lower(), int(contact.get("id") or 0))


def _format_preview_contact(contact: dict[str, Any]) -> dict[str, Any]:
    family = _clean(contact.get("family_name"))
    given = _clean(contact.get("given_name"))
    name = f"{family.upper()}, {given}".strip(", ") if family else given
    phones = []
    for item in _format_contact_phones(contact):
        value = _clean(item.get("value"))
        code = _clean(item.get("code"))
        if value:
            phones.append(f"{value} {code}".rstrip())
    addresses = [_field_list_display_address(item) for item in contact.get("addresses", [])]
    relationships = []
    for item in contact.get("relationships", []):
        relation_type = _clean(item.get("relation_type")).lower()
        relation_value = _clean(item.get("relation_value"))
        if not relation_value:
            continue
        if relation_type in _CHILD_RELATION_TYPES or relation_type in _SPOUSE_RELATION_TYPES:
            relationships.append(" ".join(part for part in (_clean(item.get("relation_type")), relation_value) if part))
        else:
            formatted = _format_labeled_relationship(_clean(item.get("relation_type")), relation_value)
            if formatted:
                relationships.append(formatted)
    emails = [_clean(item.get("email_value")) for item in contact.get("emails", [])]
    fields = contact.get("fields_list") or _clean(contact.get("fields_text")).splitlines()
    meetings = contact.get("meetings_list") or _clean(contact.get("meetings_text")).splitlines()
    return {
        "id": int(contact.get("id") or 0),
        "label": name or f"Contact {contact.get('id')}",
        "field_name": fields[0] if fields else "Field",
        "meeting_name": meetings[0] if meetings else "Meeting",
        "name": name or "CONTACT, Name",
        "phone_number": [item for item in phones if item],
        "address": [item for item in addresses if item],
        "relationships": [_preview_relationship_name(contact), *[item for item in relationships if item]],
        "emails": [item for item in emails if item],
        "page_number": ["Page 1"],
    }


_NOTE_STYLE_TAGS = {
    "b": "bold",
    "strong": "bold",
    "i": "italic",
    "em": "italic",
    "u": "underline",
}
_KNOWN_NOTE_TAG = re.compile(
    r"</?(?:strong|b|em|i|u|br)\b[^>]*>"
    r"|</span\s*>"
    r"|<span\b[^>]*\bdata-note-size-delta\s*=\s*['\"]?-?\d+(?:\.\d+)?['\"][^>]*>",
    re.IGNORECASE,
)
_NOTE_BREAK = re.compile(r"<br\s*/?>|</(?:div|p)\s*>", re.IGNORECASE)
_NOTE_BLOCK_OPEN = re.compile(r"<(?:div|p)[^>]*>", re.IGNORECASE)
NOTE_SIZE_STEP = 0.5
NOTE_SIZE_MIN_DELTA = -6.0
NOTE_SIZE_MAX_DELTA = 8.0
NOTE_PRINT_MIN_PT = 6.0
NOTE_PRINT_MAX_PT = 18.0


def _parse_note_size_delta(value: object) -> float | None:
    raw = str(value or "").strip()
    if raw == "":
        return None
    try:
        number = float(raw)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    stepped = round(number / NOTE_SIZE_STEP) * NOTE_SIZE_STEP
    return max(NOTE_SIZE_MIN_DELTA, min(NOTE_SIZE_MAX_DELTA, stepped))


def _format_note_size_delta(value: object) -> str:
    parsed = _parse_note_size_delta(value)
    if parsed is None or parsed == 0:
        return ""
    return str(int(parsed) if parsed == int(parsed) else parsed)


def print_note_font_size(base_size: float, run: dict[str, Any] | None = None) -> float:
    """Return a printed note size: book font plus the line's 0.5pt delta, clamped."""
    try:
        delta = float((run or {}).get("size_delta") or 0)
    except (TypeError, ValueError):
        delta = 0.0
    try:
        base = float(base_size)
    except (TypeError, ValueError):
        base = 10.0
    return max(NOTE_PRINT_MIN_PT, min(NOTE_PRINT_MAX_PT, base + delta))


class _InlineNoteParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.runs: list[dict[str, Any]] = []
        self.active: set[str] = set()
        self.size_stack: list[float] = []

    def _current_size(self) -> float:
        return self.size_stack[-1] if self.size_stack else 0.0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = str(tag or "").lower()
        style = _NOTE_STYLE_TAGS.get(name)
        if style:
            self.active.add(style)
            return
        if name != "span":
            return
        attr_map = {str(key or "").lower(): (value or "") for key, value in attrs}
        parsed = _parse_note_size_delta(attr_map.get("data-note-size-delta"))
        self.size_stack.append(self._current_size() if parsed is None else parsed)

    def handle_endtag(self, tag: str) -> None:
        name = str(tag or "").lower()
        style = _NOTE_STYLE_TAGS.get(name)
        if style:
            self.active.discard(style)
            return
        if name == "span" and self.size_stack:
            self.size_stack.pop()

    def handle_data(self, data: str) -> None:
        text = unescape(data)
        if not text:
            return
        run = {
            "text": text,
            "bold": "bold" in self.active,
            "italic": "italic" in self.active,
            "underline": "underline" in self.active,
            "size_delta": self._current_size(),
        }
        if self.runs:
            previous = self.runs[-1]
            if (
                previous["bold"] == run["bold"]
                and previous["italic"] == run["italic"]
                and previous["underline"] == run["underline"]
                and float(previous.get("size_delta") or 0) == float(run["size_delta"])
            ):
                previous["text"] += text
                return
        self.runs.append(run)


def _prepare_inline_html(value: object) -> str:
    text = str(value or "")
    if not _KNOWN_NOTE_TAG.search(text):
        return escape(text)
    placeholders: list[str] = []

    def hold(match: re.Match[str]) -> str:
        placeholders.append(match.group(0))
        return f"\ue000{len(placeholders) - 1}\ue001"

    held = _KNOWN_NOTE_TAG.sub(hold, text).replace("<", "&lt;").replace(">", "&gt;")
    for index, tag in enumerate(placeholders):
        held = held.replace(f"\ue000{index}\ue001", tag)
    return held


def _inline_style_runs(value: object) -> list[dict[str, Any]]:
    prepared = _prepare_inline_html(value)
    if not prepared:
        return []
    parser = _InlineNoteParser()
    parser.feed(prepared)
    parser.close()
    return [run for run in parser.runs if run.get("text")]


def _drop_plain_chars(runs: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if count <= 0:
        return [dict(run) for run in runs]
    remaining = count
    kept: list[dict[str, Any]] = []
    for run in runs:
        text = str(run.get("text") or "")
        if remaining >= len(text):
            remaining -= len(text)
            continue
        if remaining:
            text = text[remaining:]
            remaining = 0
        if text:
            kept.append({**run, "text": text})
    return kept


def _rstrip_style_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept = [dict(run) for run in runs if str(run.get("text") or "")]
    while kept:
        text = str(kept[-1]["text"])
        trimmed = text.rstrip()
        if trimmed == text:
            break
        if trimmed:
            kept[-1] = {**kept[-1], "text": trimmed}
            break
        kept.pop()
    return kept


def _note_source_lines(notes: object) -> list[str]:
    text = str(notes or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _NOTE_BLOCK_OPEN.sub("", text)
    text = _NOTE_BREAK.sub("\n", text)
    return text.split("\n")


def _parse_print_book_notes(notes: Any) -> tuple[list[str], list[str]]:
    """Extract address-book print directives from a contact's Notes.

    Any Notes line beginning with ``pb/`` is printed next to the name in the
    book; any line beginning with ``pa/`` is printed after the address lines.
    The prefix match is case-insensitive and tolerates a space after the slash
    (so ``pa/text`` and ``pa/ text`` behave the same). Bold, italic, underline,
    and per-line font-size deltas on the note text are kept.
    """
    name_line_values: list[str] = []
    after_address_values: list[str] = []
    for raw_line in _note_source_lines(notes):
        runs = _rstrip_style_runs(_inline_style_runs(raw_line))
        plain = "".join(str(run.get("text") or "") for run in runs)
        trimmed = plain.strip()
        lowered = trimmed.lower()
        if lowered.startswith("pb/"):
            target = name_line_values
        elif lowered.startswith("pa/"):
            target = after_address_values
        else:
            continue
        lead = len(plain) - len(plain.lstrip())
        value_runs = _rstrip_style_runs(_drop_plain_chars(runs, lead + 3))
        value_plain = "".join(str(run.get("text") or "") for run in value_runs)
        strip_lead = len(value_plain) - len(value_plain.lstrip())
        value_runs = _rstrip_style_runs(_drop_plain_chars(value_runs, strip_lead))
        if "".join(str(run.get("text") or "") for run in value_runs).strip():
            target.append(_style_runs_html(value_runs))
    return name_line_values, after_address_values


def print_book_note_runs(value: object, *, default_gap: str = "") -> list[dict[str, Any]]:
    """Return styled pieces of a pb/ or pa/ note, including typed spaces."""
    runs = _rstrip_style_runs(_inline_style_runs(value))
    plain = "".join(str(run.get("text") or "") for run in runs)
    if not plain.strip():
        return []
    gap_len = len(plain) - len(plain.lstrip(" "))
    body = _drop_plain_chars(runs, gap_len)
    if not "".join(str(run.get("text") or "") for run in body).strip():
        return []
    gap = plain[:gap_len] or default_gap
    styled = [{"text": gap, "bold": False, "italic": False, "underline": False, "size_delta": 0.0}] if gap else []
    styled.extend(body)
    return styled


def print_book_note_display(value: object, *, default_gap: str = "") -> str:
    """Return the printed note text without HTML tags."""
    return "".join(str(run.get("text") or "") for run in print_book_note_runs(value, default_gap=default_gap))


def _style_runs_html(runs: list[dict[str, Any]], *, preserve_spaces: bool = False) -> str:
    parts: list[str] = []
    for run in runs:
        text = _print_escape(run.get("text"))
        if preserve_spaces:
            text = text.replace(" ", "&nbsp;")
        if run.get("underline"):
            text = f"<u>{text}</u>"
        if run.get("italic"):
            text = f"<em>{text}</em>"
        if run.get("bold"):
            text = f"<strong>{text}</strong>"
        size_label = _format_note_size_delta(run.get("size_delta"))
        if size_label:
            text = (
                f'<span data-note-size-delta="{size_label}" '
                f'style="font-size: calc(1em + {size_label}pt)">{text}</span>'
            )
        parts.append(text)
    return "".join(parts)


def contact_notes_html(value: object) -> Markup:
    """Render Notes for the page, keeping bold, italic, underline, size, and line breaks."""
    lines = _note_source_lines(value)
    rendered: list[str] = []
    for index, line in enumerate(lines):
        if index:
            rendered.append("<br>")
        rendered.append(_style_runs_html(_inline_style_runs(line)))
    return Markup("".join(rendered))


def _print_note_html(value: object, *, default_gap: str = "") -> str:
    return _style_runs_html(print_book_note_runs(value, default_gap=default_gap), preserve_spaces=True)


def _field_list_contact_payload(contact: dict[str, Any]) -> dict[str, Any]:
    family = _clean(contact.get("family_name"))
    given = _clean(contact.get("given_name"))
    spouse, spouse_relation_type, children, others = _collect_contact_relationship_parts(contact.get("relationships", []))
    name = _build_contact_display_name(family, given, spouse, children, others)
    priority_names = [given, spouse, *children, *[value for _, value in others]]
    phones = _order_household_phones(_format_contact_phones(contact), priority_names)
    addresses = []
    address_entries = []
    for item in contact.get("addresses", []):
        address = _field_list_display_address(item)
        coordinates = _clean(item.get("coordinates"))
        if address:
            addresses.append(address)
            enriched = enrich_address_parts(
                street_address=item.get("street_address"),
                extended_address=item.get("extended_address"),
                city=item.get("city"),
                region=item.get("region"),
                postal_code=item.get("postal_code"),
                formatted_address=item.get("formatted_address") or address,
            )
            address_entries.append(
                {
                    "text": address,
                    "address_type": _clean(item.get("address_type")),
                    "street_address": enriched["street_address"],
                    "extended_address": enriched["extended_address"],
                    "city": enriched["city"],
                    "region": enriched["region"],
                    "postal_code": enriched["postal_code"],
                    "coordinates": coordinates,
                    "has_coordinates": bool(coordinates),
                }
            )
    fields = contact.get("fields_list") or _clean(contact.get("fields_text")).splitlines()
    meetings = contact.get("meetings_list") or _clean(contact.get("meetings_text")).splitlines()
    print_book_name, print_after_address = _parse_print_book_notes(contact.get("notes"))
    payload = {
        "id": int(contact.get("id") or 0),
        "label": name or _contact_sort_label(contact),
        "sort_label": _contact_sort_label(contact),
        "mtg_home_elder_flag": _clean(contact.get("mtg_home_elder_flag")),
        "do_not_print": bool(contact.get("do_not_print") or 0),
        "family_name": family,
        "given_name": given,
        "spouse_name": spouse,
        "spouse_relation_type": spouse_relation_type,
        "children": list(dict.fromkeys(children)),
        "other_relationships": [{"relation_type": relation_type, "relation_value": relation_value} for relation_type, relation_value in others],
        "fields": fields,
        "meetings": meetings,
        "name": name or "CONTACT, Name",
        "phones": phones,
        "addresses": addresses,
        "address_entries": address_entries,
        "print_book_name": print_book_name,
        "print_after_address": print_after_address,
    }
    payload["phones"] = _reformat_contact_phones_from_payload(payload)
    return payload


def _field_list_household_key(contact: dict[str, Any]) -> tuple[str, str]:
    address = re.sub(r"[^a-z0-9]+", "", _clean((contact.get("addresses") or [""])[0]).lower())
    return (
        _clean(contact.get("family_name") or str(contact.get("name") or "").split(",", 1)[0]).lower(),
        address or f"id:{int(contact.get('id') or 0)}",
    )


def _field_list_spouse_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_given = _clean(left.get("given_name")).lower()
    right_given = _clean(right.get("given_name")).lower()
    left_spouse = _clean(left.get("spouse_name")).lower()
    right_spouse = _clean(right.get("spouse_name")).lower()
    return bool(
        (left_given and right_spouse == left_given)
        or (right_given and left_spouse == right_given)
        or (left_spouse and right_spouse and left_spouse == right_given and right_spouse == left_given)
    )


def _field_list_merge_contact_phones(contacts: list[dict[str, Any]]) -> list[dict[str, str]]:
    merged = []
    seen = set()
    for contact in contacts:
        for phone in contact.get("phones") or []:
            value = _clean(phone.get("value"))
            code = _clean(phone.get("code"))
            key = (value, code)
            if value and key not in seen:
                seen.add(key)
                merged.append(
                    {
                        "value": value,
                        "code": code,
                        "type": _clean(phone.get("type")),
                        "custom_label": _clean(phone.get("custom_label")),
                        "owner_given_name": _clean(phone.get("owner_given_name")) or _clean(contact.get("given_name")),
                    }
                )
    return merged


def _order_household_phones(
    phones: list[dict[str, Any]],
    priority_names: list[Any],
) -> list[dict[str, Any]]:
    """Sort phones so the spouses print first, then children, then everyone else.

    Ordering is by each phone's owner (``owner_given_name``) against a priority
    list of household members. Phones whose owner is not on the list keep their
    original relative position after the recognized members. The sort is stable,
    so multiple phones for the same person stay in their original order.
    """
    ordered_keys: list[str] = []
    seen: set[str] = set()
    for name in priority_names:
        key = _phone_label_letters(_clean(name)).lower()
        if key and key not in seen:
            seen.add(key)
            ordered_keys.append(key)
    rank_of = {key: index for index, key in enumerate(ordered_keys)}
    default_rank = len(ordered_keys)

    def sort_key(entry: tuple[int, dict[str, Any]]) -> tuple[int, int]:
        original_index, phone = entry
        owner_key = _phone_label_letters(_clean(phone.get("owner_given_name"))).lower()
        return (rank_of.get(owner_key, default_rank), original_index)

    return [phone for _, phone in sorted(enumerate(phones), key=sort_key)]


def _field_list_spouse_ordered_contacts(contacts: list[dict[str, Any]], husband: str, wife: str) -> list[dict[str, Any]]:
    husband_key = _clean(husband).lower()
    wife_key = _clean(wife).lower()
    def order_key(contact: dict[str, Any]) -> tuple[int, int]:
        given = _clean(contact.get("given_name")).lower()
        if husband_key and given == husband_key:
            rank = 0
        elif wife_key and given == wife_key:
            rank = 1
        else:
            rank = 2
        return (rank, int(contact.get("id") or 0))
    return sorted(contacts, key=order_key)


def _field_list_merged_household_contact(contacts: list[dict[str, Any]]) -> dict[str, Any]:
    if len(contacts) <= 1:
        return contacts[0]
    base = dict(contacts[0])
    family = _clean(base.get("family_name") or str(base.get("name") or "").split(",", 1)[0]).upper()
    husband = ""
    wife = ""
    unassigned_givens: list[str] = []
    children: list[str] = []
    others: list[tuple[str, str]] = []
    # When both spouse cards are present, always use each person's own given_name.
    # Do not prefer the other card's spouse relationship value (it can be stale after a rename).
    for contact in contacts:
        given = _clean(contact.get("given_name"))
        relation_type = _clean(contact.get("spouse_relation_type")).lower()
        if not given:
            continue
        if relation_type == "wife" and not husband:
            # This contact lists a wife → this contact is the husband.
            husband = given
        elif relation_type == "husband" and not wife:
            # This contact lists a husband → this contact is the wife.
            wife = given
        else:
            unassigned_givens.append(given)
    for given in unassigned_givens:
        if given in {husband, wife}:
            continue
        if not husband:
            husband = given
        elif not wife:
            wife = given
    # Only fall back to spouse relationship text when that partner's card is missing.
    if not husband or not wife:
        for contact in contacts:
            spouse = _clean(contact.get("spouse_name"))
            relation_type = _clean(contact.get("spouse_relation_type")).lower()
            if not spouse:
                continue
            if relation_type == "wife" and not wife:
                wife = spouse
            elif relation_type == "husband" and not husband:
                husband = spouse
            elif not husband:
                husband = spouse
            elif not wife:
                wife = spouse
    for contact in contacts:
        children.extend([_clean(item) for item in contact.get("children") or [] if _clean(item)])
        for item in contact.get("other_relationships") or []:
            relation_type = _clean(item.get("relation_type"))
            relation_value = _clean(item.get("relation_value"))
            if relation_value:
                others.append((relation_type, relation_value))
    if husband and wife:
        name = f"{family}, {husband} & {wife}" if family else f"{husband} & {wife}"
    else:
        name = _clean(base.get("name") or base.get("label"))
    unique_children = list(dict.fromkeys(children))
    suffix = _format_name_suffix_for_parts(unique_children, others)
    if suffix:
        name = f"{name}{suffix}" if name else suffix.lstrip("; ")
    ordered_contacts = _field_list_spouse_ordered_contacts(contacts, husband, wife)
    meeting_flags = [_clean(contact.get("mtg_home_elder_flag")) for contact in contacts]
    if any(flag.startswith("1") for flag in meeting_flags):
        base["mtg_home_elder_flag"] = "1"
    elif any(flag.startswith("2") for flag in meeting_flags):
        base["mtg_home_elder_flag"] = "2"
    base["name"] = name or _clean(base.get("name") or base.get("label") or "CONTACT, Name")
    base["label"] = base["name"]
    base["sort_label"] = f"{family} {husband or wife}".strip() or _clean(base.get("sort_label"))
    base["given_name"] = husband or _clean(base.get("given_name"))
    base["spouse_name"] = wife
    base["spouse_relation_type"] = "wife" if wife else _clean(base.get("spouse_relation_type"))
    base["children"] = unique_children
    seen_other_keys: set[tuple[str, str]] = set()
    merged_others: list[dict[str, str]] = []
    for relation_type, relation_value in others:
        key = (relation_type.lower(), relation_value.lower())
        if key in seen_other_keys:
            continue
        seen_other_keys.add(key)
        merged_others.append({"relation_type": relation_type, "relation_value": relation_value})
    base["other_relationships"] = merged_others
    merged_print_book_name: list[str] = []
    merged_print_after_address: list[str] = []
    for contact in contacts:
        merged_print_book_name.extend(contact.get("print_book_name") or [])
        merged_print_after_address.extend(contact.get("print_after_address") or [])
    base["print_book_name"] = list(dict.fromkeys(merged_print_book_name))
    base["print_after_address"] = list(dict.fromkeys(merged_print_after_address))
    priority_names = [husband, wife, *unique_children, *[value for _, value in others]]
    base["phones"] = _order_household_phones(
        _field_list_merge_contact_phones(ordered_contacts), priority_names
    )
    base["phones"] = _reformat_contact_phones_from_payload(base)
    return base


def _field_list_merge_households(contacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    order: list[tuple[str, str]] = []
    for contact in contacts:
        if bool(contact.get("do_not_print")):
            continue
        key = _field_list_household_key(contact)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(contact)
    merged = []
    for key in order:
        group = grouped[key]
        visited: set[int] = set()
        for index, contact in enumerate(group):
            if index in visited:
                continue
            spouse_group = [contact]
            visited.add(index)
            for other_index in range(index + 1, len(group)):
                if other_index in visited:
                    continue
                if _field_list_spouse_match(contact, group[other_index]):
                    spouse_group.append(group[other_index])
                    visited.add(other_index)
            if len(spouse_group) > 1:
                merged.append(_field_list_merged_household_contact(spouse_group))
            else:
                merged.append(contact)
    return merged


def invalidate_field_list_print_payload_cache() -> None:
    """Kept for call sites; payloads are rebuilt from SQLite on every page open."""
    return None


def _field_list_picker_payload() -> dict[str, Any]:
    from app.services.contact_service import (
        UNASSIGNED_FIELD_NAME,
        UNASSIGNED_MEETING_NAME,
        list_contacts_for_field_list_print,
    )

    contacts = []
    for detail in list_contacts_for_field_list_print():
        if detail and not bool(detail.get("do_not_print") or 0):
            contacts.append(_field_list_contact_payload(detail))

    fields: dict[str, dict[str, Any]] = {}
    for contact in contacts:
        assigned_fields = [_clean(field) for field in contact["fields"] if _clean(field)] or [UNASSIGNED_FIELD_NAME]
        assigned_meetings = [_clean(meeting) for meeting in contact["meetings"] if _clean(meeting)] or [UNASSIGNED_MEETING_NAME]
        for field in assigned_fields:
            field_bucket = fields.setdefault(field, {"name": field, "meetings": {}})
            for meeting in assigned_meetings:
                field_bucket["meetings"].setdefault(meeting, []).append(contact)

    from app.services.field_end_list_service import field_end_list_names_by_field

    settings = _load_address_book_settings()
    field_end_lists_by_field = field_end_list_names_by_field()
    for field_name in field_end_lists_by_field:
        fields.setdefault(field_name, {"name": field_name, "meetings": {}})
    assigned_field_names = [name for name in fields if name != UNASSIGNED_FIELD_NAME]
    ordered_field_names = order_field_names(assigned_field_names, settings.get("print_order_json"))
    if UNASSIGNED_FIELD_NAME in fields:
        ordered_field_names.append(UNASSIGNED_FIELD_NAME)
    normalized_fields = []
    for field_name in ordered_field_names:
        field_end_names = field_end_lists_by_field.get(field_name) or []
        field_end_name_set = {name for name in field_end_names}
        assigned_meeting_names = [name for name in fields[field_name]["meetings"] if name != UNASSIGNED_MEETING_NAME]
        regular_meeting_names = [
            meeting_name
            for meeting_name in order_meeting_names(
                assigned_meeting_names,
                settings.get("print_order_json"),
                field_name,
            )
            if meeting_name not in field_end_name_set
        ]
        ordered_meeting_names = regular_meeting_names + list(field_end_names)
        if UNASSIGNED_MEETING_NAME in fields[field_name]["meetings"]:
            ordered_meeting_names.append(UNASSIGNED_MEETING_NAME)
        meetings = []
        for meeting_name in ordered_meeting_names:
            meeting_bucket_contacts = _field_list_merge_households(fields[field_name]["meetings"].get(meeting_name) or [])
            has_meeting_home = any(_clean(item.get("mtg_home_elder_flag")).startswith("1") for item in meeting_bucket_contacts)
            sorted_contacts = sorted(
                meeting_bucket_contacts,
                key=lambda item: _meeting_role_sort_key(item, has_meeting_home),
            )
            meetings.append(
                {
                    "name": meeting_name,
                    "contacts": sorted_contacts,
                    "is_field_end_list": meeting_name in field_end_name_set,
                    "field_end_list_order": field_end_names.index(meeting_name) + 1 if meeting_name in field_end_name_set else 0,
                }
            )
        normalized_fields.append({"name": field_name, "meetings": meetings})

    return {"fields": normalized_fields}


def _meeting_row_print_font_size_pt(row: dict[str, Any]) -> float | None:
    """Resolved print size for a meeting row (explicit override or inherited table size)."""
    for key in ("effective_font_size_pt", "font_size_override_pt", "font_size_pt"):
        parsed = _parse_font_size_value(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _flow_column_segments(column: dict[str, Any]) -> list[dict[str, Any]]:
    segments = column.get("segments")
    if isinstance(segments, list) and segments:
        return segments
    return [{"html": column.get("html"), "align": column.get("align") or "left"}]


def _flow_print_layer_payloads(flow_columns: list) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    primaries: list[dict[str, Any]] = []
    stacked: list[dict[str, Any]] = []
    for column in flow_columns or []:
        layout_cell = column.get("layout_cell") or {}
        is_stacked = bool(int(layout_cell.get("stack_under_cell_id") or 0))
        html = str(column.get("combined_text") or "")
        align = _clean(layout_cell.get("text_align")) or "left"
        payload = {
            "cell_id": int(layout_cell.get("id") or 0),
            "parent_cell_id": int(layout_cell.get("stack_under_cell_id") or 0) if is_stacked else 0,
            "col_start": int(column.get("col_start") or 1),
            "col_span": int(column.get("col_span") or 1),
            "segments": [{"html": html, "align": align}],
            "html": html,
            "align": align,
        }
        if is_stacked:
            stacked.append(payload)
        else:
            primaries.append(payload)
    return primaries, stacked


def _flow_row_print_layout(flow_columns: list) -> dict[str, Any]:
    primaries, stacked = _flow_print_layer_payloads(flow_columns)
    primary_line_counts = {
        int(column["cell_id"]): _flow_column_html_line_count(column)
        for column in primaries
        if int(column.get("cell_id") or 0)
    }
    row_extent = max(primary_line_counts.values()) if primary_line_counts else 1
    for column in primaries:
        lines = primary_line_counts.get(int(column["cell_id"]), _flow_column_html_line_count(column))
        column["grid_row_start"] = 1
        column["grid_row_span"] = lines
    for column in stacked:
        parent_id = int(column.get("parent_cell_id") or 0)
        parent_lines = primary_line_counts.get(parent_id, 1)
        lines = _flow_column_html_line_count(column)
        column["grid_row_start"] = parent_lines + 1
        column["grid_row_span"] = lines
        row_extent = max(row_extent, parent_lines + lines)
    return {
        "columns": primaries,
        "stacked_columns": stacked,
        "flow_row_line_count": max(1, row_extent),
    }


def _flow_row_print_line_count(row: dict[str, Any]) -> int:
    stored = row.get("flow_row_line_count")
    if stored is not None:
        try:
            return max(1, int(stored))
        except (TypeError, ValueError):
            pass
    primaries = row.get("columns") or []
    stacked = row.get("stacked_columns") or []
    primary_line_counts = {
        int(column.get("cell_id") or 0): _flow_column_html_line_count(column)
        for column in primaries
        if int(column.get("cell_id") or 0)
    }
    row_extent = max(primary_line_counts.values()) if primary_line_counts else 1
    for column in stacked:
        parent_id = int(column.get("parent_cell_id") or 0)
        parent_lines = primary_line_counts.get(parent_id, 1)
        lines = _flow_column_html_line_count(column)
        row_extent = max(row_extent, parent_lines + lines)
    return max(1, row_extent)


def _flow_print_column_payloads(flow_columns: list) -> list[dict[str, Any]]:
    primaries, stacked = _flow_print_layer_payloads(flow_columns)
    return [*primaries, *stacked]


def _flow_layer_html_line_count(columns: list[dict[str, Any]]) -> int:
    if not columns:
        return 0
    return max([1, *[_flow_column_html_line_count(column) for column in columns]])


def _flow_column_html_line_count(column: dict[str, Any]) -> int:
    segments = column.get("segments")
    if isinstance(segments, list) and segments:
        counts = [
            _field_list_print_html_line_count(segment.get("html"))
            for segment in segments
            if str(segment.get("html") or "").strip()
        ]
        return max(1, sum(counts)) if counts else 1
    return max(1, _field_list_print_html_line_count(column.get("html")))


_MEETINGDATA_PRINT_IMPORT_ATTEMPTED = False


def _try_import_meetingdata_for_print() -> bool:
    """Pull Meeting Data from Drive once if this computer never imported it."""
    global _MEETINGDATA_PRINT_IMPORT_ATTEMPTED
    if _MEETINGDATA_PRINT_IMPORT_ATTEMPTED:
        return False
    _MEETINGDATA_PRINT_IMPORT_ATTEMPTED = True
    try:
        from app.services.google_sync_service import (
            get_google_sync_summary,
            import_shared_meetingdata_from_google_drive,
        )

        account = (get_google_sync_summary().get("account") or {})
        if str(account.get("status") or "") != "connected" or not account.get("drive_scope_ready"):
            return False
        return bool(import_shared_meetingdata_from_google_drive(export_if_missing=False))
    except Exception:
        return False


def _field_list_meeting_preview_payload() -> dict[str, Any]:
    try:
        payload = _field_list_meeting_preview_payload_from_meetingdata()
    except Exception:
        payload = {"sections": []}
    if payload.get("sections"):
        return payload
    if _try_import_meetingdata_for_print():
        try:
            return _field_list_meeting_preview_payload_from_meetingdata()
        except Exception:
            return {"sections": []}
    return payload


def _field_list_meeting_preview_payload_from_meetingdata() -> dict[str, Any]:
    from app.services.meeting_v2_service import get_meeting_v2_section_print_preview, list_meeting_v2_sections

    # list_meeting_v2_sections() already runs ensure once; skip repeating it per section.
    previews: list[dict[str, Any]] = []
    for section_row in list_meeting_v2_sections():
        if int(section_row.get("cell_count") or 0) <= 0:
            continue
        section = get_meeting_v2_section_print_preview(int(section_row["id"]), ensure=False)
        if not section:
            continue
        rows = []
        print_blocks = list(section.get("print_blocks") or [])
        grouped_block_ids: dict[int, str] = {}
        for block_index, block in enumerate(print_blocks):
            if not any(bool((item or {}).get("group_with_next")) for item in block.get("items") or []):
                continue
            group_id = f"block-{block_index}-group-with-table-below"
            grouped_block_ids[block_index] = group_id
            for next_index in range(block_index + 1, len(print_blocks)):
                grouped_block_ids[next_index] = group_id
                next_block = print_blocks[next_index]
                if any(_clean((item or {}).get("kind")) == "table" for item in next_block.get("items") or []):
                    break

        for block_index, block in enumerate(print_blocks):
            block_keep_together = bool(block.get("keep_together"))
            print_preview = section.get("print_preview") or {}
            section_column_gap_px = int(print_preview.get("column_gap_px") or 6)
            section_preview_printable_width_px = float(print_preview.get("preview_printable_width_px") or 0)
            section_grid_column_count = int(print_preview.get("column_count") or 28)
            for item_index, item in enumerate(block.get("items") or []):
                item_kind = _clean(item.get("kind"))
                keep_together_group = grouped_block_ids.get(block_index) or (
                    f"block-{block_index}"
                    if block_keep_together
                    else f"block-{block_index}-item-{item_index}"
                    if item_kind == "table"
                    else ""
                )
                for row in item.get("rows") or []:
                    font_size_value = _meeting_row_print_font_size_pt(row)
                    row_style = {
                        "row_id": int(row.get("id") or 0),
                        "row_order": int(row.get("row_order") or 0),
                        "font_size_pt": font_size_value,
                        "effective_font_size_pt": font_size_value,
                        "line_height": float(print_preview.get("line_height") or 1.2),
                        "column_gap_px": section_column_gap_px,
                        "preview_printable_width_px": section_preview_printable_width_px,
                        "grid_column_count": section_grid_column_count,
                        "is_underlined_row": bool(row.get("is_underlined_row")),
                        "is_table_start_row": bool(row.get("is_table_start_row")),
                        "is_table_end_row": bool(row.get("is_table_end_row")),
                        "no_meeting_association": bool(row.get("no_meeting_association")),
                        "print_new_page": bool(row.get("print_new_page")),
                        "keep_together_group": keep_together_group,
                    }
                    if row.get("is_blank_row"):
                        rows.append({"kind": "blank", "cells": [], **row_style})
                    elif row.get("is_divider_row"):
                        rows.append({"kind": "divider", "cells": [], **row_style})
                    elif row.get("is_column_flow_row"):
                        flow_layout = _flow_row_print_layout(row.get("flow_columns") or [])
                        rows.append({"kind": "flow", **flow_layout, **row_style})
                    else:
                        cells = []
                        for cell in row.get("cells") or []:
                            cells.append(
                                {
                                    "html": str(cell.get("text_value") or ""),
                                    "align": _clean(cell.get("text_align")) or "left",
                                    "col_start": int(cell.get("col_start") or 1),
                                    "col_span": int(cell.get("col_span") or 1),
                                    "is_bold": bool(cell.get("is_bold")),
                                    "is_italic": bool(cell.get("is_italic")),
                                    "is_title": bool(cell.get("is_title")),
                                    "is_underlined": bool(cell.get("is_underlined")),
                                }
                            )
                        if cells:
                            rows.append({"kind": "cells", "cells": cells, **row_style})
        previews.append(
            {
                "field": str(section.get("field_name") or ""),
                "meeting": str(section.get("meeting_name") or ""),
                "field_key": _norm_lookup(section.get("field_name")),
                "meeting_key": _norm_lookup(section.get("meeting_name")),
                "rows": rows,
            }
        )
    return {"sections": previews}


def _field_list_preview_contacts_payload(picker_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    contacts = []
    for field in ((picker_payload or _field_list_picker_payload()).get("fields") or []):
        for meeting in field.get("meetings") or []:
            for contact in meeting.get("contacts") or []:
                contacts.append(
                    {
                        "id": int(contact.get("id") or 0),
                        "label": str(contact.get("label") or contact.get("name") or ""),
                        "field_name": str(field.get("name") or ""),
                        "meeting_name": str(meeting.get("name") or ""),
                        "name": str(contact.get("name") or ""),
                        "phone_number": [
                            str(phone.get("value") or "").strip()
                            for phone in (contact.get("phones") or [])
                            if str(phone.get("value") or "").strip()
                        ],
                        "address": list(contact.get("addresses") or []),
                        "relationships": list(contact.get("children") or []),
                        "emails": [],
                        "page_number": [],
                    }
                )
                if len(contacts) >= FIELD_LIST_PREVIEW_CONTACT_LIMIT:
                    break
            if len(contacts) >= FIELD_LIST_PREVIEW_CONTACT_LIMIT:
                break
        if len(contacts) >= FIELD_LIST_PREVIEW_CONTACT_LIMIT:
            break
    if not contacts:
        contacts.append(
            {
                "id": 0,
                "label": "Sample contact",
                "field_name": "AL Central",
                "meeting_name": "Birmingham (GRAHAM)",
                "name": "GRAHAM, Mark & Stephanie",
                "phone_number": ["205-337-3265", "111-222-3333 M"],
                "address": ["3028 Dolly Ridge Dr, Vestavia, AL 35243"],
                "relationships": ["Ruth, Harrison"],
                "emails": ["mark.graham@example.com"],
                "page_number": ["Page 1"],
            }
        )
    return {
        "contacts": contacts,
        "selected_contact_id": contacts[0]["id"],
    }


def get_field_list_builder_context(template_id: int | None = None) -> dict[str, Any]:
    base_settings = _default_address_book_settings() if template_id is None else _load_address_book_settings()
    selected_template = _default_field_list_template() if template_id is None else _load_template(template_id)
    settings = _settings_from_json(selected_template.get("settings_json"), base_settings) if selected_template.get("settings_json") else base_settings
    settings["page_width_in"] = PAGE_WIDTH_IN
    settings["page_height_in"] = PAGE_HEIGHT_IN
    # Always rebuild from the live DB so any contact/meeting edit shows up immediately.
    picker_payload = _field_list_picker_payload()
    picker_json = json.dumps(picker_payload)
    meeting_preview_json = json.dumps(_field_list_meeting_preview_payload())
    preview_contacts = _field_list_preview_contacts_payload(picker_payload)
    templates_list = list_field_list_templates()
    return {
        "page_title": "Field List",
        "address_book_settings": settings,
        "field_list_item_types": FIELD_LIST_ITEM_TYPES,
        "templates_list": templates_list,
        "field_list_has_user_templates": has_field_list_user_templates(),
        "selected_template": selected_template,
        "field_list_can_edit": can_edit_field_list(),
        "field_list_payload_json": json.dumps(selected_template["items"]),
        "field_list_item_types_json": json.dumps(FIELD_LIST_ITEM_TYPES),
        "field_list_preview_contacts": preview_contacts["contacts"],
        "field_list_preview_contacts_json": json.dumps(preview_contacts),
        "field_list_picker_json": picker_json,
        "field_list_meeting_preview_json": meeting_preview_json,
        "field_list_font_families": FONT_FAMILY_OPTIONS,
        "field_list_settings_json": json.dumps(
            {
                "pageWidthIn": PAGE_WIDTH_IN,
                "pageHeightIn": PAGE_HEIGHT_IN,
                "marginLeftIn": settings["margin_left_in"],
                "marginRightIn": settings["margin_right_in"],
                "marginTopIn": settings["margin_top_in"],
                "marginBottomIn": settings["margin_bottom_in"],
                "fontFamily": settings["font_family"],
                "baseFontSizePt": settings["base_font_size_pt"],
                "baseFontBold": bool(settings["base_font_bold"]),
                "baseFontItalic": bool(settings["base_font_italic"]),
                "baseFontUnderline": bool(settings["base_font_underline"]),
                "lineHeight": settings["line_height"],
                "previewScale": 1.0,
                "pageColumnCount": settings["page_column_count"],
                "includeBibleStudyUnionInfo": bool(settings["include_bible_study_union_info"]),
                "twoColumnGapCh": settings["two_column_gap_ch"],
                "columnWidthIn": settings["column_width_in"],
                "addressAlign": settings["address_align"],
                "titleFontFamily": settings["title_font_family"],
                "titleFontSizePt": settings["title_font_size_pt"],
                "titleFontBold": bool(settings["title_font_bold"]),
                "titleFontItalic": bool(settings["title_font_italic"]),
                "titleFontUnderline": bool(settings["title_font_underline"]),
                "titleAlign": settings["title_align"],
                "meetingNameFontFamily": settings["meeting_name_font_family"],
                "meetingNameFontSizePt": settings["meeting_name_font_size_pt"],
                "meetingNameFontBold": bool(settings["meeting_name_font_bold"]),
                "meetingNameFontItalic": bool(settings["meeting_name_font_italic"]),
                "meetingNameFontUnderline": bool(settings["meeting_name_font_underline"]),
                "meetingNameAlign": settings["meeting_name_align"],
                "bibleStudyFontSizePt": settings["bible_study_font_size_pt"],
                "bibleStudyUnionAlign": settings["bible_study_union_align"],
                "separatorLines": bool(settings["separator_lines"]),
                "separatorVerticalLines": bool(settings["separator_vertical_lines"]),
                "manualPaletteItems": bool(settings["manual_palette_items"]),
                "selectedFieldName": settings.get("selected_field_name", ""),
                "selectedMeetingName": settings.get("selected_meeting_name", ""),
                "selectedContactId": settings.get("selected_contact_id", 0),
            }
        ),
    }


def create_field_list_template(template_name: str = "", items_json: str = "", settings_values: dict[str, Any] | None = None) -> int:
    ensure_default_field_list_template()
    items = _parse_template_items_json(items_json)
    normalized_settings = _normalize_address_book_settings_values(settings_values or {}, _load_address_book_settings())
    with get_connection() as conn:
        next_number = int(
            conn.execute("SELECT COUNT(*) AS count FROM field_list_templates").fetchone()["count"] or 0
        ) + 1
        saved_template_name = _clean(template_name) or f"Field List {next_number}"
        created = conn.execute(
            """
            INSERT INTO field_list_templates (name, is_default, settings_json, created_at, updated_at)
            VALUES (?, 0, ?, ?, ?)
            """,
            (saved_template_name, _settings_json(normalized_settings), _now_text(), _now_text()),
        )
        template_id = int(created.lastrowid)
        for index, item in enumerate(items, start=1):
            normalized_item = _normalize_template_item(item, index)
            conn.execute(
                """
                INSERT INTO field_list_template_items (
                  template_id,
                  item_type,
                  label_number,
                  x_in,
                  y_in,
                  width_in,
                  height_in,
                  font_size_pt,
                  sort_order,
                  created_at,
                  updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    template_id,
                    normalized_item["item_type"],
                    normalized_item["label_number"],
                    normalized_item["x_in"],
                    normalized_item["y_in"],
                    normalized_item["width_in"],
                    normalized_item["height_in"],
                    normalized_item["font_size_pt"],
                    index,
                    _now_text(),
                    _now_text(),
                ),
            )
        conn.commit()
        return template_id


def save_address_book_settings(values: dict[str, Any]) -> dict[str, Any]:
    ensure_address_book_settings()
    normalized = _normalize_address_book_settings_values(values, _load_address_book_settings())
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE address_book_settings
            SET
              margin_left_in = ?,
              margin_right_in = ?,
              margin_top_in = ?,
              margin_bottom_in = ?,
              font_family = ?,
              base_font_size_pt = ?,
              base_font_bold = ?,
              base_font_italic = ?,
              base_font_underline = ?,
              line_height = ?,
              preview_scale = ?,
              page_column_count = ?,
              include_bible_study_union_info = ?,
              two_column_gap_ch = ?,
              column_width_in = ?,
              address_align = ?,
              address_map_provider = ?,
              address_italic = ?,
              title_font_family = ?,
              title_font_size_pt = ?,
              title_font_bold = ?,
              title_font_italic = ?,
              title_font_underline = ?,
              title_align = ?,
              meeting_name_font_family = ?,
              meeting_name_font_size_pt = ?,
              meeting_name_font_bold = ?,
              meeting_name_font_italic = ?,
              meeting_name_font_underline = ?,
              meeting_name_align = ?,
              bible_study_font_size_pt = ?,
              bible_study_union_align = ?,
              separator_lines = ?,
              separator_vertical_lines = ?,
              manual_palette_items = ?,
              print_order_mode = ?,
              print_order_json = ?,
              meeting_page_mode = ?,
              field_separator_pages = ?,
              double_column_index = ?,
              updated_at = ?
            WHERE id = 1
            """,
            (
                normalized["margin_left_in"],
                normalized["margin_right_in"],
                normalized["margin_top_in"],
                normalized["margin_bottom_in"],
                normalized["font_family"],
                normalized["base_font_size_pt"],
                normalized["base_font_bold"],
                normalized["base_font_italic"],
                normalized["base_font_underline"],
                normalized["line_height"],
                normalized["preview_scale"],
                normalized["page_column_count"],
                normalized["include_bible_study_union_info"],
                normalized["two_column_gap_ch"],
                normalized["column_width_in"],
                normalized["address_align"],
                normalized["address_map_provider"],
                normalized["address_italic"],
                normalized["title_font_family"],
                normalized["title_font_size_pt"],
                normalized["title_font_bold"],
                normalized["title_font_italic"],
                normalized["title_font_underline"],
                normalized["title_align"],
                normalized["meeting_name_font_family"],
                normalized["meeting_name_font_size_pt"],
                normalized["meeting_name_font_bold"],
                normalized["meeting_name_font_italic"],
                normalized["meeting_name_font_underline"],
                normalized["meeting_name_align"],
                normalized["bible_study_font_size_pt"],
                normalized["bible_study_union_align"],
                normalized["separator_lines"],
                normalized["separator_vertical_lines"],
                normalized["manual_palette_items"],
                normalized["print_order_mode"],
                normalized["print_order_json"],
                normalized["meeting_page_mode"],
                normalized["field_separator_pages"],
                normalized["double_column_index"],
                _now_text(),
            ),
        )
        conn.commit()
    return normalized


def _parse_template_items_json(raw_json: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(raw_json or "[]")
    except json.JSONDecodeError:
        payload = []
    if not isinstance(payload, list):
        payload = []
    return [
        _normalize_template_item(item, index + 1)
        for index, item in enumerate(payload)
        if isinstance(item, dict) and _is_supported_item_type(item)
    ]


def _load_saved_template_items(conn: Any, template_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM field_list_template_items
        WHERE template_id = ?
        ORDER BY sort_order ASC, id ASC
        """,
        (template_id,),
    ).fetchall()
    return [
        _normalize_template_item(dict(item), index + 1)
        for index, item in enumerate(rows)
        if _is_supported_item_type(dict(item))
    ]


def save_field_list_template(template_id: int | None, name: str, items_json: str, settings_values: dict[str, Any] | None = None) -> int:
    ensure_default_field_list_template()
    normalized_name = _clean(name) or DEFAULT_TEMPLATE_NAME
    items = _parse_template_items_json(items_json)
    normalized_settings = _normalize_address_book_settings_values(settings_values or {}, _load_address_book_settings())

    with get_connection() as conn:
        selected_id = int(template_id or 0)
        existing = None
        fallback_items: list[dict[str, Any]] = []
        if selected_id:
            existing = conn.execute("SELECT id, name FROM field_list_templates WHERE id = ?", (selected_id,)).fetchone()
            if existing:
                fallback_items = _load_saved_template_items(conn, selected_id)
            if existing and _clean(existing["name"]).lower() != normalized_name.lower():
                existing = None
                selected_id = 0
        if not items:
            items = fallback_items or [
                _normalize_template_item(item, index + 1)
                for index, item in enumerate(_default_template_items())
            ]
        if existing is None:
            created = conn.execute(
                """
                INSERT INTO field_list_templates (name, is_default, settings_json, created_at, updated_at)
                VALUES (?, 0, ?, ?, ?)
                """,
                (normalized_name, _settings_json(normalized_settings), _now_text(), _now_text()),
            )
            selected_id = int(created.lastrowid)
        else:
            conn.execute(
                """
                UPDATE field_list_templates
                SET name = ?, settings_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (normalized_name, _settings_json(normalized_settings), _now_text(), selected_id),
            )

        conn.execute("DELETE FROM field_list_template_items WHERE template_id = ?", (selected_id,))
        for index, item in enumerate(items, start=1):
            normalized_item = _normalize_template_item(item, index)
            conn.execute(
                """
                INSERT INTO field_list_template_items (
                  template_id,
                  item_type,
                  label_number,
                  x_in,
                  y_in,
                  width_in,
                  height_in,
                  font_size_pt,
                  sort_order,
                  created_at,
                  updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    selected_id,
                    normalized_item["item_type"],
                    normalized_item["label_number"],
                    normalized_item["x_in"],
                    normalized_item["y_in"],
                    normalized_item["width_in"],
                    normalized_item["height_in"],
                    normalized_item["font_size_pt"],
                    index,
                    _now_text(),
                    _now_text(),
                ),
            )

        default_count = int(
            conn.execute("SELECT COUNT(*) AS count FROM field_list_templates WHERE is_default = 1").fetchone()["count"] or 0
        )
        if default_count <= 0:
            conn.execute("UPDATE field_list_templates SET is_default = CASE WHEN id = ? THEN 1 ELSE 0 END", (selected_id,))
        conn.commit()
    return selected_id


def delete_field_list_template(template_id: int | None) -> int:
    ensure_default_field_list_template()
    selected_id = int(template_id or 0)
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, is_default
            FROM field_list_templates
            ORDER BY is_default DESC, LOWER(name) ASC, id ASC
            """
        ).fetchall()
        ids = [int(row["id"]) for row in rows]
        if selected_id not in ids or len(ids) <= 1:
            return ids[0] if ids else ensure_default_field_list_template()

        deleted_row = next(row for row in rows if int(row["id"]) == selected_id)
        if bool(deleted_row["is_default"]):
            return next((template_id_value for template_id_value in ids if template_id_value != selected_id), selected_id)
        remaining_custom_ids = [
            int(row["id"])
            for row in rows
            if int(row["id"]) != selected_id and not bool(row["is_default"])
        ]
        next_id = remaining_custom_ids[0] if remaining_custom_ids else 0
        conn.execute("DELETE FROM field_list_template_items WHERE template_id = ?", (selected_id,))
        conn.execute("DELETE FROM field_list_templates WHERE id = ?", (selected_id,))
        conn.commit()
        return next_id


def _field_list_meeting_sort_orders(field_name: str) -> dict[str, int]:
    normalized_field = _clean(field_name)
    if not normalized_field:
        return {}
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT meeting_name, sort_order
            FROM meeting_sections_v2
            WHERE field_name = ?
            ORDER BY sort_order ASC, id ASC
            """,
            (normalized_field,),
        ).fetchall()
    return {_norm_lookup(row["meeting_name"]): int(row["sort_order"] or 0) for row in rows}


def _meeting_scope_matches(section_value: str, selected_value: str) -> bool:
    section_key = _norm_lookup(section_value)
    selected_key = _norm_lookup(selected_value)
    if not selected_key:
        return True
    return section_key == selected_key or section_key.startswith(selected_key) or selected_key.startswith(section_key)


def _field_list_bible_font_sections(field_name: str, selected_meeting_names: list[str] | None = None) -> list[dict[str, Any]]:
    selected_field_key = _norm_lookup(field_name)
    selected_meeting_names = [_clean(name) for name in (selected_meeting_names or []) if _clean(name)]
    rows = fetch_all(
        """
        SELECT id, field_name, meeting_name
        FROM meeting_sections_v2
        ORDER BY lower(field_name), lower(meeting_name), id
        """
    )
    sections = []
    for row in rows:
        section = dict(row)
        field_key = _norm_lookup(section.get("field_name"))
        field_matches = (
            True
            if not selected_field_key
            else field_key == selected_field_key or field_key.startswith(selected_field_key) or selected_field_key.startswith(field_key)
        )
        if not field_matches:
            continue
        if selected_meeting_names and not any(_meeting_scope_matches(section.get("meeting_name") or "", name) for name in selected_meeting_names):
            continue
        sections.append(section)
    return sections


def _field_list_bible_font_row_text(conn, row_id: int, is_flow: bool) -> str:
    if is_flow:
        rows = conn.execute(
            """
            SELECT text_value
            FROM meeting_row_flow_items_v2
            WHERE row_id = ?
            ORDER BY column_index, item_order, id
            """,
            (row_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT text_value
            FROM meeting_row_cells_v2
            WHERE row_id = ?
            ORDER BY col_start, id
            """,
            (row_id,),
        ).fetchall()
    return " | ".join(_clean(row["text_value"]) for row in rows if _clean(row["text_value"]))


def inspect_field_list_bible_font_conflicts(
    field_name: str,
    selected_meeting_names: list[str] | None,
    font_size_pt: float,
) -> dict[str, Any]:
    selected_size = _coerce_float(font_size_pt, 11.0, 6.0, 15.0)
    sections = _field_list_bible_font_sections(field_name, selected_meeting_names)
    meeting_summaries = []
    conflict_rows = []
    meeting_differences = []
    has_differences = False
    with get_connection() as conn:
        for section in sections:
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id, row_order, row_kind, format_code
                    FROM meeting_section_rows_v2
                    WHERE section_id = ?
                    ORDER BY row_order, id
                    """,
                    (int(section["id"]),),
                ).fetchall()
            ]
            candidate_rows = []
            explicit_counts: dict[float, int] = {}
            for row in rows:
                tags = str(row.get("row_kind") or "content").lower().split("|")
                primary_kind = tags[0] if tags else "content"
                if primary_kind in {"blank", "divider"}:
                    continue
                text = _field_list_bible_font_row_text(conn, int(row["id"]), primary_kind == "column_flow")
                if not text:
                    continue
                explicit_size = _parse_font_size_value(row.get("format_code"))
                if explicit_size is not None:
                    explicit_counts[explicit_size] = explicit_counts.get(explicit_size, 0) + 1
                    if abs(explicit_size - selected_size) >= 0.01:
                        has_differences = True
                candidate_rows.append(
                    {
                        "id": int(row["id"]),
                        "row_order": int(row["row_order"] or 0),
                        "text": text,
                        "explicit_size": explicit_size,
                    }
                )
            majority_size = None
            if explicit_counts:
                majority_size = sorted(explicit_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
            if majority_size is not None and abs(majority_size - selected_size) >= 0.01:
                has_differences = True
                meeting_differences.append(
                    {
                        "section_id": int(section["id"]),
                        "field_name": section.get("field_name") or "",
                        "meeting_name": section.get("meeting_name") or "",
                        "majority_size": _format_font_size(majority_size),
                    }
                )
            section_conflicts = []
            if majority_size is not None:
                for row in candidate_rows:
                    explicit_size = row["explicit_size"]
                    if explicit_size is not None and abs(explicit_size - majority_size) >= 0.01:
                        conflict = {
                            "section_id": int(section["id"]),
                            "field_name": section.get("field_name") or "",
                            "meeting_name": section.get("meeting_name") or "",
                            "row_id": row["id"],
                            "row_order": row["row_order"],
                            "row_text": row["text"],
                            "current_size": _format_font_size(explicit_size),
                        }
                        section_conflicts.append(conflict)
                        conflict_rows.append(conflict)
            meeting_summaries.append(
                {
                    "section_id": int(section["id"]),
                    "field_name": section.get("field_name") or "",
                    "meeting_name": section.get("meeting_name") or "",
                    "row_count": len(candidate_rows),
                    "majority_size": _format_font_size(majority_size) if majority_size is not None else "",
                    "conflict_count": len(section_conflicts),
                }
            )
    return {
        "field_name": field_name,
        "selected_size": _format_font_size(selected_size),
        "has_differences": has_differences,
        "has_conflicts": bool(conflict_rows),
        "has_meeting_differences": bool(meeting_differences),
        "meeting_difference_count": len(meeting_differences),
        "meetings": meeting_summaries,
        "meeting_differences": meeting_differences,
        "conflicts": conflict_rows,
    }


def apply_field_list_bible_font_resolution(
    field_name: str,
    selected_meeting_names: list[str] | None,
    font_size_pt: float,
    mode: str,
    row_actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    selected_size = _coerce_float(font_size_pt, 11.0, 6.0, 15.0)
    selected_format = _format_font_size(selected_size)
    inspection = inspect_field_list_bible_font_conflicts(field_name, selected_meeting_names, selected_size)
    conflict_ids = {int(row["row_id"]) for row in inspection["conflicts"]}
    row_action_map = {int(action.get("row_id") or 0): action for action in (row_actions or []) if int(action.get("row_id") or 0)}
    sections = _field_list_bible_font_sections(field_name, selected_meeting_names)
    updated_count = 0
    with get_connection() as conn:
        for section in sections:
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id, row_kind
                    FROM meeting_section_rows_v2
                    WHERE section_id = ?
                    ORDER BY row_order, id
                    """,
                    (int(section["id"]),),
                ).fetchall()
            ]
            for row in rows:
                row_id = int(row["id"])
                tags = str(row.get("row_kind") or "content").lower().split("|")
                primary_kind = tags[0] if tags else "content"
                next_value = selected_format
                if mode == "except_conflicts" and row_id in conflict_ids:
                    continue
                if mode == "resolve" and row_id in conflict_ids:
                    action = row_action_map.get(row_id) or {}
                    choice = _clean(action.get("choice"))
                    if choice == "keep":
                        continue
                    if choice == "new":
                        next_value = _format_font_size(_coerce_float(action.get("new_size"), selected_size, 6.0, 15.0))
                conn.execute(
                    """
                    UPDATE meeting_section_rows_v2
                    SET format_code = ?
                    WHERE id = ?
                    """,
                    (next_value, row_id),
                )
                updated_count += 1
        conn.commit()
    return {"updated_count": updated_count, "selected_size": selected_format}


def save_field_list_bible_font_size_setting(
    template_id: int | None,
    font_size_pt: float,
    items_json: str = "",
    template_name: str = "",
) -> dict[str, Any]:
    selected_size = _coerce_float(font_size_pt, 11.0, 6.0, 15.0)
    current_settings = _load_address_book_settings()
    current_settings["bible_study_font_size_pt"] = selected_size
    saved_settings = save_address_book_settings(current_settings)
    if template_id:
        with get_connection() as conn:
            template = conn.execute(
                "SELECT id, name FROM field_list_templates WHERE id = ?",
                (int(template_id),),
            ).fetchone()
            if template:
                save_field_list_template(
                    int(template_id),
                    _clean(template_name) or _clean(template["name"]),
                    items_json,
                    saved_settings,
                )
    return saved_settings


def _print_escape(value: object) -> str:
    return escape(str(value or ""), quote=True)


def _print_align_class(value: object, default: str = "center") -> str:
    normalized = _clean(value).lower()
    if normalized == "auto":
        return "center"
    return normalized if normalized in {"left", "center", "right"} else default


def _meeting_preview_row_style(row: dict[str, Any]) -> str:
    declarations = []
    font_size = _parse_font_size_value(row.get("font_size_pt"))
    if font_size is None:
        font_size = _parse_font_size_value(row.get("effective_font_size_pt"))
    if font_size is not None:
        declarations.append(f"--meeting-row-font-size: {font_size:.2f}pt")
    try:
        line_height = float(row.get("line_height") or 0)
    except (TypeError, ValueError):
        line_height = 0.0
    if line_height > 0:
        declarations.append(f"line-height: {max(0.8, min(3.0, line_height)):.2f}")
    column_gap = _coerce_int(row.get("column_gap_px"), 6, 0, 48)
    declarations.append(f"--meeting-grid-column-gap: {column_gap}px")
    return "; ".join(declarations)


def _meeting_preview_html_for_print(value: object) -> str:
    return re.sub(r"\r?\n", "<br>", str(value or ""))


def _field_list_print_relationship_suffix_html(contact: dict[str, Any]) -> str:
    parts: list[str] = []
    for child in contact.get("children") or []:
        child_name = _clean(child)
        if child_name:
            parts.append(f'<span class="print-field-list-relationship-child">{_print_escape(child_name)}</span>')
    for item in contact.get("other_relationships") or []:
        label = _clean(item.get("relation_type"))
        name = _clean(item.get("relation_value"))
        if not name:
            continue
        if label:
            parts.append(
                f'<span class="print-field-list-relationship-pair">{_print_escape(label)}: {_print_escape(name)}</span>'
            )
        else:
            parts.append(_print_escape(name))
    return ", ".join(parts)


def _field_list_print_relationship_suffix_html_from_text(suffix: str) -> str:
    segments: list[str] = []
    text = _clean(suffix)
    while text:
        label_match = re.match(r"([^:,]+):\s*", text)
        if label_match:
            label = label_match.group(1).strip()
            rest = text[label_match.end():]
            next_labeled = re.search(r",\s*(?:[^:,]+):\s*", rest)
            if next_labeled:
                name = rest[:next_labeled.start()].strip()
                text = rest[next_labeled.start() + 1:].lstrip()
            else:
                name = rest.strip()
                text = ""
            segments.append(
                f'<span class="print-field-list-relationship-pair">{_print_escape(label)}: {_print_escape(name)}</span>'
            )
            continue
        next_labeled = re.search(r",\s*(?:[^:,]+):\s*", text)
        if next_labeled:
            child = text[:next_labeled.start()].strip().strip(",")
            if child:
                segments.append(f'<span class="print-field-list-relationship-child">{_print_escape(child)}</span>')
            text = text[next_labeled.start() + 1:].lstrip()
            continue
        if text:
            segments.append(f'<span class="print-field-list-relationship-child">{_print_escape(text.strip())}</span>')
        text = ""
    return ", ".join(segments)


def _field_list_print_contact_name_head_html(value: str) -> str:
    comma_index = value.find(",")
    if comma_index < 0:
        return _print_escape(value)
    last_name = value[:comma_index]
    rest = value[comma_index:]
    return f'<span class="print-field-list-contact-last-name">{_print_escape(last_name)}</span>{_print_escape(rest)}'


def _field_list_print_contact_name_html(contact: dict[str, Any], settings: dict[str, Any] | None = None) -> str:
    value = _clean(contact.get("name") or contact.get("label") or "CONTACT, Name")
    semicolon_index = value.find("; ")
    if semicolon_index >= 0:
        head = value[:semicolon_index]
        suffix_html = _field_list_print_relationship_suffix_html(contact)
        if not suffix_html:
            suffix_html = _field_list_print_relationship_suffix_html_from_text(value[semicolon_index + 2:])
        combined = f"{_field_list_print_contact_name_head_html(head)}; {suffix_html}"
    else:
        combined = _field_list_print_contact_name_head_html(value)
    if not settings:
        return combined
    base_size = float(settings.get("base_font_size_pt") or 10)
    column_width = float(settings.get("column_width_in") or 4.0)
    if int(settings.get("page_column_count") or 1) == 2:
        column_width = max(
            1.0,
            (PAGE_WIDTH_IN - float(settings.get("margin_left_in") or 0.35) - float(settings.get("margin_right_in") or 0.35)) / 2.0,
        )
    text_width_in = max(1.0, column_width - 1.18)
    approx_chars = max(12, int((text_width_in * 72.0 / base_size) * 1.62))

    def measure_width(text: str) -> float:
        weighted = sum(1.35 if char.isupper() else 0.72 if char in "ilI.,;:' " else 1.0 for char in text)
        return weighted

    lines = _wrap_contact_name_lines(contact, measure_width, approx_chars)
    if len(lines) <= 1:
        return combined
    html_lines = []
    for index, line in enumerate(lines):
        line_semicolon = line.find("; ")
        if line_semicolon >= 0:
            line_head = line[:line_semicolon]
            line_suffix = line[line_semicolon + 2:]
            line_html = f"{_field_list_print_contact_name_head_html(line_head)}; {_field_list_print_relationship_suffix_html_from_text(line_suffix)}"
        elif index > 0:
            line_suffix = line.lstrip(" ")
            if line.startswith("; "):
                line_html = f"; {_field_list_print_relationship_suffix_html_from_text(line[2:])}"
            elif line.startswith(" "):
                line_html = f"&nbsp;{_field_list_print_relationship_suffix_html_from_text(line_suffix)}"
            else:
                line_html = _field_list_print_relationship_suffix_html_from_text(line)
        else:
            line_html = _field_list_print_contact_name_head_html(line)
        indent_class = " is-indented" if index > 0 else ""
        indent_style = f' style="padding-left: {index}ch;"' if index > 0 else ""
        html_lines.append(f'<div class="print-field-list-contact-name-line{indent_class}"{indent_style}>{line_html}</div>')
    return "".join(html_lines)


def _field_list_print_compact_inline_fits(contact: dict[str, Any], address: str, settings: dict[str, Any]) -> bool:
    base_size = float(settings.get("base_font_size_pt") or 10)
    column_width = float(settings.get("column_width_in") or 4.0)
    if int(settings.get("page_column_count") or 1) == 2:
        column_width = max(1.0, (PAGE_WIDTH_IN - float(settings.get("margin_left_in") or 0.35) - float(settings.get("margin_right_in") or 0.35)) / 2.0)
    text_width_in = max(1.0, column_width - 1.18)
    approx_chars = max(12, int((text_width_in * 72.0 / base_size) * 1.62))
    value = f"{_clean(contact.get('name') or contact.get('label') or 'CONTACT, Name')}, {_clean(address)}"
    weighted = sum(1.35 if char.isupper() else 0.72 if char in "ilI.,;:' " else 1.0 for char in value)
    return weighted <= approx_chars


def _field_list_print_phone_line_html(phone: dict[str, Any], index: int) -> str:
    value = _clean(phone.get("value"))
    if not value:
        return ""
    code = _clean(phone.get("code"))
    row_class = "is-primary-phone" if index == 0 else "is-secondary-phone"
    return (
        f'<div class="print-field-list-phone-line {row_class}">'
        f'<span class="print-field-list-phone-value">{_print_escape(value)}</span>'
        f'<span class="print-field-list-phone-code">{_print_escape(code)}</span>'
        "</div>"
    )


def _field_list_print_book_note_values(contact: dict[str, Any]) -> list[str]:
    return [
        item
        for item in (contact.get("print_book_name") or [])
        if print_book_note_display(item).strip()
    ]


def _field_list_print_after_address_values(contact: dict[str, Any]) -> list[str]:
    return [
        item
        for item in (contact.get("print_after_address") or [])
        if print_book_note_display(item).strip()
    ]


def _field_list_print_book_note_text(contact: dict[str, Any]) -> str:
    values = _field_list_print_book_note_values(contact)
    return " ".join(f"({print_book_note_display(value)})" for value in values)


def _field_list_print_book_note_fits_on_name(contact: dict[str, Any], settings: dict[str, Any], pb_text: str) -> bool:
    if not pb_text:
        return True
    base_size = float(settings.get("base_font_size_pt") or 10)
    column_width = float(settings.get("column_width_in") or 4.0)
    if int(settings.get("page_column_count") or 1) == 2:
        column_width = max(
            1.0,
            (PAGE_WIDTH_IN - float(settings.get("margin_left_in") or 0.35) - float(settings.get("margin_right_in") or 0.35)) / 2.0,
        )
    text_width_in = max(1.0, column_width - 1.18)
    approx_chars = max(12, int((text_width_in * 72.0 / base_size) * 1.62))
    name_value = _clean(contact.get("name") or contact.get("label") or "CONTACT, Name")

    def measure_width(text: str) -> float:
        return sum(1.35 if char.isupper() else 0.72 if char in "ilI.,;:' " else 1.0 for char in text)

    first_line = (_wrap_contact_name_lines(contact, measure_width, approx_chars) or [name_value])[0]
    return measure_width(f"{first_line} {pb_text}") <= approx_chars


def _field_list_print_note_lines_html(contact: dict[str, Any], settings: dict[str, Any]) -> tuple[str, str, str]:
    """Return (pb_inline_html, pb_own_line_html, pa_lines_html)."""
    pb_values = _field_list_print_book_note_values(contact)
    pb_text = _field_list_print_book_note_text(contact)
    pb_html = " ".join(f"({_print_note_html(value)})" for value in pb_values)
    pb_inline_html = ""
    pb_own_line_html = ""
    if pb_text:
        if _field_list_print_book_note_fits_on_name(contact, settings, pb_text):
            pb_inline_html = f'<span class="print-field-list-print-book-note">&nbsp;{pb_html}</span>'
        else:
            pb_own_line_html = (
                f'<div class="print-field-list-print-book-note print-field-list-print-note-line">'
                f"{pb_html}</div>"
            )
    pa_lines_html = "".join(
        f'<div class="print-field-list-print-after-note print-field-list-print-note-line">'
        f"{_print_note_html(value)}</div>"
        for value in _field_list_print_after_address_values(contact)
    )
    return pb_inline_html, pb_own_line_html, pa_lines_html


def _field_list_print_contact_html(contact: dict[str, Any], settings: dict[str, Any], compact_address: bool = False) -> str:
    raw_phones = [phone for phone in list(contact.get("phones") or []) if _clean(phone.get("value"))]
    phone_lines = []
    if compact_address and len(raw_phones) >= 3:
        phone_lines.append(_field_list_print_phone_line_html(raw_phones[0], 0))
        combined_phone_html = []
        for index, phone in enumerate(raw_phones[1:]):
            if index == 1:
                combined_phone_html.append('<span class="print-field-list-phone-continuation-gap"></span>')
            elif index > 1:
                combined_phone_html.append('<span class="print-field-list-phone-comma">,</span>')
            combined_phone_html.append(
                '<span class="print-field-list-phone-pair">'
                f'<span class="print-field-list-phone-value">{_print_escape(_clean(phone.get("value")))}</span>'
                f'<span class="print-field-list-phone-code">{_print_escape(_clean(phone.get("code")))}</span>'
                "</span>"
            )
        phone_lines.append(
            '<div class="print-field-list-phone-line is-secondary-phone is-combined-phone-line">'
            + "".join(combined_phone_html)
            + "</div>"
        )
    else:
        for index, phone in enumerate(raw_phones):
            phone_lines.append(_field_list_print_phone_line_html(phone, index))
    phone_lines = [line for line in phone_lines if line]
    from app.services.address_book_pdf_service import _text_width as _pdf_text_width

    address_font_size = float(settings.get("base_font_size_pt") or 10.0)
    address_font_family = str(settings.get("font_family") or "Arial")
    address_lines = _field_list_address_display_lines(
        contact.get("address_entries") or contact.get("addresses", []),
        max_width=_field_list_address_column_width_pt(settings),
        text_width=lambda value, size=address_font_size, family=address_font_family: _pdf_text_width(
            value, size, family
        ),
    )
    compact_address_lines = address_lines
    address_align = "right" if settings.get("address_align") != "left" else "left"
    compact_class = " is-compact-address" if compact_address else ""
    pb_inline_html, pb_own_line_html, pa_lines_html = _field_list_print_note_lines_html(contact, settings)
    if compact_address:
        address_html = ""
        phone_address_html = ""
        if compact_address_lines:
            many_phones = len(raw_phones) >= 3
            first_address_fits = _field_list_print_compact_inline_fits(contact, compact_address_lines[0], settings)
            additional_address_html = "".join(
                f'<span class="print-field-list-contact-extra-address">{_print_escape(line)}</span>'
                for line in compact_address_lines[1:]
            )
            if many_phones and not first_address_fits:
                phone_address_html = (
                    '<div class="print-field-list-phone-address-continuation">'
                    + "".join(
                        f'<span class="print-field-list-contact-extra-address">{_print_escape(line)}</span>'
                        for line in compact_address_lines
                    )
                    + "</div>"
                )
            else:
                if many_phones and len(compact_address_lines) > 1:
                    phone_address_html = f'<div class="print-field-list-phone-address-continuation">{additional_address_html}</div>'
                    additional_address_html = ""
                address_html = (
                    '<span class="print-field-list-contact-inline-address-group">'
                    '<span class="print-field-list-contact-inline-separator">,</span>'
                    f'<span class="print-field-list-contact-inline-address">{_print_escape(compact_address_lines[0])}</span>'
                    '</span>'
                    + additional_address_html
                )
        return (
            f'<div class="print-field-list-contact-row{compact_class}">'
            f'<div class="print-field-list-phone-stack">{"".join(phone_lines)}{phone_address_html}</div>'
            '<div class="print-field-list-contact-main">'
            f'<div class="print-field-list-contact-name">{_field_list_print_contact_name_html(contact, settings)}{pb_inline_html}{address_html}</div>'
            f"{pb_own_line_html}{pa_lines_html}"
            "</div></div>"
        )
    address_block = ""
    if address_lines or pa_lines_html:
        address_block = (
            f'<div class="print-field-list-contact-address align-{address_align}">'
            + "".join(f"<div>{_print_escape(line)}</div>" for line in address_lines)
            + pa_lines_html
            + "</div>"
        )
    return (
        f'<div class="print-field-list-contact-row{compact_class}">'
        f'<div class="print-field-list-phone-stack">{"".join(phone_lines)}</div>'
        '<div class="print-field-list-contact-main">'
        f'<div class="print-field-list-contact-name">{_field_list_print_contact_name_html(contact, settings)}{pb_inline_html}</div>'
        f"{pb_own_line_html}"
        f"{address_block}"
        "</div></div>"
    )


def _field_list_print_contact_sort_key(contact: dict[str, Any]) -> tuple[str, int]:
    return (_clean(contact.get("sort_label") or contact.get("name") or contact.get("label")).lower(), int(contact.get("id") or 0))


def _field_list_sorted_meeting_contacts(meeting: dict[str, Any], meeting_home_first: bool) -> list[dict[str, Any]]:
    contacts = list(meeting.get("contacts") or [])
    if meeting_home_first:
        has_meeting_home = any(_clean(item.get("mtg_home_elder_flag")).startswith("1") for item in contacts)
        return sorted(contacts, key=lambda item: _meeting_role_sort_key(item, has_meeting_home))
    return sorted(contacts, key=_field_list_print_contact_sort_key)


def _field_list_print_contacts_columns_html(
    contacts: list[dict[str, Any]],
    settings: dict[str, Any],
    appended_html: str = "",
    appended_line_count: int = 0,
    force_one_column: bool = False,
    duplicate_columns: bool = False,
    compact_address: bool = False,
) -> str:
    column_count = 2 if duplicate_columns else 1 if force_one_column else 2 if int(settings.get("page_column_count") or 1) == 2 else 1
    midpoint = len(contacts)
    if duplicate_columns:
        columns = [contacts, contacts]
    elif column_count == 2 and len(contacts) > 1:
        midpoint = _field_list_print_balanced_split_index(contacts, appended_line_count)
        columns = [contacts[:midpoint], contacts[midpoint:]]
    else:
        columns = [contacts]
    column_html = []
    for index, column in enumerate(columns):
        appended = appended_html if appended_html and (column_count == 1 or index == 1 or duplicate_columns) else ""
        column_html.append(
            '<div class="print-field-list-meeting-contact-column">'
            + "".join(_field_list_print_contact_html(contact, settings, compact_address=compact_address) for contact in column)
            + appended
            + "</div>"
        )
    return (
        f'<div class="print-field-list-meeting-contacts print-field-list-meeting-contacts-{column_count}">'
        + "".join(column_html)
        + "</div>"
    )


def _meeting_preview_rows_for_print(field_name: str, meeting_name: str, preview_payload: dict[str, Any]) -> list[dict[str, Any]]:
    selected_field_key = _norm_lookup(field_name)
    selected_meeting_key = _norm_lookup(meeting_name)
    best_score = -1
    best_rows: list[dict[str, Any]] = []
    for section in preview_payload.get("sections") or []:
        meeting_key = _norm_lookup(section.get("meeting") or section.get("meeting_key"))
        field_key = _norm_lookup(section.get("field") or section.get("field_key"))
        if not selected_meeting_key or not meeting_key:
            continue
        meeting_matches = (
            meeting_key == selected_meeting_key
            or meeting_key.startswith(selected_meeting_key)
            or selected_meeting_key.startswith(meeting_key)
        )
        if not meeting_matches:
            continue
        field_matches = (
            field_key == selected_field_key
            or (selected_field_key and selected_field_key in field_key)
            or (field_key and field_key in selected_field_key)
        )
        score = 2 if field_matches else 1
        if score > best_score:
            best_score = score
            best_rows = list(section.get("rows") or [])
    return best_rows


def _flow_print_grid_column_html(column: dict[str, Any]) -> str:
    col_start = _coerce_int(column.get("col_start"), 1, 1, 28)
    col_span = _coerce_int(column.get("col_span"), 1, 1, 28)
    row_start = _coerce_int(column.get("grid_row_start"), 1, 1, 999)
    row_span = _coerce_int(column.get("grid_row_span"), 1, 1, 999)
    segment_html = []
    for index, segment in enumerate(_flow_column_segments(column)):
        if not str(segment.get("html") or "").strip():
            continue
        align_class = _print_align_class(segment.get("align"), "left")
        stacked_class = " is-stacked-segment" if index > 0 else ""
        segment_html.append(
            f'<div class="print-field-list-meeting-preview-flow-text align-{align_class}{stacked_class}">'
            f'{_meeting_preview_html_for_print(segment.get("html"))}</div>'
        )
    if not segment_html:
        return ""
    return (
        f'<div class="print-field-list-meeting-preview-cell print-field-list-meeting-preview-flow-column" '
        f'style="grid-column: {col_start} / span {col_span}; grid-row: {row_start} / span {row_span};">'
        + "".join(segment_html)
        + "</div>"
    )


def _flow_print_grid_html(row: dict[str, Any]) -> str:
    row_count = _flow_row_print_line_count(row)
    column_html = [
        html
        for column in [*(row.get("columns") or []), *(row.get("stacked_columns") or [])]
        if (html := _flow_print_grid_column_html(column))
    ]
    return (
        f'<div class="print-field-list-meeting-preview-flow-grid" '
        f'style="grid-template-rows: repeat({row_count}, auto);">'
        + "".join(column_html)
        + "</div>"
    )


def _flow_print_band_html(columns: list[dict[str, Any]], *, band_class: str = "") -> str:
    column_html = []
    for column in columns or []:
        col_start = _coerce_int(column.get("col_start"), 1, 1, 28)
        col_span = _coerce_int(column.get("col_span"), 1, 1, 28)
        segment_html = []
        for index, segment in enumerate(_flow_column_segments(column)):
            if not str(segment.get("html") or "").strip():
                continue
            align_class = _print_align_class(segment.get("align"), "left")
            stacked_class = " is-stacked-segment" if index > 0 else ""
            segment_html.append(
                f'<div class="print-field-list-meeting-preview-flow-text align-{align_class}{stacked_class}">'
                f'{_meeting_preview_html_for_print(segment.get("html"))}</div>'
            )
        column_html.append(
            f'<div class="print-field-list-meeting-preview-cell print-field-list-meeting-preview-flow-column" '
            f'style="grid-column: {col_start} / span {col_span};">'
            + "".join(segment_html)
            + "</div>"
        )
    band_classes = "print-field-list-meeting-preview-flow-band"
    if band_class:
        band_classes += f" {band_class}"
    return f'<div class="{band_classes}">' + "".join(column_html) + "</div>"


def _meeting_preview_row_print_html(row: dict[str, Any]) -> str:
    kind = _clean(row.get("kind"))
    row_style = _meeting_preview_row_style(row)
    style_attr = f' style="{row_style}"' if row_style else ""
    row_classes = " is-underlined-row" if row.get("is_underlined_row") else ""
    if kind == "blank":
        return f'<div class="print-field-list-meeting-preview-blank{row_classes}"{style_attr}></div>'
    if kind == "divider":
        return f'<div class="print-field-list-meeting-preview-divider{row_classes}"{style_attr}></div>'
    if kind == "flow":
        return (
            f'<div class="print-field-list-meeting-preview-flow{row_classes}"{style_attr}>'
            + _flow_print_grid_html(row)
            + "</div>"
        )
    cells = row.get("cells") or []
    cell_html = []
    for cell in cells:
        col_start = _coerce_int(cell.get("col_start"), 1, 1, 28)
        col_span = _coerce_int(cell.get("col_span"), 28, 1, 28)
        classes = [
            "print-field-list-meeting-preview-cell",
            f'align-{_print_align_class(cell.get("align"), "left")}',
            "is-bold" if cell.get("is_bold") else "",
            "is-italic" if cell.get("is_italic") else "",
            "is-title" if cell.get("is_title") else "",
            "is-underlined" if cell.get("is_underlined") else "",
        ]
        cell_html.append(
            f'<div class="{" ".join(item for item in classes if item)}" style="grid-column: {col_start} / span {col_span};">{_meeting_preview_html_for_print(cell.get("html"))}</div>'
        )
    return f'<div class="print-field-list-meeting-preview-row{row_classes}"{style_attr}>{"".join(cell_html)}</div>'


def _field_list_print_bible_html(
    field_name: str,
    meeting_name: str,
    settings: dict[str, Any],
    preview_payload: dict[str, Any],
    force_render: bool = False,
) -> str:
    if not force_render and not settings.get("include_bible_study_union_info"):
        return ""
    rows = _meeting_preview_rows_for_print(field_name, meeting_name, preview_payload)
    if not rows:
        return ""
    align = _print_align_class(settings.get("bible_study_union_align"), "center")
    row_html_parts: list[str] = []
    current_group = ""
    grouped_rows: list[dict[str, Any]] = []

    def flush_group() -> None:
        nonlocal current_group, grouped_rows
        if not grouped_rows:
            return
        row_html_parts.append(
            '<div class="print-field-list-keep-together-block">'
            + "".join(_meeting_preview_row_print_html(row) for row in grouped_rows)
            + "</div>"
        )
        current_group = ""
        grouped_rows = []

    for row in rows:
        group = _clean(row.get("keep_together_group"))
        if not group:
            flush_group()
            row_html_parts.append(_meeting_preview_row_print_html(row))
            continue
        if current_group and group != current_group:
            flush_group()
        current_group = group
        grouped_rows.append(row)
    flush_group()
    return (
        f'<div class="print-field-list-bible-cluster print-field-list-keep-together-block align-{align}">'
        + "".join(row_html_parts)
        + "</div>"
    )


def _field_list_print_contact_line_count(contact: dict[str, Any]) -> int:
    phone_count = max(1, len(list(contact.get("phones") or [])))
    address_lines = _field_list_address_display_lines(contact.get("address_entries") or contact.get("addresses") or [])
    name_lines = _field_list_estimated_name_line_count(contact)
    text_count = name_lines + len(address_lines)
    return max(phone_count, text_count)


def _field_list_estimated_name_line_count(contact: dict[str, Any]) -> int:
    value = _clean(contact.get("name") or contact.get("label"))
    return 2 if len(value) > 38 else 1


def _field_list_print_html_line_count(value: object) -> int:
    parts = re.split(r"<br\s*/?>|\r?\n", str(value or ""), flags=re.IGNORECASE)
    return max(1, len(parts))


def _field_list_print_meeting_preview_row_line_count(row: dict[str, Any]) -> int:
    kind = _clean(row.get("kind"))
    if kind in {"blank", "divider"}:
        return 1
    if kind == "flow":
        return _flow_row_print_line_count(row)
    cells = row.get("cells") or []
    return max([1, *[_field_list_print_html_line_count(cell.get("html")) for cell in cells]])


def _field_list_print_bible_line_count(field_name: str, meeting_name: str, preview_payload: dict[str, Any]) -> int:
    rows = _meeting_preview_rows_for_print(field_name, meeting_name, preview_payload)
    return sum(_field_list_print_meeting_preview_row_line_count(row) for row in rows)


def _field_list_print_balanced_split_index(contacts: list[dict[str, Any]], appended_line_count: int) -> int:
    if len(contacts) <= 1:
        return len(contacts)
    line_counts = [_field_list_print_contact_line_count(contact) for contact in contacts]
    total_lines = sum(line_counts)
    best_index = (len(contacts) + 1) // 2
    best_delta = float("inf")
    best_tallest_column = float("inf")
    last_index = len(contacts) if appended_line_count else len(contacts) - 1

    for index in range(1, last_index + 1):
        left_lines = sum(line_counts[:index])
        right_lines = total_lines - left_lines + appended_line_count
        delta = abs(left_lines - right_lines)
        tallest_column = max(left_lines, right_lines)
        if (
            delta < best_delta
            or (delta == best_delta and tallest_column < best_tallest_column)
            or (appended_line_count and delta == best_delta and tallest_column == best_tallest_column and index > best_index)
        ):
            best_delta = delta
            best_tallest_column = tallest_column
            best_index = index
    return best_index


def _field_list_print_meeting_contacts_html(
    field_name: str,
    meeting: dict[str, Any],
    settings: dict[str, Any],
    preview_payload: dict[str, Any],
    append_bible: bool,
) -> str:
    contacts = _field_list_sorted_meeting_contacts(meeting, bool(settings.get("print_meeting_home_first")))
    bible_html = _field_list_print_bible_html(field_name, meeting.get("name") or "", settings, preview_payload) if append_bible else ""
    bible_line_count = _field_list_print_bible_line_count(field_name, meeting.get("name") or "", preview_payload) + 1 if bible_html else 0
    return _field_list_print_contacts_columns_html(
        contacts,
        settings,
        bible_html,
        bible_line_count,
        force_one_column=bool(settings.get("manual_palette_items")),
        duplicate_columns=bool(settings.get("manual_palette_items")),
    )


def _field_list_print_item_html(
    item: dict[str, Any],
    field_name: str,
    meeting: dict[str, Any],
    settings: dict[str, Any],
    preview_payload: dict[str, Any],
    has_standalone_bible_item: bool,
    show_bible_separator: bool = False,
) -> str:
    item_type = _clean(item.get("item_type"))
    if item_type == "field_name":
        return ""
    if item_type == "meeting_name":
        align = _print_align_class(settings.get("meeting_name_align"), "center")
        now = datetime.now()
        meeting_name = _clean(meeting.get("name"))
        meeting_title = (
            f"{meeting_name} - {now.month}/{now.day}/{now.year}"
            if settings.get("manual_palette_items")
            else meeting_name
        )
        if settings.get("manual_palette_items"):
            escaped_title = _print_escape(meeting_title)
            return (
                f'<div class="print-field-list-duplicate-meeting-name">'
                f'<div class="align-{align}">{escaped_title}</div>'
                f'<div class="align-{align}">{escaped_title}</div>'
                f"</div>"
            )
        return f'<div class="print-field-list-meeting-name align-{align}">{_print_escape(meeting_title)}</div>'
    if item_type == "meetings":
        return _field_list_print_meeting_contacts_html(field_name, meeting, settings, preview_payload, not has_standalone_bible_item)
    if item_type == "bible_study_union":
        bible_html = _field_list_print_bible_html(field_name, meeting.get("name") or "", settings, preview_payload, force_render=True)
        if not bible_html:
            return ""
        separator_html = '<div class="print-field-list-inner-separator"></div>' if show_bible_separator and settings.get("separator_lines") else ""
        return bible_html + separator_html
    if item_type == "page_number":
        return ""
    return ""


def _field_list_print_meeting_block_html(
    field_name: str,
    meeting: dict[str, Any],
    items: list[dict[str, Any]],
    settings: dict[str, Any],
    preview_payload: dict[str, Any],
    show_leading_separator: bool = False,
) -> str:
    ordered_items = sorted(
        [item for item in items if _clean(item.get("item_type")) not in {"field_name", "page_number"}],
        key=lambda item: (float(item.get("y_in") or 0), float(item.get("x_in") or 0), int(item.get("sort_order") or 0)),
    )
    has_standalone_bible_item = any(_clean(item.get("item_type")) == "bible_study_union" for item in ordered_items)
    inner = []
    previous_bottom = None
    for item_index, item in enumerate(ordered_items):
        item_type = _clean(item.get("item_type"))
        has_following_content = any(
            _clean(next_item.get("item_type")) in {"meeting_name", "meetings", "bible_study_union"}
            for next_item in ordered_items[item_index + 1 :]
        )
        html = _field_list_print_item_html(
            item,
            field_name,
            meeting,
            settings,
            preview_payload,
            has_standalone_bible_item,
            show_bible_separator=item_type == "bible_study_union" and has_following_content,
        )
        if not html:
            continue
        y_in = float(item.get("y_in") or 0)
        height_in = float(item.get("height_in") or 0)
        gap_in = 0.0 if previous_bottom is None else min(max(0.0, y_in - previous_bottom), 0.35)
        previous_bottom = max(y_in + height_in, y_in)
        if item_type == "meeting_name":
            item_font_size = float(settings.get("meeting_name_font_size_pt") or item.get("font_size_pt") or 12)
        elif item_type == "bible_study_union":
            item_font_size = float(settings.get("bible_study_font_size_pt") or item.get("font_size_pt") or 11)
        else:
            item_font_size = float(settings.get("base_font_size_pt") or item.get("font_size_pt") or 11)
        style = (
            f"--item-width: {float(item.get('width_in') or 1):.3f}in; "
            f"--item-x: {float(item.get('x_in') or 0):.3f}in; "
            f"--item-font-size: {item_font_size:.2f}pt; "
            f"--item-gap: {gap_in:.3f}in;"
        )
        inner.append(f'<div class="print-field-list-template-item item-{item_type}" style="{style}">{html}</div>')
    leading_separator = '<div class="print-field-list-meeting-separator"></div>' if show_leading_separator and settings.get("separator_lines") else ""
    return f'<section class="print-field-list-meeting-block">{leading_separator}{"".join(inner)}</section>'


def _field_list_flat_alphabetical_contacts(meetings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contacts_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for meeting in meetings:
        for contact in meeting.get("contacts") or []:
            key = _field_list_household_key(contact)
            contacts_by_key[key] = contact
    return sorted(contacts_by_key.values(), key=_field_list_print_contact_sort_key)


def _field_list_print_flat_alphabetical_html(field_name: str, meetings: list[dict[str, Any]], settings: dict[str, Any], compact_address: bool = False) -> str:
    contacts = _field_list_flat_alphabetical_contacts(meetings)
    if not contacts:
        return ""
    return (
        '<section class="print-field-list-flat-block">'
        + _field_list_print_contacts_columns_html(contacts, settings, compact_address=compact_address)
        + "</section>"
    )


def _field_list_flat_page_chunks(contacts: list[dict[str, Any]], settings: dict[str, Any]) -> list[list[dict[str, Any]]]:
    margin_top = float(settings.get("margin_top_in") or 0.35)
    margin_bottom = float(settings.get("margin_bottom_in") or 0.35)
    base_font_size = float(settings.get("base_font_size_pt") or 10)
    line_height = float(settings.get("line_height") or 1.2)
    line_height_in = max(0.07, (base_font_size / 72.0) * line_height * 0.84)
    usable_height_in = max(1.0, PAGE_HEIGHT_IN - margin_top - margin_bottom - 0.18 - 0.22)
    lines_per_column = max(8, int(usable_height_in / line_height_in))
    column_count = 2 if int(settings.get("page_column_count") or 1) == 2 else 1
    max_lines = lines_per_column * column_count
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_lines = 0

    for contact in contacts:
        contact_lines = max(1, _field_list_print_contact_line_count(contact))
        if current and current_lines + contact_lines > max_lines:
            chunks.append(current)
            current = []
            current_lines = 0
        current.append(contact)
        current_lines += contact_lines
    if current:
        chunks.append(current)
    return chunks


def _field_list_print_flat_alphabetical_pages_html(title: str, meetings: list[dict[str, Any]], settings: dict[str, Any], compact_address: bool = False) -> str:
    contacts = _field_list_flat_alphabetical_contacts(meetings)
    if not contacts:
        return ""
    column_count = 2 if int(settings.get("page_column_count") or 1) == 2 else 1
    return (
        f'<section class="print-field-list-measured-source" data-field-list-measured-source data-title="{_print_escape(title)}" data-column-count="{column_count}">'
        + "".join(
            '<div class="print-field-list-measured-contact" data-measured-contact>'
            + _field_list_print_contact_html(contact, settings, compact_address=compact_address)
            + "</div>"
            for contact in contacts
        )
        + "</section>"
    )


def _field_list_canonical_one_column_items(settings: dict[str, Any]) -> list[dict[str, Any]]:
    column_width = _coerce_float(settings.get("column_width_in"), 4.0, 1.0, PAGE_WIDTH_IN)
    return [
        {
            "item_type": "meeting_name",
            "label_number": 2,
            "x_in": 0.0,
            "y_in": 0.0,
            "width_in": column_width,
            "height_in": 0.28,
            "font_size_pt": float(settings.get("meeting_name_font_size_pt") or 12),
            "sort_order": 1,
        },
        {
            "item_type": "meetings",
            "label_number": 4,
            "x_in": 0.0,
            "y_in": 0.50,
            "width_in": column_width,
            "height_in": 2.6,
            "font_size_pt": float(settings.get("base_font_size_pt") or 11),
            "sort_order": 2,
        },
    ]


def render_field_list_print_html(
    template_id: int | None,
    items_json: str,
    field_name: str,
    order_mode: str,
    settings_values: dict[str, Any] | None = None,
    print_mode: str = "template",
    selected_meeting_names: list[str] | None = None,
    meeting_home_first: bool = False,
    page_numbers: bool = False,
) -> str:
    selected_template = _load_template(template_id)
    base_settings = _settings_from_json(selected_template.get("settings_json"), _load_address_book_settings()) if selected_template.get("settings_json") else _load_address_book_settings()
    settings = _normalize_address_book_settings_values(settings_values or {}, base_settings)
    items = _parse_template_items_json(items_json) or selected_template.get("items") or []
    picker_payload = _field_list_picker_payload()
    normalized_print_mode = _clean(print_mode).lower()
    is_flat_alphabetical = normalized_print_mode in {"alphabetical_no_bible", "compact_no_bible"}
    preview_payload = {"sections": []} if is_flat_alphabetical else _field_list_meeting_preview_payload()
    normalized_field_name = _clean(field_name)
    selected_field = next((field for field in picker_payload.get("fields", []) if field.get("name") == normalized_field_name), None)
    if not selected_field:
        return _field_list_print_message_html("Field List", "Choose a field before printing.")

    selected_meeting_keys = {_norm_lookup(name) for name in (selected_meeting_names or []) if _clean(name)}
    meetings = list(selected_field.get("meetings") or [])
    if selected_meeting_keys:
        meetings = [meeting for meeting in meetings if _norm_lookup(meeting.get("name")) in selected_meeting_keys]
    if _clean(order_mode).lower() == "alphabetical":
        meetings.sort(key=lambda item: _clean(item.get("name")).lower())
    else:
        sort_orders = _field_list_meeting_sort_orders(normalized_field_name)
        meetings.sort(key=lambda item: (sort_orders.get(_norm_lookup(item.get("name")), 10_000_000), _clean(item.get("name")).lower()))

    settings["print_meeting_home_first"] = bool(meeting_home_first)
    if int(settings.get("page_column_count") or 1) == 1:
        items = _field_list_canonical_one_column_items(settings)
    printable_meetings = [meeting for meeting in meetings if meeting.get("contacts")]
    is_compact_list_print = normalized_print_mode == "compact_no_bible"
    is_duplicate_meeting_print = bool(settings.get("manual_palette_items"))
    now = datetime.now()
    today = f"{now.month}/{now.day}/{now.year}"
    title = f"{normalized_field_name} Field List - {today}"
    uses_template_pages = not is_flat_alphabetical
    uses_manual_pages = uses_template_pages or (is_flat_alphabetical and bool(page_numbers))
    if is_flat_alphabetical and page_numbers:
        meeting_blocks = [_field_list_print_flat_alphabetical_pages_html(title, printable_meetings, settings, compact_address=is_compact_list_print)]
    elif is_flat_alphabetical:
        meeting_blocks = [_field_list_print_flat_alphabetical_html(normalized_field_name, printable_meetings, settings, compact_address=is_compact_list_print)]
    else:
        meeting_blocks = [
            _field_list_print_meeting_block_html(
                normalized_field_name,
                meeting,
                items,
                settings,
                preview_payload,
                show_leading_separator=False,
            )
            for index, meeting in enumerate(printable_meetings)
        ]
    meeting_blocks = [block for block in meeting_blocks if block]
    if not meeting_blocks:
        return _field_list_print_message_html("Field List", "No meetings found for this field.")

    separator_class = "" if settings.get("separator_lines") else " no-separator-lines"
    vertical_separator_class = "" if settings.get("separator_vertical_lines") else " no-vertical-separator-lines"
    meeting_blocks_html = "".join(meeting_blocks)
    settings["_manual_pages"] = uses_manual_pages
    page_top_content_px = float(settings.get("margin_top_in") or 0.35) * 96
    css = _field_list_print_css(settings)
    manual_page_class = " manual-pages" if uses_manual_pages else ""
    page_number_class = " show-page-numbers" if page_numbers else ""
    duplicate_meeting_class = " duplicate-meeting-print" if is_duplicate_meeting_print else ""
    one_column_class = " one-column-print" if int(settings.get("page_column_count") or 1) == 1 else ""
    compact_list_class = " compact-list-print" if is_compact_list_print else ""
    header_html = "" if uses_manual_pages or is_duplicate_meeting_print else f"""<header class="print-field-list-header">
    <h1>{_print_escape(title)}</h1>
  </header>"""
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{_print_escape(title)}</title>
  <style>{css}</style>
</head>
<body class="print-field-list{separator_class}{vertical_separator_class}{manual_page_class}{page_number_class}{duplicate_meeting_class}{one_column_class}{compact_list_class}">
  {header_html}
  <main>
    {meeting_blocks_html}
  </main>
  <script>
    const paginateMeasuredFieldList = async () => {{
      const source = document.querySelector("[data-field-list-measured-source]");
      if (!source) return;
      if (document.fonts && document.fonts.ready) {{
        try {{ await document.fonts.ready; }} catch (_error) {{}}
      }}
      const main = document.querySelector("main");
      const contacts = Array.from(source.querySelectorAll("[data-measured-contact]"));
      const title = source.dataset.title || document.title || "Field List";
      const columnCount = Number(source.dataset.columnCount || 1) === 2 ? 2 : 1;
      main.innerHTML = "";

      let contactIndex = 0;
      let pageNumber = 1;
      while (contactIndex < contacts.length) {{
        const page = document.createElement("section");
        page.className = "print-field-list-manual-page";
        page.dataset.pageNumber = String(pageNumber);

        const header = document.createElement("header");
        header.className = "print-field-list-header";
        const heading = document.createElement("h1");
        heading.textContent = title;
        header.appendChild(heading);

        const body = document.createElement("div");
        body.className = "print-field-list-manual-page-body";
        const grid = document.createElement("div");
        grid.className = `print-field-list-meeting-contacts print-field-list-meeting-contacts-${{columnCount}}`;
        const columns = [];
        for (let index = 0; index < columnCount; index += 1) {{
          const column = document.createElement("div");
          column.className = "print-field-list-meeting-contact-column";
          columns.push(column);
          grid.appendChild(column);
        }}
        body.appendChild(grid);
        page.appendChild(header);
        page.appendChild(body);
        main.appendChild(page);

        let addedToPage = 0;
        for (let columnIndex = 0; columnIndex < columnCount && contactIndex < contacts.length; columnIndex += 1) {{
          const column = columns[columnIndex];
          while (contactIndex < contacts.length) {{
            const row = contacts[contactIndex].firstElementChild.cloneNode(true);
            column.appendChild(row);
            const overflowed = body.scrollHeight > body.clientHeight + 1 || column.scrollHeight > body.clientHeight + 1;
            if (overflowed && column.children.length > 1) {{
              column.removeChild(row);
              break;
            }}
            contactIndex += 1;
            addedToPage += 1;
            if (overflowed) break;
          }}
        }}

        if (addedToPage === 0 && contactIndex < contacts.length) {{
          columns[0].appendChild(contacts[contactIndex].firstElementChild.cloneNode(true));
          contactIndex += 1;
        }}
        pageNumber += 1;
      }}
    }};

    const paginateTemplateFieldList = async () => {{
      if (!document.body.classList.contains("manual-pages")) return;
      if (document.querySelector("[data-field-list-measured-source]")) return;
      if (document.fonts && document.fonts.ready) {{
        try {{ await document.fonts.ready; }} catch (_error) {{}}
      }}
      const main = document.querySelector("main");
      const blocks = Array.from(main.querySelectorAll(":scope > .print-field-list-meeting-block"));
      if (!blocks.length) return;
      main.innerHTML = "";

      let templatePageNumber = 1;
      const makePage = (includeHeader = false) => {{
        const page = document.createElement("section");
        page.className = "print-field-list-manual-page";
        page.dataset.pageNumber = String(templatePageNumber);
        templatePageNumber += 1;
        const body = document.createElement("div");
        body.className = "print-field-list-manual-page-body";
        if (includeHeader) {{
          const header = document.createElement("header");
          header.className = "print-field-list-header";
          const heading = document.createElement("h1");
          heading.textContent = document.title || "Field List";
          header.appendChild(heading);
          page.appendChild(header);
        }}
        page.appendChild(body);
        main.appendChild(page);
        return body;
      }};

      let body = makePage(!document.body.classList.contains("duplicate-meeting-print"));
      blocks.forEach((block) => {{
        const clone = block.cloneNode(true);
        const separator = body.children.length && !document.body.classList.contains("no-separator-lines")
          ? document.createElement("div")
          : null;
        if (separator) {{
          separator.className = "print-field-list-meeting-separator";
          body.appendChild(separator);
        }}
        body.appendChild(clone);
        const overflowed = body.scrollHeight > body.clientHeight + 1;
        if (overflowed && body.children.length > (separator ? 2 : 1)) {{
          body.removeChild(clone);
          if (separator) body.removeChild(separator);
          body = makePage(false);
          body.appendChild(clone);
        }}
      }});
    }};

    const hidePageEdgeSeparators = () => {{
      const separators = Array.from(document.querySelectorAll(".print-field-list-meeting-separator, .print-field-list-inner-separator"));
      if (!separators.length) return;
      const pageHeight = 11 * 96;
      const pageTopContent = {page_top_content_px:.2f};
      const edgeTop = pageTopContent + 144;
      const edgeBottom = 96;
      separators.forEach((separator) => {{
        separator.classList.remove("is-page-edge-hidden");
        const rect = separator.getBoundingClientRect();
        const top = rect.top + window.scrollY;
        const pageOffset = ((top % pageHeight) + pageHeight) % pageHeight;
        if (pageOffset < edgeTop || pageOffset > pageHeight - edgeBottom) {{
          separator.classList.add("is-page-edge-hidden");
        }}
      }});
    }};

    window.addEventListener("load", async () => {{
      await paginateMeasuredFieldList();
      await paginateTemplateFieldList();
      hidePageEdgeSeparators();
      setTimeout(() => window.print(), 250);
    }});
    window.addEventListener("beforeprint", hidePageEdgeSeparators);
  </script>
</body>
</html>"""


def _field_list_print_message_html(title: str, message: str) -> str:
    return f"""<!doctype html>
<html>
<head><meta charset="utf-8"><title>{_print_escape(title)}</title></head>
<body style="font-family: Arial, sans-serif; padding: 24px;">
  <h1>{_print_escape(title)}</h1>
  <p>{_print_escape(message)}</p>
</body>
</html>"""


def _field_list_print_css(settings: dict[str, Any]) -> str:
    page_width = PAGE_WIDTH_IN
    page_height = PAGE_HEIGHT_IN
    font_family = _print_escape(settings.get("font_family") or "Arial")
    base_font_size = float(settings.get("base_font_size_pt") or 10)
    line_height = float(settings.get("line_height") or 1.2)
    margin_top = float(settings.get("margin_top_in") or 0.35)
    margin_right = float(settings.get("margin_right_in") or 0.35)
    margin_bottom = float(settings.get("margin_bottom_in") or 0.35)
    margin_left = float(settings.get("margin_left_in") or 0.35)
    printable_width = max(1.0, page_width - margin_left - margin_right)
    column_width = min(float(settings.get("column_width_in") or 4.0), printable_width)
    manual_pages = bool(settings.get("_manual_pages"))
    content_height = max(1.0, page_height - 0.05 if manual_pages else page_height - margin_top - margin_bottom - 0.25)
    page_margin_rule = "margin: 0;" if manual_pages else f"margin: {margin_top}in {margin_right}in {margin_bottom}in {margin_left}in;"
    manual_main_width = f"{page_width}in" if manual_pages else "100%"
    manual_footer_bottom = max(0.05, min(0.18, margin_bottom * 0.35))
    title_font = _print_escape(settings.get("title_font_family") or font_family)
    meeting_font = _print_escape(settings.get("meeting_name_font_family") or font_family)
    title_size = float(settings.get("title_font_size_pt") or 14)
    meeting_size = float(settings.get("meeting_name_font_size_pt") or 12)
    bible_study_size = float(settings.get("bible_study_font_size_pt") or 11)
    column_gap = float(settings.get("two_column_gap_ch") or 4)
    base_weight = 800 if settings.get("base_font_bold") else 500
    base_style = "italic" if settings.get("base_font_italic") else "normal"
    base_decoration = "underline" if settings.get("base_font_underline") else "none"
    title_weight = 800
    title_style = "italic" if settings.get("title_font_italic") else "normal"
    title_decoration = "underline" if settings.get("title_font_underline") else "none"
    meeting_weight = 800
    meeting_style = "italic" if settings.get("meeting_name_font_italic") else "normal"
    meeting_decoration = "underline" if settings.get("meeting_name_font_underline") else "none"
    return f"""
@page {{
  size: {page_width}in {page_height}in;
  {page_margin_rule}
}}
* {{
  box-sizing: border-box;
}}
body {{
  margin: 0;
  font-family: "{font_family}", Arial, sans-serif;
  font-size: {base_font_size}pt;
  line-height: {line_height};
  font-weight: {base_weight};
  font-style: {base_style};
  text-decoration: {base_decoration};
  color: #111;
}}
.print-field-list.manual-pages {{
  width: {page_width}in;
  margin: 0;
}}
main {{
  width: calc(100% - 0.16in);
  max-width: calc(100% - 0.16in);
  margin-left: auto;
  margin-right: auto;
}}
.print-field-list.manual-pages main {{
  width: {manual_main_width};
  max-width: {manual_main_width};
  margin: 0;
}}
.print-field-list-manual-page {{
  width: {page_width}in;
  height: {content_height:.3f}in;
  min-height: {content_height:.3f}in;
  position: relative;
  display: flex;
  flex-direction: column;
  break-after: page;
  page-break-after: always;
  overflow: hidden;
  padding: {margin_top}in {margin_right}in {margin_bottom}in {margin_left}in;
}}
.print-field-list-manual-page::after {{
  content: attr(data-page-number);
  position: absolute;
  left: 0;
  right: 0;
  bottom: {manual_footer_bottom:.3f}in;
  height: 0.14in;
  text-align: center;
  font-family: "{font_family}", Arial, sans-serif;
  font-size: 10px;
  line-height: 1;
  font-weight: 500;
}}
.print-field-list:not(.show-page-numbers) .print-field-list-manual-page::after {{
  display: none;
}}
.print-field-list-manual-page:last-child {{
  break-after: auto;
  page-break-after: auto;
}}
.print-field-list-manual-page-body {{
  flex: 1 1 auto;
  min-height: 0;
  max-height: none;
  overflow: hidden;
}}
.print-field-list-measured-source {{
  display: none;
}}
.print-field-list-header {{
  margin-bottom: 0.06in;
  text-align: center;
}}
.print-field-list.one-column-print .print-field-list-header {{
  width: {column_width:.3f}in;
  max-width: 100%;
  margin-left: 0;
  margin-right: auto;
}}
.print-field-list-header h1 {{
  margin: 0;
  font-family: "{title_font}", Arial, sans-serif;
  font-size: {title_size}pt;
  font-weight: {title_weight};
  font-style: {title_style};
  text-decoration: {title_decoration};
}}
.print-field-list-meeting-block {{
  break-inside: avoid;
  page-break-inside: avoid;
  padding: 0.06in 0 0.08in;
}}
.print-field-list-meeting-separator {{
  height: 0;
  margin: 0.08in 0 0;
  border-top: 1px solid rgba(17, 17, 17, 0.78);
  break-after: avoid;
  page-break-after: avoid;
}}
.print-field-list-meeting-separator.is-page-edge-hidden,
.print-field-list-inner-separator.is-page-edge-hidden {{
  display: none;
}}
.print-field-list-template-item {{
  width: min(100%, var(--item-width));
  font-size: var(--item-font-size);
  margin-top: var(--item-gap, 0);
  margin-bottom: 0.04in;
}}
.print-field-list-template-item.item-meeting_name,
.print-field-list-template-item.item-meetings {{
  width: 100%;
}}
.print-field-list.one-column-print .print-field-list-template-item.item-meeting_name {{
  width: min(100%, var(--item-width));
}}
.print-field-list-template-item.item-bible_study_union {{
  margin-left: auto;
  margin-right: auto;
}}
.print-field-list-title {{
  font-family: "{title_font}", Arial, sans-serif;
  font-size: {title_size}pt;
  font-weight: {title_weight};
  font-style: {title_style};
  text-decoration: {title_decoration};
}}
.print-field-list-meeting-name {{
  font-family: "{meeting_font}", Arial, sans-serif;
  font-size: {meeting_size}pt;
  font-weight: {meeting_weight};
  font-style: {meeting_style};
  text-decoration: {meeting_decoration};
}}
.print-field-list-duplicate-meeting-name {{
  display: grid;
  grid-template-columns: repeat(2, minmax(0, calc((100% - {column_gap}ch) / 2)));
  column-gap: {column_gap}ch;
  width: 100%;
  font-family: "{meeting_font}", Arial, sans-serif;
  font-size: {meeting_size}pt;
  font-weight: 800;
  font-style: {meeting_style};
  text-decoration: {meeting_decoration};
}}
.print-field-list-flat-block {{
  break-inside: auto;
  page-break-inside: auto;
}}
.print-field-list-flat-title {{
  margin: 0 0 0.1in;
  text-align: center;
  font-family: "{title_font}", Arial, sans-serif;
  font-size: {title_size}pt;
  font-weight: {title_weight};
  font-style: {title_style};
  text-decoration: {title_decoration};
}}
.align-left {{ text-align: left; }}
.align-center {{ text-align: center; }}
.align-right {{ text-align: right; }}
.print-field-list-meeting-contacts {{
  display: grid;
  gap: 0;
  width: 100%;
  max-width: 100%;
}}
.print-field-list-meeting-contacts-2 {{
  position: relative;
  grid-template-columns: repeat(2, minmax(0, calc((100% - {column_gap}ch) / 2)));
  column-gap: {column_gap}ch;
  align-items: start;
}}
.print-field-list-meeting-contacts-1 {{
  width: {column_width:.3f}in;
  max-width: 100%;
}}
.print-field-list-flat-block .print-field-list-meeting-contacts-2::before,
.print-field-list-manual-page .print-field-list-meeting-contacts-2::before {{
  content: "";
  position: absolute;
  top: 0;
  bottom: 0;
  left: 50%;
  border-left: 1px solid rgba(17, 17, 17, 0.55);
  transform: translateX(-50%);
}}
.print-field-list.no-vertical-separator-lines .print-field-list-meeting-contacts-2::before {{
  display: none;
}}
.print-field-list-meeting-contact-column {{
  width: 100%;
  max-width: 100%;
  min-width: 0;
}}
.print-field-list-contact-row {{
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 0.33em;
  break-inside: avoid;
  page-break-inside: avoid;
  min-width: 0;
}}
.print-field-list-phone-line {{
  display: flex;
  align-items: baseline;
  column-gap: 0.33em;
  white-space: nowrap;
  width: max-content;
  overflow: visible;
}}
.print-field-list-phone-code {{
  display: inline-block;
  min-width: 2.4ch;
  white-space: pre;
}}
.print-field-list-phone-line.is-primary-phone .print-field-list-phone-code {{
  min-width: 2.4ch;
  max-width: 2.4ch;
  overflow: hidden;
}}
.print-field-list-phone-line.is-secondary-phone .print-field-list-phone-code {{
  max-width: none;
  overflow: visible;
}}
.print-field-list-phone-line.is-combined-phone-line {{
  display: flex;
  align-items: baseline;
  gap: 0.35ch;
}}
.print-field-list-phone-pair {{
  display: inline-flex;
  gap: 1ch;
  white-space: nowrap;
}}
.print-field-list-phone-comma {{
  display: inline-block;
}}
.print-field-list-phone-continuation-gap {{
  display: inline-block;
  width: 2ch;
}}
.print-field-list-phone-stack {{
  font-weight: inherit;
  min-width: 0;
  width: max-content;
  overflow: visible;
}}
.print-field-list-contact-row.is-compact-address .print-field-list-phone-stack {{
  min-width: 0;
  width: max-content;
  max-width: none;
  overflow: visible;
}}
.print-field-list-contact-name {{
  font-weight: inherit;
  white-space: normal;
  overflow-wrap: anywhere;
}}
.print-field-list-contact-row.is-compact-address .print-field-list-contact-name {{
  overflow-wrap: anywhere;
}}
.print-field-list-contact-main {{
  min-width: 0;
}}
.print-field-list-contact-last-name {{
  font-weight: 800;
}}
.print-field-list-relationship-pair {{
  white-space: nowrap;
}}
.print-field-list-contact-name-line.is-indented {{
  padding-left: 1ch;
}}
.print-field-list-contact-inline-address {{
  display: inline;
  font-size: 0.95em;
  font-style: normal;
  font-weight: 500;
  vertical-align: baseline;
}}
.print-field-list-contact-inline-address-group {{
  white-space: nowrap;
}}
.print-field-list-contact-inline-separator {{
  display: inline;
}}
.print-field-list-contact-inline-separator::after {{
  content: " ";
}}
.print-field-list-contact-extra-address {{
  display: block;
  font-size: 0.95em;
  font-style: normal;
  font-weight: 500;
  line-height: inherit;
  padding-left: 1ch;
  white-space: normal;
}}
.print-field-list-phone-address-continuation {{
  display: block;
  font-size: 0.95em;
  font-style: normal;
  font-weight: 500;
  line-height: 1.15;
  padding-left: 15ch;
  white-space: normal;
}}
.print-field-list-contact-address {{
  font-size: 0.95em;
  font-style: normal;
  font-weight: 500;
  min-width: 0;
  overflow-wrap: break-word;
  word-break: normal;
  white-space: pre-line;
}}
.print-field-list-contact-address > div {{
  min-width: 0;
  max-width: 100%;
  white-space: pre-line;
  overflow-wrap: break-word;
  word-break: normal;
}}
.print-field-list-print-book-note,
.print-field-list-print-after-note {{
  font-style: normal;
  font-family: inherit;
  font-weight: 500;
}}
.print-field-list-print-note-line {{
  min-width: 0;
  overflow-wrap: break-word;
  word-break: normal;
}}
.print-field-list-contact-address .print-field-list-print-note-line {{
  font-size: inherit;
}}
.print-field-list-bible-cluster {{
  break-inside: avoid;
  page-break-inside: avoid;
  margin-top: 0.18in;
  width: 100%;
  font-size: {bible_study_size}pt;
}}
.print-field-list-keep-together-block {{
  break-inside: avoid;
  page-break-inside: avoid;
}}
.print-field-list-bible-cluster.align-center {{
  text-align: center;
}}
.print-field-list-bible-cluster.align-left {{
  text-align: left;
}}
.print-field-list-inner-separator {{
  width: 38%;
  min-width: 2.8in;
  max-width: 4.25in;
  margin: 0.14in auto 0.08in;
  border-top: 1px solid rgba(17, 17, 17, 0.78);
}}
.print-field-list-meeting-preview-row {{
  display: grid;
  grid-template-columns: repeat(28, minmax(0, 1fr));
  gap: var(--meeting-grid-column-gap, 6px);
  width: 100%;
  font-size: var(--meeting-row-font-size, inherit);
}}
.print-field-list-meeting-preview-row.is-underlined-row,
.print-field-list-meeting-preview-flow.is-underlined-row {{
  position: relative;
  padding-bottom: 0.015in;
  margin-bottom: 0.02in;
}}
.print-field-list-meeting-preview-row.is-underlined-row::after,
.print-field-list-meeting-preview-flow.is-underlined-row::after {{
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  border-bottom: 1px solid rgba(83, 73, 58, 0.72);
}}
.print-field-list-bible-cluster.align-center .print-field-list-meeting-preview-row,
.print-field-list-bible-cluster.align-center .print-field-list-meeting-preview-flow {{
  margin-left: auto;
  margin-right: auto;
}}
.print-field-list-bible-cluster.align-left .print-field-list-meeting-preview-row,
.print-field-list-bible-cluster.align-left .print-field-list-meeting-preview-flow {{
  margin-left: 0;
  margin-right: auto;
}}
.print-field-list-meeting-preview-cell {{
  min-width: 0;
  padding: 0;
  overflow: visible;
  white-space: nowrap;
  font-weight: 500;
  font-style: normal;
  text-decoration: none;
}}
.print-field-list-meeting-preview-cell.align-center {{
  text-align: center;
}}
.print-field-list-meeting-preview-cell.align-right {{
  text-align: right;
}}
.print-field-list-meeting-preview-cell.align-left {{
  text-align: left;
}}
.print-field-list-meeting-preview-cell.is-bold {{
  font-weight: 800;
}}
.print-field-list-meeting-preview-cell.is-italic {{
  font-style: italic;
}}
.print-field-list-meeting-preview-cell em,
.print-field-list-meeting-preview-cell i,
.print-field-list-meeting-preview-flow-text em,
.print-field-list-meeting-preview-flow-text i {{
  font-style: italic;
}}
.print-field-list-meeting-preview-cell.is-underlined {{
  text-decoration: underline;
}}
.print-field-list-meeting-preview-blank {{
  min-height: 0.12in;
}}
.print-field-list-meeting-preview-divider {{
  border-top: 1px solid #111;
  height: 0.25em;
  margin-top: 0.15em;
}}
.print-field-list-meeting-preview-flow {{
  width: 100%;
  padding: 0;
  text-align: left;
  font-size: var(--meeting-row-font-size, inherit);
  font-weight: 500;
  font-style: normal;
  text-decoration: none;
}}
.print-field-list-meeting-preview-flow-grid {{
  display: grid;
  grid-template-columns: repeat(28, minmax(0, 1fr));
  gap: var(--meeting-grid-column-gap, 6px);
  width: 100%;
}}
.print-field-list-meeting-preview-flow-band {{
  display: grid;
  grid-template-columns: repeat(28, minmax(0, 1fr));
  gap: var(--meeting-grid-column-gap, 6px);
  width: 100%;
}}
.print-field-list-meeting-preview-flow-band.is-stacked-band {{
  margin-top: 0.04in;
}}
.print-field-list-meeting-preview-flow-column {{
  background: transparent;
}}
.print-field-list-meeting-preview-flow-text {{
  white-space: pre-line;
}}
.print-field-list-meeting-preview-flow-text.align-center {{
  text-align: center;
}}
.print-field-list-meeting-preview-flow-text.align-right {{
  text-align: right;
}}
.print-field-list-meeting-preview-flow-text.is-stacked-segment {{
  margin-top: 0.12in;
}}
.print-field-list-meeting-preview-flow-text div,
.print-field-list-meeting-preview-flow-text p {{
  margin: 0;
}}
@media screen {{
  body {{
    max-width: {page_width}in;
    margin: 24px auto;
    padding: {margin_top}in {margin_right}in {margin_bottom}in {margin_left}in;
    background: #fff;
    box-shadow: 0 8px 30px rgba(0, 0, 0, 0.18);
  }}
}}
"""


FIELD_LIST_SHARED_SETTING_KEYS = (
    "margin_left_in",
    "margin_right_in",
    "margin_top_in",
    "margin_bottom_in",
    "font_family",
    "base_font_size_pt",
    "base_font_bold",
    "base_font_italic",
    "base_font_underline",
    "line_height",
    "preview_scale",
    "page_column_count",
    "include_bible_study_union_info",
    "two_column_gap_ch",
    "column_width_in",
    "address_align",
    "address_map_provider",
    "address_italic",
    "title_font_family",
    "title_font_size_pt",
    "title_font_bold",
    "title_font_italic",
    "title_font_underline",
    "title_align",
    "meeting_name_font_family",
    "meeting_name_font_size_pt",
    "meeting_name_font_bold",
    "meeting_name_font_italic",
    "meeting_name_font_underline",
    "meeting_name_align",
    "bible_study_font_size_pt",
    "bible_study_union_align",
    "separator_lines",
    "separator_vertical_lines",
    "manual_palette_items",
    "print_order_mode",
    "print_order_json",
    "meeting_page_mode",
    "field_separator_pages",
    "double_column_index",
)


def export_field_list_payload() -> dict[str, Any]:
    settings = _load_address_book_settings()
    template = _load_template(ensure_default_field_list_template())
    templates = [
        {
            "id": int(template.get("id") or 0),
            "name": _clean(template.get("name")) or DEFAULT_TEMPLATE_NAME,
            "is_default": True,
            "settings_json": template.get("settings_json") or _settings_json(settings),
            "items": template.get("items") or [],
        }
    ]
    from app.services.meeting_toc_service import list_unlisted_toc_meetings

    return {
        "format": "contactsfreeshare.field_list.v1",
        "page_width_in": PAGE_WIDTH_IN,
        "page_height_in": PAGE_HEIGHT_IN,
        "unlisted_toc_meetings": list_unlisted_toc_meetings(),
        "address_book_settings": {
            key: settings[key]
            for key in FIELD_LIST_SHARED_SETTING_KEYS
        },
        "templates": templates,
    }


def import_field_list_payload(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    settings = payload.get("address_book_settings")
    templates = payload.get("templates")
    if not isinstance(settings, dict) or not isinstance(templates, list):
        return False

    save_address_book_settings(settings)
    base_settings = _normalize_address_book_settings_values(settings, _load_address_book_settings())
    normalized_templates = []
    for raw_template in templates:
        if not isinstance(raw_template, dict):
            continue
        if not bool(raw_template.get("is_default")):
            continue
        template_name = _clean(raw_template.get("name")) or DEFAULT_TEMPLATE_NAME
        if template_name.lower() not in SHARED_FIELD_LIST_TEMPLATE_NAMES:
            continue
        items = raw_template.get("items")
        if not isinstance(items, list):
            items = []
        template_settings = _settings_from_json(
            raw_template.get("settings_json") or raw_template.get("settings"),
            base_settings,
        )
        normalized_templates.append(
            {
                "name": template_name,
                "is_default": True,
                "settings_json": _settings_json(template_settings),
                "items": [
                    _normalize_template_item(item, index + 1)
                    for index, item in enumerate(items)
                    if isinstance(item, dict) and _is_supported_item_type(item)
                ],
            }
        )

    if not normalized_templates:
        return False

    with get_connection() as conn:
        incoming_names = [_clean(template["name"]).lower() for template in normalized_templates]
        conn.execute("UPDATE field_list_templates SET is_default = 0")
        if incoming_names:
            shared_rows = conn.execute(
                """
                SELECT id, name
                FROM field_list_templates
                """
            ).fetchall()
            for row in shared_rows:
                if _clean(row["name"]).lower() not in incoming_names:
                    continue
                conn.execute("DELETE FROM field_list_template_items WHERE template_id = ?", (int(row["id"]),))
                conn.execute("DELETE FROM field_list_templates WHERE id = ?", (int(row["id"]),))
        for template in normalized_templates:
            cursor = conn.execute(
                """
                INSERT INTO field_list_templates (id, name, is_default, settings_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    None,
                    template["name"],
                    1 if template["is_default"] else 0,
                    template["settings_json"],
                    _now_text(),
                    _now_text(),
                ),
            )
            inserted_id = int(cursor.lastrowid)
            for index, item in enumerate(template["items"], start=1):
                conn.execute(
                    """
                    INSERT INTO field_list_template_items (
                      template_id,
                      item_type,
                      label_number,
                      x_in,
                      y_in,
                      width_in,
                      height_in,
                      font_size_pt,
                      sort_order,
                      created_at,
                      updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        inserted_id,
                        item["item_type"],
                        item["label_number"],
                        item["x_in"],
                        item["y_in"],
                        item["width_in"],
                        item["height_in"],
                        item["font_size_pt"],
                        index,
                        _now_text(),
                        _now_text(),
                    ),
                )
        conn.commit()
    if "unlisted_toc_meetings" in payload:
        from app.services.meeting_toc_service import replace_unlisted_toc_meetings

        raw_meetings = payload.get("unlisted_toc_meetings")
        replace_unlisted_toc_meetings(raw_meetings if isinstance(raw_meetings, list) else [])
    ensure_default_field_list_template()
    return True
