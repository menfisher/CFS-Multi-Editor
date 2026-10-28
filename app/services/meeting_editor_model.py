from __future__ import annotations

from dataclasses import asdict, dataclass, field


MEETING_EDITOR_COLUMN_COUNT = 28


@dataclass
class MeetingCellStyle:
    text_align: str = "left"
    font_scale: float = 1.0
    is_bold: bool = False
    is_italic: bool = False
    is_underlined: bool = False
    is_title: bool = False


@dataclass
class MeetingEditorCell:
    col_start: int
    col_span: int
    text_value: str
    style: MeetingCellStyle = field(default_factory=MeetingCellStyle)


@dataclass
class MeetingEditorRow:
    row_order: int
    row_kind: str
    format_code: str = ""
    label_text: str = ""
    notes: str = ""
    cells: list[MeetingEditorCell] = field(default_factory=list)


@dataclass
class MeetingEditorSection:
    field_name: str
    meeting_name: str
    sort_order: int = 0
    source_legacy_start_row: int | None = None
    source_legacy_end_row: int | None = None
    notes: str = ""
    rows: list[MeetingEditorRow] = field(default_factory=list)
    column_count: int = MEETING_EDITOR_COLUMN_COUNT
    column_pixel_width: int = 16


def legacy_format_code_to_row_kind(format_code: str, cells: list[dict]) -> str:
    code = (format_code or "").lower()
    if len(cells) == 1 and cells[0].get("grid_span", 1) >= 20:
        return "title"
    if cells and any(str(cell.get("value") or "").strip().lower() in {
        "jan", "feb", "mar", "apr", "may", "jun",
        "jul", "aug", "sep", "oct", "nov", "dec",
    } for cell in cells):
        return "month"
    if "ul" in code:
        return "underlined"
    if "umt" in code or "um" in code:
        return "group"
    return "content"


def make_editor_cell(raw_cell: dict) -> MeetingEditorCell:
    style = MeetingCellStyle(
        text_align="center" if raw_cell.get("is_full_width") else "left",
        font_scale=1.0,
        is_bold=bool(raw_cell.get("is_full_width")),
        is_underlined=False,
        is_title=bool(raw_cell.get("is_full_width")),
    )
    return MeetingEditorCell(
        col_start=int(raw_cell.get("grid_column", 1)),
        col_span=int(raw_cell.get("grid_span", 1)),
        text_value=str(raw_cell.get("value") or ""),
        style=style,
    )


def build_editor_section_from_legacy_section(section: dict, sort_order: int = 0) -> MeetingEditorSection:
    rows = []
    legacy_rows = section.get("rows") or []
    start_row = legacy_rows[0]["sheet_row_number"] if legacy_rows else None
    end_row = legacy_rows[-1]["sheet_row_number"] if legacy_rows else None

    for row in legacy_rows:
        raw_cells = row.get("cells") or []
        row_kind = legacy_format_code_to_row_kind(row.get("format_code", ""), raw_cells)
        editor_cells = [make_editor_cell(cell) for cell in raw_cells]
        rows.append(
            MeetingEditorRow(
                row_order=int(row.get("sheet_row_number", 0)),
                row_kind=row_kind,
                format_code=str(row.get("format_code") or ""),
                label_text="",
                notes="",
                cells=editor_cells,
            )
        )

    return MeetingEditorSection(
        field_name=str(section.get("field_name") or ""),
        meeting_name=str(section.get("meeting_name") or ""),
        sort_order=sort_order,
        source_legacy_start_row=start_row,
        source_legacy_end_row=end_row,
        rows=rows,
    )


def editor_section_to_dict(section: MeetingEditorSection) -> dict:
    return asdict(section)
