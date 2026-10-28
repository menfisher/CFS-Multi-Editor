#!/usr/bin/env python3
"""Smoke test: meeting table PDF layout matches preview (no word-wrap)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.address_book_pdf_service import (  # noqa: E402
    _default_meeting_content_width_pt,
    _html_styled_lines,
    _meeting_column_gap_pt,
    _meeting_grid_cell_bounds,
    _meeting_row_line_count,
    _text_width,
    build_address_book_pdf,
)

_LAYOUT = {
    "trim_width_in": 3.5,
    "trim_height_in": 5.5,
    "margin_left_in": 0.14,
    "margin_right_in": 0.14,
    "margin_top_in": 0.14,
    "margin_bottom_in": 0.14,
    "base_font_size_pt": 7,
    "line_height": 1.2,
    "font_family": "Arial",
}

_HAMMOND_HEADER_CELLS = [
    {"html": "Jan", "col_start": 1, "col_span": 4},
    {"html": "G. Stafford", "col_start": 5, "col_span": 4},
    {"html": "V. Hoover", "col_start": 9, "col_span": 4},
    {"html": "R. Clemens", "col_start": 13, "col_span": 4},
    {"html": "J. Corneille", "col_start": 17, "col_span": 4},
    {"html": "T. Stafford", "col_start": 21, "col_span": 4, "is_bold": True},
    {"html": "Hammond #1", "col_start": 25, "col_span": 4},
]


def _pdf_text_positions(pdf: bytes) -> list[tuple[float, float, float, str]]:
    positions: list[tuple[float, float, float, str]] = []
    stream = pdf.decode("latin-1", errors="replace")
    for part in re.findall(r"stream\n(.*?)\nendstream", stream, re.DOTALL):
        for block in re.findall(r"BT(.*?)ET", part, re.DOTALL):
            size_match = re.search(r"/F\d+\s+([\d.]+)\s+Tf", block)
            tm_match = re.search(r"1 0 0 1 ([\d.]+) ([\d.]+) Tm", block)
            text_match = re.search(r"\(([^)]*)\)\s*Tj", block)
            if not (size_match and tm_match and text_match):
                continue
            size = float(size_match.group(1))
            x = float(tm_match.group(1))
            y = float(tm_match.group(2))
            text = text_match.group(1).replace("\\(", "(").replace("\\)", ")")
            positions.append((x, y, size, text))
    return positions


_PREVIEW_PRINTABLE_WIDTH_PX = 3.22 * 220.0  # default pocket-book editor canvas width


def test_column_gap_scales_to_printable_width() -> None:
    content_width = _default_meeting_content_width_pt()
    scaled_gap = _meeting_column_gap_pt(
        6,
        grid_width_pt=content_width,
        preview_printable_width_px=_PREVIEW_PRINTABLE_WIDTH_PX,
    )
    legacy_gap = _meeting_column_gap_pt(6)
    assert scaled_gap < legacy_gap
    assert scaled_gap < 2.5


def test_six_column_table_keeps_single_line_cells() -> None:
    content_width = _default_meeting_content_width_pt()
    column_gap_pt = _meeting_column_gap_pt(
        6,
        grid_width_pt=content_width,
        preview_printable_width_px=_PREVIEW_PRINTABLE_WIDTH_PX,
    )
    row_font_size = 7.0
    font_family = "Arial"

    for cell in _HAMMOND_HEADER_CELLS:
        _, cell_width = _meeting_grid_cell_bounds(
            cell["col_start"],
            cell["col_span"],
            content_width,
            column_gap_pt=column_gap_pt,
        )
        styled_lines = _html_styled_lines(cell["html"])
        assert len(styled_lines) == 1, f"expected one styled line for {cell['html']!r}, got {len(styled_lines)}"
        line_text = "".join(str(run.get("text") or "") for run in styled_lines[0])
        assert line_text == cell["html"], f"expected {cell['html']!r}, got {line_text!r}"
        assert _text_width(line_text, row_font_size, font_family) == _text_width(
            cell["html"], row_font_size, font_family
        )

    row = {"kind": "cells", "cells": _HAMMOND_HEADER_CELLS, "column_gap_px": 6}
    assert _meeting_row_line_count(row, content_width=content_width) == 1


def test_build_pdf_with_meeting_table() -> None:
    row = {
        "kind": "cells",
        "font_size_pt": 6.0,
        "column_gap_px": 6,
        "preview_printable_width_px": _PREVIEW_PRINTABLE_WIDTH_PX,
        "grid_column_count": 28,
        "cells": [
            {**cell, "align": "auto"}
            for cell in _HAMMOND_HEADER_CELLS
        ],
    }
    pdf = build_address_book_pdf(
        fields=[
            {
                "name": "Hammond, Metairie Union Meetings",
                "meetings": [
                    {
                        "name": "Sample Meeting",
                        "contacts": [],
                    }
                ],
            }
        ],
        meeting_preview_sections=[
            {
                "field": "Hammond, Metairie Union Meetings",
                "meeting": "Sample Meeting",
                "rows": [row],
            }
        ],
        settings={"address_map_provider": "google", "address_align": "left"},
        layout=_LAYOUT,
        print_order_mode="default",
        print_order_json="{}",
    )
    assert pdf.startswith(b"%PDF"), "expected PDF bytes"

    drawn_text = [text for _x, _y, _size, text in _pdf_text_positions(pdf)]
    assert "T. Stafford" in drawn_text, f"T. Stafford must stay on one line; got fragments: {drawn_text!r}"
    assert "Staffor" not in drawn_text, "character-break fragment Staffor must not appear"
    assert "Hammond #1" in drawn_text, "Hammond #1 must not be clipped or split"
    assert drawn_text.count("T.") <= 1 or "T. Stafford" in drawn_text, "T. must not be split from Stafford"

    meeting_fragments = {"Stafford", "Hammond", "#1"}
    found: set[str] = set()
    for _x, _y, _size, text in _pdf_text_positions(pdf):
        for fragment in meeting_fragments:
            if fragment in text:
                found.add(fragment)
    assert found >= {"Stafford", "Hammond", "#1"}, f"missing meeting-table text fragments: {found}"


def test_pdf_uses_effective_font_size_when_override_missing() -> None:
    """Table body rows inherit 6.0pt; PDF must not fall back to book 7.0pt."""
    row = {
        "kind": "cells",
        "effective_font_size_pt": 6.0,
        "column_gap_px": 6,
        "preview_printable_width_px": _PREVIEW_PRINTABLE_WIDTH_PX,
        "grid_column_count": 28,
        "cells": [
            {"html": "T. Stafford", "col_start": 21, "col_span": 4, "align": "auto", "is_underlined": True},
        ],
    }
    pdf = build_address_book_pdf(
        fields=[{"name": "F", "meetings": [{"name": "M", "contacts": []}]}],
        meeting_preview_sections=[{"field": "F", "meeting": "M", "rows": [row]}],
        settings={"address_map_provider": "google"},
        layout=_LAYOUT,
        print_order_mode="default",
        print_order_json="{}",
    )
    sizes = []
    for part in re.findall(r"stream\n(.*?)\nendstream", pdf.decode("latin-1", errors="replace"), re.DOTALL):
        for block in re.findall(r"BT(.*?)ET", part, re.DOTALL):
            if "Stafford" not in block:
                continue
            size_match = re.search(r"/F\d+\s+([\d.]+)\s+Tf", block)
            if size_match:
                sizes.append(float(size_match.group(1)))
    assert sizes, "expected T. Stafford text in PDF"
    assert all(size == 6.0 for size in sizes), f"expected 6.0pt, got {sizes}"


    printable_right = (_LAYOUT["trim_width_in"] - _LAYOUT["margin_right_in"]) * 72.0
    max_right = 0.0
    for part in re.findall(r"stream\n(.*?)\nendstream", pdf.decode("latin-1", errors="replace"), re.DOTALL):
        for block in re.findall(r"BT(.*?)ET", part, re.DOTALL):
            tm = re.search(r"1 0 0 1 ([\d.]+) ([\d.]+) Tm", block)
            text = re.search(r"\(([^)]*)\)\s*Tj", block)
            size = re.search(r"/F\d+\s+([\d.]+)\s+Tf", block)
            if not (tm and text and size):
                continue
            label = text.group(1)
            if label not in {"T. Stafford", "Hammond #1", "Stafford", "Hammond"}:
                continue
            x = float(tm.group(1))
            s = float(size.group(1))
            max_right = max(max_right, x + _text_width(label, s))
    assert max_right <= printable_right + 0.5, f"text extends past printable edge: {max_right:.2f} > {printable_right:.2f}"


def _pdf_page_count(pdf: bytes) -> int:
    return len(re.findall(br"/Type\s*/Page\b", pdf)) - len(re.findall(br"/Type\s*/Pages\b", pdf))


def _pdf_blank_body_page_indexes(pdf: bytes) -> list[int]:
    raw = pdf.decode("latin-1", errors="replace")
    kids_match = re.search(r"/Kids \[(.*?)\]\s*/Count", raw, re.DOTALL)
    if not kids_match:
        return []
    kid_ids = re.findall(r"(\d+) 0 R", kids_match.group(1))
    blank_pages: list[int] = []
    for page_index, kid in enumerate(kid_ids, start=1):
        page_match = re.search(rf"{kid} 0 obj<<(.*?)>>\nendobj", raw, re.DOTALL)
        if not page_match:
            continue
        contents_match = re.search(r"/Contents (\d+) 0 R", page_match.group(1))
        if not contents_match:
            blank_pages.append(page_index)
            continue
        stream_match = re.search(
            rf"{contents_match.group(1)} 0 obj<<.*?>>\nstream\n(.*?)\\nendstream",
            raw,
            re.DOTALL,
        )
        if not stream_match:
            blank_pages.append(page_index)
            continue
        texts = re.findall(r"\(([^)]*)\)\s*Tj", stream_match.group(1))
        texts = [text.replace("\\(", "(").replace("\\)", ")") for text in texts]
        body = [text for text in texts if " - Page " not in text and text.strip(".")]
        if not body:
            blank_pages.append(page_index)
    return blank_pages


def test_unassociated_table_skips_continued_preface_page() -> None:
    """Standalone meeting tables must not get a blank '(continued)' page before them."""
    contacts = [{"name": f"Contact {index:02d}"} for index in range(1, 28)]
    table_rows = [
        {
            "kind": "cells",
            "is_table_start_row": True,
            "no_meeting_association": True,
            "font_size_pt": 6.0,
            "column_gap_px": 6,
            "preview_printable_width_px": _PREVIEW_PRINTABLE_WIDTH_PX,
            "grid_column_count": 28,
            "cells": [{"html": "StandaloneTable", "col_start": 1, "col_span": 28, "align": "auto"}],
        }
    ]
    for row_index in range(1, 45):
        table_rows.append(
            {
                "kind": "cells",
                "is_table_end_row": row_index == 44,
                "font_size_pt": 6.0,
                "column_gap_px": 6,
                "preview_printable_width_px": _PREVIEW_PRINTABLE_WIDTH_PX,
                "grid_column_count": 28,
                "cells": [{"html": f"Row {row_index}", "col_start": 1, "col_span": 28, "align": "auto"}],
            }
        )
    pdf = build_address_book_pdf(
        fields=[
            {
                "name": "Hammond, Metairie Union Meetings",
                "meetings": [{"name": "Metairie", "contacts": contacts}],
            }
        ],
        meeting_preview_sections=[
            {
                "field": "Hammond, Metairie Union Meetings",
                "meeting": "Metairie",
                "rows": table_rows,
            }
        ],
        settings={"address_map_provider": "google", "address_align": "left"},
        layout=_LAYOUT,
        print_order_mode="default",
        print_order_json="{}",
    )
    drawn_text = [text for _x, _y, _size, text in _pdf_text_positions(pdf)]
    assert "StandaloneTable" in drawn_text, "expected standalone table content in PDF"
    assert not any("(continued)" in text for text in drawn_text), (
        f"unassociated table must not insert a blank continued page; got {drawn_text!r}"
    )
    blank_pages = _pdf_blank_body_page_indexes(pdf)
    assert not blank_pages, f"expected no blank body pages, got blank pages {blank_pages}"


def test_associated_full_page_table_has_no_blank_preface_page() -> None:
    contacts = [{"name": f"Contact {index:02d}"} for index in range(1, 28)]
    table_rows = [
        {
            "kind": "cells",
            "is_table_start_row": True,
            "font_size_pt": 6.0,
            "column_gap_px": 6,
            "preview_printable_width_px": _PREVIEW_PRINTABLE_WIDTH_PX,
            "grid_column_count": 28,
            "cells": [{"html": "MeetingTable", "col_start": 1, "col_span": 28, "align": "auto"}],
        }
    ]
    for row_index in range(1, 55):
        table_rows.append(
            {
                "kind": "cells",
                "is_table_end_row": row_index == 54,
                "font_size_pt": 6.0,
                "column_gap_px": 6,
                "preview_printable_width_px": _PREVIEW_PRINTABLE_WIDTH_PX,
                "grid_column_count": 28,
                "cells": [{"html": f"Row {row_index}", "col_start": 1, "col_span": 28, "align": "auto"}],
            }
        )
    pdf = build_address_book_pdf(
        fields=[{"name": "Field", "meetings": [{"name": "Metairie", "contacts": contacts}]}],
        meeting_preview_sections=[{"field": "Field", "meeting": "Metairie", "rows": table_rows}],
        settings={"address_map_provider": "google", "address_align": "left"},
        layout=_LAYOUT,
        print_order_mode="default",
        print_order_json="{}",
    )
    drawn_text = [text for _x, _y, _size, text in _pdf_text_positions(pdf)]
    assert "MeetingTable" in drawn_text
    blank_pages = _pdf_blank_body_page_indexes(pdf)
    assert not blank_pages, f"associated full-page table must not leave blank pages; got {blank_pages}"


def _pdf_text_y_positions(pdf: bytes, needle: str) -> list[float]:
    return [y for _x, y, _size, text in _pdf_text_positions(pdf) if needle in text]


def test_marked_table_stays_on_one_page() -> None:
    """Table Start/End rows must not paginate mid-table when space runs out on the contact page."""
    contacts = [{"name": f"Contact {index:02d}"} for index in range(1, 30)]
    group_id = "block-0-item-0"
    table_rows = [
        {
            "kind": "cells",
            "is_table_start_row": True,
            "keep_together_group": group_id,
            "font_size_pt": 7.0,
            "column_gap_px": 6,
            "preview_printable_width_px": _PREVIEW_PRINTABLE_WIDTH_PX,
            "grid_column_count": 28,
            "cells": [{"html": "Even Months- H. Dawley", "col_start": 1, "col_span": 28, "align": "auto"}],
        },
        {
            "kind": "cells",
            "keep_together_group": group_id,
            "font_size_pt": 7.0,
            "column_gap_px": 6,
            "preview_printable_width_px": _PREVIEW_PRINTABLE_WIDTH_PX,
            "grid_column_count": 28,
            "cells": [{"html": "Odd Months- J. Ekelund", "col_start": 1, "col_span": 28, "align": "auto"}],
        },
        {
            "kind": "cells",
            "is_table_end_row": True,
            "keep_together_group": group_id,
            "font_size_pt": 7.0,
            "column_gap_px": 6,
            "preview_printable_width_px": _PREVIEW_PRINTABLE_WIDTH_PX,
            "grid_column_count": 28,
            "cells": [{"html": "To Mobile: Greg Knaak", "col_start": 1, "col_span": 28, "align": "auto"}],
        },
    ]
    pdf = build_address_book_pdf(
        fields=[{"name": "Union Meetings", "meetings": [{"name": "Fairhope (EKELUND)", "contacts": contacts}]}],
        meeting_preview_sections=[{"field": "Union Meetings", "meeting": "Fairhope (EKELUND)", "rows": table_rows}],
        settings={"address_map_provider": "google", "address_align": "left"},
        layout=_LAYOUT,
        print_order_mode="default",
        print_order_json="{}",
    )
    even_y = _pdf_text_y_positions(pdf, "Even Months")
    odd_y = _pdf_text_y_positions(pdf, "Odd Months")
    mobile_y = _pdf_text_y_positions(pdf, "To Mobile")
    assert even_y and odd_y and mobile_y, "expected all table rows in PDF"
    ys = [*even_y, *odd_y, *mobile_y]
    assert max(ys) - min(ys) < (_LAYOUT["trim_height_in"] * 72.0), (
        f"table rows must stay on one page; y spread {max(ys) - min(ys):.1f} pt"
    )


def _content_footer_page_numbers(pdf: bytes, field_name: str) -> list[int]:
    pattern = re.compile(rf"{re.escape(field_name)} - Page (\d+)")
    numbers: list[int] = []
    for _x, _y, _size, text in _pdf_text_positions(pdf):
        match = pattern.search(text)
        if match:
            numbers.append(int(match.group(1)))
    return numbers


def _meetings_share_content_page(pdf: bytes, *headings: str) -> bool:
    raw = pdf.decode("latin-1", errors="replace")
    for stream in re.findall(r"stream\n(.*?)\nendstream", raw, re.DOTALL):
        if "Table of Contents" in stream or "Index - Page" in stream:
            continue
        if all(heading in stream for heading in headings):
            return True
    return False


def test_complete_meetings_mode_packs_small_meetings_on_one_page() -> None:
    contact = {
        "name": "DOE, John",
        "phones": [{"value": "5551234567", "code": "HM"}],
        "addresses": [],
    }
    fields = [
        {
            "name": "Field A",
            "meetings": [
                {"name": "Meeting 1", "contacts": [contact]},
                {"name": "Meeting 2", "contacts": [contact]},
            ],
        }
    ]
    common = {
        "fields": fields,
        "meeting_preview_sections": [],
        "layout": _LAYOUT,
        "print_order_mode": "alphabetical",
        "print_order_json": "{}",
    }
    one_per_page = build_address_book_pdf(
        **common,
        settings={
            "address_map_provider": "google",
            "address_align": "left",
            "include_bible_study_union_info": 0,
            "meeting_page_mode": "one_meeting",
        },
    )
    packed = build_address_book_pdf(
        **common,
        settings={
            "address_map_provider": "google",
            "address_align": "left",
            "include_bible_study_union_info": 0,
            "meeting_page_mode": "complete_meetings",
        },
    )
    one_page_numbers = _content_footer_page_numbers(one_per_page, "Field A")
    packed_page_numbers = _content_footer_page_numbers(packed, "Field A")
    assert max(one_page_numbers) >= 2, f"expected one meeting per page, got {one_page_numbers}"
    assert _meetings_share_content_page(packed, "Meeting 1", "Meeting 2"), (
        "expected both meetings on the same content page"
    )
    assert not _meetings_share_content_page(one_per_page, "Meeting 1", "Meeting 2"), (
        "expected one-meeting-per-page layout to split meetings across pages"
    )


def test_grandchildren_relationship_lines_wrap_within_max_width() -> None:
    from app.services.field_list_service import _wrap_contact_name_lines

    font_size = 7.0
    font_family = "Arial"
    max_width = 120.0
    contact = {
        "name": "TH, Tammy",
        "children": [],
        "other_relationships": [
            {
                "relation_type": "Grandchildren",
                "relation_value": "Prudhomme: Laiyla, Brie'elle, Remmie, Chayauna",
            }
        ],
    }
    lines = _wrap_contact_name_lines(
        contact,
        lambda text: _text_width(text, font_size, font_family),
        max_width,
    )
    assert len(lines) >= 2, f"expected wrapped grandchildren lines, got {lines!r}"
    for line in lines:
        assert _text_width(line, font_size, font_family) <= max_width + 0.5, line


def test_address_book_contact_text_stays_inside_right_margin() -> None:
    contact = {
        "name": "TH, Tammy",
        "phones": [{"value": "2059678901", "code": "HM"}],
        "addresses": [{"text": "1020 Lake St\nNatchitoches, LA 71457"}],
        "children": [],
        "other_relationships": [
            {
                "relation_type": "Grandchildren",
                "relation_value": "Prudhomme: Laiyla, Brie'elle, Remmie, Chayauna",
            }
        ],
    }
    pdf = build_address_book_pdf(
        fields=[
            {
                "name": "Field A",
                "meetings": [{"name": "Meeting 1", "contacts": [contact]}],
            }
        ],
        meeting_preview_sections=[],
        layout=_LAYOUT,
        settings={
            "address_map_provider": "google",
            "address_align": "right",
            "include_bible_study_union_info": 0,
            "meeting_page_mode": "one_meeting",
        },
        print_order_mode="alphabetical",
        print_order_json="{}",
    )
    page_width = float(_LAYOUT["trim_width_in"]) * 72.0
    margin_right = float(_LAYOUT["margin_right_in"]) * 72.0
    right_edge = page_width - margin_right
    for x, _y, size, text in _pdf_text_positions(pdf):
        if not text.strip():
            continue
        assert x + _text_width(text, size, "Arial") <= right_edge + 0.5, (
            f"text {text!r} at x={x} exceeds right margin"
        )


def test_complete_meetings_mode_starts_each_field_on_new_page() -> None:
    contact = {
        "name": "DOE, John",
        "phones": [{"value": "5551234567", "code": "HM"}],
        "addresses": [],
    }
    pdf = build_address_book_pdf(
        fields=[
            {
                "name": "AL Central",
                "meetings": [{"name": "Meeting Central", "contacts": [contact]}],
            },
            {
                "name": "AL North",
                "meetings": [{"name": "Meeting North", "contacts": [contact]}],
            },
        ],
        meeting_preview_sections=[],
        layout=_LAYOUT,
        settings={
            "address_map_provider": "google",
            "address_align": "left",
            "include_bible_study_union_info": 0,
            "meeting_page_mode": "complete_meetings",
        },
        print_order_mode="alphabetical",
        print_order_json="{}",
    )
    assert not _meetings_share_content_page(pdf, "Meeting Central", "Meeting North"), (
        "expected each field to start on a new page in complete meetings mode"
    )
    central_pages = _content_footer_page_numbers(pdf, "AL Central")
    north_pages = _content_footer_page_numbers(pdf, "AL North")
    assert central_pages and north_pages, "expected footers for both fields"
    assert max(central_pages) < min(north_pages), (
        f"expected AL North after AL Central, got central={central_pages} north={north_pages}"
    )


def test_complete_meetings_mode_leaves_separator_gap() -> None:
    contact = {
        "name": "DOE, John",
        "phones": [{"value": "5551234567", "code": "HM"}],
        "addresses": [],
    }
    pdf = build_address_book_pdf(
        fields=[
            {
                "name": "Field A",
                "meetings": [
                    {"name": "Meeting 1", "contacts": [contact]},
                    {"name": "Meeting 2", "contacts": [contact]},
                ],
            }
        ],
        meeting_preview_sections=[],
        layout=_LAYOUT,
        settings={
            "address_map_provider": "google",
            "address_align": "left",
            "include_bible_study_union_info": 0,
            "meeting_page_mode": "complete_meetings",
        },
        print_order_mode="alphabetical",
        print_order_json="{}",
    )
    line_step = float(_LAYOUT["base_font_size_pt"]) * float(_LAYOUT["line_height"])
    positions = _pdf_text_positions(pdf)
    meeting_one_contact_y = next(y for _x, y, _size, text in positions if text == ", John" and y > 340)
    meeting_two_heading_y = min(y for _x, y, _size, text in positions if text == "Meeting 2" and 330 < y < 370)
    gap = meeting_one_contact_y - meeting_two_heading_y
    assert gap >= line_step * 2.5, (
        f"expected at least two blank separator lines between packed meetings, gap={gap}, line_step={line_step}"
    )


if __name__ == "__main__":
    test_column_gap_scales_to_printable_width()
    test_six_column_table_keeps_single_line_cells()
    test_build_pdf_with_meeting_table()
    test_pdf_uses_effective_font_size_when_override_missing()
    test_unassociated_table_skips_continued_preface_page()
    test_associated_full_page_table_has_no_blank_preface_page()
    test_marked_table_stays_on_one_page()
    test_complete_meetings_mode_packs_small_meetings_on_one_page()
    test_complete_meetings_mode_starts_each_field_on_new_page()
    test_grandchildren_relationship_lines_wrap_within_max_width()
    test_address_book_contact_text_stays_inside_right_margin()
    test_complete_meetings_mode_leaves_separator_gap()
    print("OK: meeting PDF layout smoke tests passed")
