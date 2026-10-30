from __future__ import annotations

import json
import math
import re
import base64
import binascii
import zlib
from html import unescape
from html.parser import HTMLParser
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import quote

from app.services.address_format import address_display_lines
from app.services.pdf_font_service import (
    add_embedded_pdf_font_set,
    pdf_font_resource_name,
    pdf_text_width,
    resolve_pdf_font_path,
)
from app.services.preset_service import FONT_FAMILY_OPTIONS


def _clean(value: object) -> str:
    return str(value or "").strip()


_PDF_HELVETICA_WIDTHS = {
    " ": 278, "!": 278, '"': 355, "#": 556, "$": 556, "%": 889, "&": 667, "'": 191,
    "(": 333, ")": 333, "*": 389, "+": 584, ",": 278, "-": 333, ".": 278, "/": 278,
    "0": 556, "1": 556, "2": 556, "3": 556, "4": 556, "5": 556, "6": 556, "7": 556, "8": 556, "9": 556,
    ":": 278, ";": 278, "<": 584, "=": 584, ">": 584, "?": 556, "@": 1015,
    "A": 667, "B": 667, "C": 722, "D": 722, "E": 667, "F": 611, "G": 778, "H": 722, "I": 278,
    "J": 500, "K": 667, "L": 556, "M": 833, "N": 722, "O": 778, "P": 667, "Q": 778, "R": 722,
    "S": 667, "T": 611, "U": 722, "V": 667, "W": 944, "X": 667, "Y": 667, "Z": 611,
    "[": 278, "\\": 278, "]": 278, "^": 469, "_": 556, "`": 333,
    "a": 556, "b": 556, "c": 500, "d": 556, "e": 556, "f": 278, "g": 556, "h": 556, "i": 222,
    "j": 222, "k": 500, "l": 222, "m": 833, "n": 556, "o": 556, "p": 556, "q": 556, "r": 333,
    "s": 500, "t": 278, "u": 556, "v": 500, "w": 722, "x": 500, "y": 500, "z": 500,
    "{": 334, "|": 260, "}": 334, "~": 584,
}

_PDF_TIMES_WIDTHS = {
    " ": 250, "!": 333, '"': 408, "#": 500, "$": 500, "%": 833, "&": 778, "'": 180,
    "(": 333, ")": 333, "*": 500, "+": 564, ",": 250, "-": 333, ".": 250, "/": 278,
    "0": 500, "1": 500, "2": 500, "3": 500, "4": 500, "5": 500, "6": 500, "7": 500, "8": 500, "9": 500,
    ":": 333, ";": 333, "<": 564, "=": 564, ">": 564, "?": 444, "@": 921,
    "A": 722, "B": 667, "C": 667, "D": 722, "E": 611, "F": 556, "G": 722, "H": 722, "I": 333,
    "J": 389, "K": 722, "L": 611, "M": 889, "N": 722, "O": 722, "P": 667, "Q": 722, "R": 722,
    "S": 556, "T": 611, "U": 722, "V": 722, "W": 944, "X": 722, "Y": 722, "Z": 611,
    "[": 333, "\\": 278, "]": 333, "^": 469, "_": 500, "`": 333,
    "a": 444, "b": 500, "c": 444, "d": 500, "e": 444, "f": 333, "g": 500, "h": 500, "i": 278,
    "j": 278, "k": 500, "l": 278, "m": 778, "n": 500, "o": 500, "p": 500, "q": 500, "r": 333,
    "s": 389, "t": 278, "u": 500, "v": 500, "w": 722, "x": 500, "y": 500, "z": 444,
    "{": 480, "|": 200, "}": 480, "~": 541,
}

_PDF_HELVETICA_BOLD_WIDTHS = {
    " ": 278, "!": 333, '"': 474, "#": 556, "$": 556, "%": 889, "&": 722, "'": 238,
    "(": 333, ")": 333, "*": 389, "+": 584, ",": 278, "-": 333, ".": 278, "/": 278,
    "0": 556, "1": 556, "2": 556, "3": 556, "4": 556, "5": 556, "6": 556, "7": 556, "8": 556, "9": 556,
    ":": 333, ";": 333, "<": 584, "=": 584, ">": 584, "?": 611, "@": 975,
    "A": 722, "B": 722, "C": 722, "D": 722, "E": 667, "F": 611, "G": 778, "H": 722, "I": 278,
    "J": 556, "K": 722, "L": 611, "M": 833, "N": 722, "O": 778, "P": 667, "Q": 778, "R": 722,
    "S": 667, "T": 611, "U": 722, "V": 667, "W": 944, "X": 667, "Y": 667, "Z": 611,
    "[": 333, "\\": 278, "]": 333, "^": 584, "_": 556, "`": 333,
    "a": 556, "b": 611, "c": 556, "d": 611, "e": 556, "f": 333, "g": 611, "h": 611, "i": 278,
    "j": 278, "k": 556, "l": 278, "m": 889, "n": 611, "o": 611, "p": 611, "q": 611, "r": 389,
    "s": 556, "t": 333, "u": 611, "v": 556, "w": 778, "x": 556, "y": 556, "z": 500,
    "{": 389, "|": 280, "}": 389, "~": 584,
}

_PDF_TIMES_BOLD_WIDTHS = {
    " ": 250, "!": 333, '"': 555, "#": 500, "$": 500, "%": 1000, "&": 833, "'": 278,
    "(": 333, ")": 333, "*": 500, "+": 570, ",": 250, "-": 333, ".": 250, "/": 278,
    "0": 500, "1": 500, "2": 500, "3": 500, "4": 500, "5": 500, "6": 500, "7": 500, "8": 500, "9": 500,
    ":": 333, ";": 333, "<": 570, "=": 570, ">": 570, "?": 500, "@": 930,
    "A": 722, "B": 667, "C": 722, "D": 722, "E": 667, "F": 611, "G": 778, "H": 778, "I": 389,
    "J": 500, "K": 778, "L": 667, "M": 944, "N": 722, "O": 778, "P": 611, "Q": 778, "R": 722,
    "S": 556, "T": 667, "U": 722, "V": 722, "W": 1000, "X": 722, "Y": 722, "Z": 667,
    "[": 333, "\\": 278, "]": 333, "^": 581, "_": 500, "`": 333,
    "a": 500, "b": 556, "c": 444, "d": 556, "e": 444, "f": 333, "g": 500, "h": 556, "i": 278,
    "j": 333, "k": 556, "l": 278, "m": 833, "n": 556, "o": 500, "p": 556, "q": 556, "r": 444,
    "s": 389, "t": 333, "u": 556, "v": 500, "w": 722, "x": 500, "y": 500, "z": 444,
    "{": 394, "|": 220, "}": 394, "~": 520,
}

_PDF_FONT_WIDTHS = {
    "helvetica": _PDF_HELVETICA_WIDTHS,
    "times": _PDF_TIMES_WIDTHS,
}
_PDF_BOLD_FONT_WIDTHS = {
    "helvetica": _PDF_HELVETICA_BOLD_WIDTHS,
    "times": _PDF_TIMES_BOLD_WIDTHS,
}
_PDF_COURIER_WIDTH = 600


def _normalize_pdf_font_family(font_family: object) -> str:
    cleaned = _clean(font_family) or "Arial"
    return cleaned if cleaned in FONT_FAMILY_OPTIONS else "Arial"


def _pdf_font_group(font_family: str) -> str:
    name = _clean(font_family).lower()
    if name in {"courier new", "courier"}:
        return "courier"
    if name in {"times new roman", "georgia", "times"}:
        return "times"
    return "helvetica"


def _pdf_base_font_names(font_family: str) -> tuple[str, str]:
    group = _pdf_font_group(font_family)
    if group == "courier":
        return "Courier", "Courier-Bold"
    if group == "times":
        return "Times-Roman", "Times-Bold"
    return "Helvetica", "Helvetica-Bold"


def _fitz_builtin_font_names_for(font_family: str) -> dict[str, str]:
    group = _pdf_font_group(font_family)
    if group == "times":
        return {"regular": "tiro", "bold": "tibo", "italic": "titi"}
    if group == "courier":
        return {"regular": "cour", "bold": "cobo", "italic": "coit"}
    return {"regular": "helv", "bold": "hebo", "italic": "heit"}


def _pdf_italic_font_name(font_family: str) -> str:
    group = _pdf_font_group(font_family)
    if group == "courier":
        return "Courier-Oblique"
    if group == "times":
        return "Times-Italic"
    return "Helvetica-Oblique"


def _normalize_pdf_text(value: object) -> str:
    text = str(value or "")
    for source, replacement in (
        ("\u2018", "'"),
        ("\u2019", "'"),
        ("\u201a", "'"),
        ("\u201b", "'"),
        ("\u2032", "'"),
        ("\u00b4", "'"),
        ("\u201c", '"'),
        ("\u201d", '"'),
        ("\u2013", "-"),
        ("\u2014", "-"),
        ("\u2026", "..."),
        ("\u00a0", " "),
    ):
        text = text.replace(source, replacement)
    # WinAnsiEncoding (PDF) matches Latin-1 for 0xA0-0xFF; keep accents like é, í.
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _pdf_escape(value: object) -> str:
    return _normalize_pdf_text(value).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _safe_filename_part(value: object) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", _clean(value))
    return text.strip("_") or "Address_Book"


def address_book_pdf_filename(book_title: str) -> str:
    return f"{_safe_filename_part(book_title)}_{datetime.now().strftime('%Y-%m-%d')}.pdf"


def _sort_key(value: object) -> str:
    return _clean(value).lower()


def _match_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _clean(value).lower()).strip()


_FIELD_SEPARATOR_TITLE_INSET_PT = 0.375 * 72.0
_PAGE_FRAME_OUTER_WIDTH_PT = 1.15
_PAGE_FRAME_INNER_WIDTH_PT = 0.42
_PAGE_FRAME_DOUBLE_GAP_PT = 2.35
_PAGE_FRAME_SIDE_INSET_PT = 5.6
_PAGE_FRAME_BOTTOM_INSET_PT = 16.0


def _meeting_heading(meeting_name: str, home_last_name: object = "") -> str:
    name = _clean(meeting_name)
    if not name:
        return name
    paren_match = re.match(r"^(.+?)\s+\(([^)]+)\)\s*$", name)
    if paren_match:
        base = paren_match.group(1).strip()
        surname = paren_match.group(2).strip().upper()
        return f"{base} ({surname})"
    home_name = _clean(home_last_name).upper()
    return f"{name} ({home_name})" if home_name else name


def _meeting_base_name(meeting_name: str, home_last_name: object = "") -> str:
    heading = _meeting_heading(meeting_name, home_last_name)
    paren_match = re.match(r"^(.+?)\s+\(([^)]+)\)\s*$", heading)
    if paren_match:
        return paren_match.group(1).strip()
    return heading


def _contact_flag_value(contact: dict) -> str:
    return _clean(contact.get("mtg_home_elder_flag"))


def _person_name_case(value: object) -> str:
    text = _clean(value)
    if not text:
        return ""
    letters = re.sub(r"[^A-Za-z]", "", text)
    if letters and letters == letters.upper():
        return text.title()
    return text


def _contact_last_name(contact: dict) -> str:
    family = _clean(contact.get("family_name"))
    if family:
        return family.upper()
    name = _index_contact_name(contact.get("name"))
    if "," in name:
        return name.split(",", 1)[0].strip().upper()
    return name.upper()


def _separator_elder_contact(meeting: dict) -> dict | None:
    contacts = [item for item in (meeting.get("contacts") or []) if isinstance(item, dict)]
    for contact in contacts:
        if _contact_flag_value(contact).startswith("2"):
            return contact
    for contact in contacts:
        if _contact_flag_value(contact).startswith("1"):
            return contact
    heading = _meeting_heading(_clean(meeting.get("name")), meeting.get("home_last_name"))
    paren_match = re.match(r"^(.+?)\s+\(([^)]+)\)\s*$", heading)
    surname = paren_match.group(2).strip().upper() if paren_match else _clean(meeting.get("home_last_name")).upper()
    if not surname:
        return None
    for contact in contacts:
        if _contact_last_name(contact) == surname:
            return contact
    return None


def _separator_elder_name(contact: dict | None) -> str:
    if not contact:
        return ""
    given = _person_name_case(contact.get("given_name"))
    family = _person_name_case(contact.get("family_name"))
    if given and family:
        return f"{given} {family}"
    if given:
        return given
    if family:
        return family
    raw = _index_contact_name(contact.get("name"))
    if "," in raw:
        last, rest = raw.split(",", 1)
        first = rest.split("&", 1)[0].strip()
        first = re.split(r"[;,]", first, 1)[0].strip()
        return " ".join(part for part in (_person_name_case(first), _person_name_case(last)) if part)
    return _person_name_case(raw.split("&", 1)[0].strip())


def _phone_entry_type(phone: object) -> str:
    if not isinstance(phone, dict):
        return ""
    return _clean(phone.get("type") or phone.get("phone_type"))


def _phone_entry_value(phone: object) -> str:
    if isinstance(phone, dict):
        return _clean(phone.get("value") or phone.get("phone_value"))
    return _clean(phone)


def _normalize_phone_kind(phone_type: object) -> str:
    return re.sub(r"[^a-z]+", " ", _clean(phone_type).lower()).strip()


def _phone_is_mobile(phone: object) -> bool:
    kind = _normalize_phone_kind(_phone_entry_type(phone))
    if kind in {"mobile", "cell", "cellular", "cell phone"}:
        return True
    if not isinstance(phone, dict):
        return False
    return _clean(phone.get("code")).lower() == "c"


def _phone_is_home(phone: object) -> bool:
    kind = _normalize_phone_kind(_phone_entry_type(phone))
    return kind in {"home", "house"}


def _phone_belongs_to_elder(phone: object, contact: dict) -> bool:
    if not isinstance(phone, dict):
        return True
    owner = _clean(phone.get("owner_given_name")).lower()
    given = _clean(contact.get("given_name")).lower()
    if not owner or not given:
        return True
    return owner == given or given.startswith(owner) or owner.startswith(given)


def _format_separator_phone_value(value: object) -> str:
    text = _clean(value)
    digits = _phone_digits(text)
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return text


def _separator_phone_text(contact: dict | None) -> str:
    if not contact:
        return ""
    phones = [item for item in (contact.get("phones") or []) if _phone_entry_value(item)]
    if not phones:
        return ""
    elder_mobiles = [item for item in phones if _phone_is_mobile(item) and _phone_belongs_to_elder(item, contact)]
    mobiles = [item for item in phones if _phone_is_mobile(item)]
    homes = [item for item in phones if _phone_is_home(item)]
    chosen = (elder_mobiles or mobiles or homes or phones)[0]
    return _format_separator_phone_value(_phone_entry_value(chosen))


def _append_dot_leaders(
    page: "_PdfPage",
    x_start: float,
    x_end: float,
    y: float,
    size: float,
    font_family: str,
) -> None:
    space = _text_width(" ", size, font_family)
    left = x_start + space
    right = x_end - space
    if right <= left:
        return
    dot_width = max(1.0, _text_width(".", size, font_family))
    count = int((right - left) / dot_width)
    if count <= 0:
        return
    page.lines.append(_PdfLine("." * count, left, y, size))


def _order_payload(raw_json: object) -> dict[str, Any]:
    try:
        payload = json.loads(str(raw_json or "") or "{}")
    except json.JSONDecodeError:
        return {"fields": []}
    return payload if isinstance(payload, dict) else {"fields": []}


def _is_field_end_list_meeting(meeting: dict) -> bool:
    return bool(meeting.get("is_field_end_list"))


def _field_end_list_sort_key(meeting: dict) -> tuple:
    try:
        order = int(meeting.get("field_end_list_order") or 0)
    except (TypeError, ValueError):
        order = 0
    return (order, _sort_key(meeting.get("name")))


def _order_field_meetings(meetings: list[dict], sort_key) -> list[dict]:
    regular = [meeting for meeting in meetings if not _is_field_end_list_meeting(meeting)]
    field_end = [meeting for meeting in meetings if _is_field_end_list_meeting(meeting)]
    return sorted(regular, key=sort_key) + sorted(field_end, key=_field_end_list_sort_key)


def order_address_book_fields(fields: list[dict], mode: str, raw_order_json: object) -> list[dict]:
    copied_fields = [
        {
            **field,
            "meetings": [dict(meeting) for meeting in field.get("meetings", [])],
        }
        for field in fields
    ]
    if mode != "custom":
        for field in copied_fields:
            field["meetings"] = _order_field_meetings(
                field.get("meetings", []),
                lambda item: _sort_key(item.get("name")),
            )
        return sorted(copied_fields, key=lambda item: _sort_key(item.get("name")))

    payload = _order_payload(raw_order_json)
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
        field_orders[field_name] = field_order
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
            meeting_orders[(field_name, meeting_name)] = meeting_order

    for field in copied_fields:
        field_name = _clean(field.get("name"))
        field["meetings"] = _order_field_meetings(
            field.get("meetings", []),
            lambda item, field_name=field_name: (
                meeting_orders.get((field_name, _clean(item.get("name"))), 1_000_000),
                _sort_key(item.get("name")),
            ),
        )
    return sorted(
        copied_fields,
        key=lambda item: (
            field_orders.get(_clean(item.get("name")), 1_000_000),
            _sort_key(item.get("name")),
        ),
    )


@dataclass
class _PdfLine:
    text: str
    x: float
    y: float
    size: float
    bold: bool = False
    italic: bool = False
    uri: str = ""
    width: float = 0.0
    link_page_index: int | None = None
    link_page_y: float | None = None
    color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    effect: str = ""
    rotate_180: bool = False
    outer_header: bool = False


def _contrast_pdf_color(color: tuple[float, float, float]) -> tuple[float, float, float]:
    luminance = (0.299 * color[0]) + (0.587 * color[1]) + (0.114 * color[2])
    return (0.0, 0.0, 0.0) if luminance > 0.42 else (1.0, 1.0, 1.0)


def _offset_pdf_color(
    color: tuple[float, float, float],
    amount: float,
) -> tuple[float, float, float]:
    return tuple(min(1.0, max(0.0, channel + amount)) for channel in color)


def _pdf_line_effect_draws(line: _PdfLine) -> list[tuple[float, float, tuple[float, float, float]]]:
    effect = _clean(line.effect).lower()
    if not effect or effect == "none":
        return [(0.0, 0.0, line.color)]

    step = max(0.45, min(2.2, line.size * 0.055))
    contrast = _contrast_pdf_color(line.color)
    dark = (0.0, 0.0, 0.0)
    light = (1.0, 1.0, 1.0)
    shadow = tuple(channel * 0.22 for channel in line.color)

    if effect == "outline":
        return [
            (-step, 0.0, contrast),
            (step, 0.0, contrast),
            (0.0, -step, contrast),
            (0.0, step, contrast),
            (-step, -step, contrast),
            (step, -step, contrast),
            (-step, step, contrast),
            (step, step, contrast),
            (0.0, 0.0, line.color),
        ]
    if effect == "shadow":
        return [(step * 1.5, -step * 1.5, shadow), (0.0, 0.0, line.color)]
    if effect == "embossed":
        return [(-step, step, light), (step, -step, dark), (0.0, 0.0, line.color)]
    if effect == "engraved":
        return [
            (-step, step, dark),
            (step, -step, light),
            (0.0, 0.0, _offset_pdf_color(line.color, -0.08)),
        ]
    if effect == "bevel":
        return [
            (-step * 0.8, step * 0.8, light),
            (step * 0.9, -step * 0.9, dark),
            (step * 1.6, -step * 1.6, shadow),
            (0.0, 0.0, line.color),
        ]
    return [(0.0, 0.0, line.color)]


def _pdf_line_content(line: _PdfLine, font_family: str = "Arial") -> list[str]:
    if line.bold:
        font_ref = "/F2"
    elif line.italic:
        font_ref = "/F3"
    else:
        font_ref = "/F1"
    line_width = line.width or _text_width(line.text, line.size, font_family)

    def draw(x_offset: float, y_offset: float, color: tuple[float, float, float]) -> str:
        r, g, b = color
        if line.rotate_180:
            return (
                f"{r:.3f} {g:.3f} {b:.3f} rg BT {font_ref} {line.size:.2f} Tf "
                f"-1 0 0 -1 {line.x + line_width + x_offset:.2f} {line.y + line.size + y_offset:.2f} Tm "
                f"({_pdf_escape(line.text)}) Tj ET"
            )
        return (
            f"{r:.3f} {g:.3f} {b:.3f} rg BT {font_ref} {line.size:.2f} Tf "
            f"1 0 0 1 {line.x + x_offset:.2f} {line.y + y_offset:.2f} Tm "
            f"({_pdf_escape(line.text)}) Tj ET"
        )

    return [draw(x_offset, y_offset, color) for x_offset, y_offset, color in _pdf_line_effect_draws(line)]


@dataclass
class _PdfRule:
    x1: float
    y1: float
    x2: float
    y2: float
    width: float = 0.45


@dataclass
class _PdfImage:
    data: bytes
    pixel_width: int
    pixel_height: int
    x: float
    y: float
    width: float
    height: float
    rotate_180: bool = False
    filter_name: str = "DCTDecode"


@dataclass
class _PdfPage:
    width: float
    height: float
    lines: list[_PdfLine] = field(default_factory=list)
    rules: list[_PdfRule] = field(default_factory=list)
    images: list[_PdfImage] = field(default_factory=list)


@dataclass
class _TocEntry:
    field_name: str
    meeting_heading: str
    page: int
    is_heading_only: bool = False
    toc_page_index: int = 0
    heading_lines: list[_PdfLine] = field(default_factory=list)


@dataclass
class _IndexEntry:
    name: str
    page: int
    target_y: float = 0.0


def _fitz_insert_page_image(page, image: _PdfImage, page_h: float, fitz) -> None:
    rect = fitz.Rect(
        image.x,
        page_h - image.y - image.height,
        image.x + image.width,
        page_h - image.y,
    )
    rotate = 180 if image.rotate_180 else 0
    data = bytes(image.data or b"")
    if not data:
        return

    def insert_pixmap(rgb: bytes) -> None:
        pixmap = fitz.Pixmap(
            fitz.csRGB,
            max(1, int(image.pixel_width)),
            max(1, int(image.pixel_height)),
            rgb,
            0,
        )
        page.insert_image(rect, pixmap=pixmap, rotate=rotate)

    if image.filter_name == "FlateDecode":
        insert_pixmap(zlib.decompress(data))
        return
    if data[:2] == b"\xff\xd8" or data[:8] == b"\x89PNG\r\n\x1a\n":
        page.insert_image(rect, stream=data, rotate=rotate)
        return
    try:
        page.insert_image(rect, stream=data, rotate=rotate)
    except Exception:
        if image.filter_name == "FlateDecode" or len(data) == int(image.pixel_width) * int(image.pixel_height) * 3:
            insert_pixmap(data)
        else:
            raise


class _PdfWriter:
    def __init__(self, width: float, height: float, *, font_family: str = "Arial", title: str = "") -> None:
        self.width = width
        self.height = height
        self.font_family = _normalize_pdf_font_family(font_family)
        self.title = str(title or "").strip()
        self.pages: list[_PdfPage] = []

    def add_page(self) -> _PdfPage:
        page = _PdfPage(self.width, self.height)
        self.pages.append(page)
        return page

    def bytes(self) -> bytes:
        rendered = self._fitz_bytes()
        if rendered:
            return rendered
        return self._legacy_bytes()

    def _font_file_paths(self) -> dict[str, str]:
        regular = resolve_pdf_font_path(self.font_family, "regular")
        if not regular:
            return {}
        return {
            "regular": regular,
            "bold": resolve_pdf_font_path(self.font_family, "bold") or regular,
            "italic": resolve_pdf_font_path(self.font_family, "italic") or regular,
        }

    def _fitz_builtin_font_names(self) -> dict[str, str]:
        return _fitz_builtin_font_names_for(self.font_family)

    def _fitz_bytes(self) -> bytes | None:
        try:
            from app.services.pymupdf_runtime import ensure_pymupdf

            ensure_pymupdf()
            import fitz
        except Exception:
            return None

        font_files = self._font_file_paths()
        builtin = self._fitz_builtin_font_names()
        document = fitz.open()
        pending_links: list[tuple[int, dict]] = []
        try:
            for page_data in self.pages:
                page = document.new_page(width=page_data.width, height=page_data.height)
                page_h = page_data.height
                font_names = dict(builtin)
                if font_files:
                    for style, path in font_files.items():
                        fontname = pdf_font_resource_name(self.font_family, style)
                        page.insert_font(
                            fontname=fontname,
                            fontfile=path,
                            set_simple=True,
                        )
                        font_names[style] = fontname
                for image in page_data.images:
                    _fitz_insert_page_image(page, image, page_h, fitz)
                for rule in page_data.rules:
                    page.draw_line(
                        fitz.Point(rule.x1, page_h - rule.y1),
                        fitz.Point(rule.x2, page_h - rule.y2),
                        width=rule.width,
                        color=(0, 0, 0),
                    )
                for line in page_data.lines:
                    if not str(line.text or ""):
                        continue
                    style = "bold" if line.bold else "italic" if line.italic else "regular"
                    fontname = font_names[style]
                    line_width = line.width or _text_width(line.text, line.size, self.font_family)
                    for x_offset, y_offset, color in _pdf_line_effect_draws(line):
                        if line.rotate_180:
                            point = fitz.Point(
                                line.x + line_width + x_offset,
                                page_h - (line.y + line.size + y_offset),
                            )
                            rotate = 180
                        else:
                            point = fitz.Point(line.x + x_offset, page_h - (line.y + y_offset))
                            rotate = 0
                        page.insert_text(
                            point,
                            line.text,
                            fontsize=line.size,
                            fontname=fontname,
                            color=color,
                            rotate=rotate,
                        )
                    if not line.uri and line.link_page_index is None:
                        continue
                    rect = fitz.Rect(
                        line.x,
                        page_h - (line.y + line.size + 1),
                        line.x + line_width,
                        page_h - (line.y - 1),
                    )
                    link = {
                        "from": rect,
                    }
                    if line.link_page_index is not None:
                        target_y = page_h - line.link_page_y if line.link_page_y is not None else 0
                        link.update(
                            {
                                "kind": fitz.LINK_GOTO,
                                "page": max(0, min(len(self.pages) - 1, line.link_page_index)),
                                "to": fitz.Point(0, target_y),
                            }
                        )
                    else:
                        link.update({"kind": fitz.LINK_URI, "uri": line.uri})
                    pending_links.append((len(document) - 1, link))
            for page_index, link in pending_links:
                document[page_index].insert_link(link)
            _fitz_sync_embedded_font_names(document)
            _fitz_add_winansi_tounicode(document)
            _fitz_mark_pdf_editable(document, self.title)
            return document.tobytes(
                deflate=True,
                garbage=3,
                clean=False,
                encryption=fitz.PDF_ENCRYPT_NONE,
            )
        finally:
            document.close()

    def _legacy_bytes(self) -> bytes:
        objects: list[bytes] = []

        def add_object(raw: str | bytes) -> int:
            objects.append(raw.encode("latin-1") if isinstance(raw, str) else raw)
            return len(objects)

        embedded_fonts = add_embedded_pdf_font_set(add_object, self.font_family)
        if embedded_fonts:
            font_regular, font_bold, font_italic = embedded_fonts
        else:
            font_regular_name, font_bold_name = _pdf_base_font_names(self.font_family)
            font_italic_name = _pdf_italic_font_name(self.font_family)
            font_regular = add_object(
                f"<< /Type /Font /Subtype /Type1 /BaseFont /{font_regular_name} /Encoding /WinAnsiEncoding >>"
            )
            font_bold = add_object(
                f"<< /Type /Font /Subtype /Type1 /BaseFont /{font_bold_name} /Encoding /WinAnsiEncoding >>"
            )
            font_italic = add_object(
                f"<< /Type /Font /Subtype /Type1 /BaseFont /{font_italic_name} /Encoding /WinAnsiEncoding >>"
            )
        page_refs: list[int] = []
        pending_pages: list[tuple[_PdfPage, int, dict[str, int]]] = []
        content_refs: set[int] = set()

        for page in self.pages:
            xobject_refs: dict[str, int] = {}
            for index, image in enumerate(page.images, start=1):
                image_name = f"Im{index}"
                image_filter = "FlateDecode" if image.filter_name == "FlateDecode" else "DCTDecode"
                image_ref = add_object(
                    (
                        f"<< /Type /XObject /Subtype /Image /Width {max(1, image.pixel_width)} "
                        f"/Height {max(1, image.pixel_height)} /ColorSpace /DeviceRGB "
                        f"/BitsPerComponent 8 /Filter /{image_filter} /Length {len(image.data)} >>\nstream\n"
                    ).encode("latin-1")
                    + image.data
                    + b"\nendstream"
                )
                xobject_refs[image_name] = image_ref
            content_parts = []
            for index, image in enumerate(page.images, start=1):
                if image.rotate_180:
                    content_parts.append(
                        f"q {-image.width:.2f} 0 0 {-image.height:.2f} "
                        f"{image.x + image.width:.2f} {image.y + image.height:.2f} cm /Im{index} Do Q"
                    )
                else:
                    content_parts.append(
                        f"q {image.width:.2f} 0 0 {image.height:.2f} {image.x:.2f} {image.y:.2f} cm /Im{index} Do Q"
                    )
            for rule in page.rules:
                content_parts.append(
                    f"q {rule.width:.2f} w {rule.x1:.2f} {rule.y1:.2f} m {rule.x2:.2f} {rule.y2:.2f} l S Q"
                )
            for line in page.lines:
                content_parts.extend(_pdf_line_content(line, self.font_family))
            content_ref = add_object("\n".join(content_parts).encode("latin-1", errors="replace"))
            content_refs.add(content_ref)
            page_ref = add_object(b"")
            page_refs.append(page_ref)
            pending_pages.append((page, content_ref, xobject_refs))

        pages_ref = add_object(b"")
        info_title = _pdf_escape(self.title or "Address Book")
        info_ref = add_object(
            f"<< /Title ({info_title}) /Creator (ContactsFreeShare) /Producer (ContactsFreeShare) >>"
        )
        catalog_ref = add_object(f"<< /Type /Catalog /Pages {pages_ref} 0 R >>")

        for page_ref, (page, content_ref, xobject_refs) in zip(page_refs, pending_pages):
            annots: list[int] = []
            for line in page.lines:
                if not line.uri and line.link_page_index is None:
                    continue
                text_width = line.width or _text_width(line.text, line.size, self.font_family)
                rect = f"/Rect [{line.x:.2f} {line.y - 1:.2f} {line.x + text_width:.2f} {line.y + line.size + 1:.2f}] "
                if line.link_page_index is not None:
                    target_index = max(0, min(len(page_refs) - 1, line.link_page_index))
                    target_ref = page_refs[target_index]
                    destination = (
                        f"[{target_ref} 0 R /XYZ null {line.link_page_y:.2f} null]"
                        if line.link_page_y is not None
                        else f"[{target_ref} 0 R /Fit]"
                    )
                    annot = add_object(
                        "<< /Type /Annot /Subtype /Link "
                        f"{rect}/Border [0 0 0] /A << /S /GoTo /D {destination} >> >>"
                    )
                else:
                    # Include /Type /Action and keep hierarchical URIs (scheme://…) so
                    # macOS Preview hands the link to Launch Services instead of treating
                    # it as a relative file path ("File sms:… not found").
                    annot = add_object(
                        "<< /Type /Annot /Subtype /Link "
                        f"{rect}/Border [0 0 0] "
                        f"/A << /Type /Action /S /URI /URI ({_pdf_escape(line.uri)}) >> >>"
                    )
                annots.append(annot)
            annot_refs = " ".join(f"{item} 0 R" for item in annots)
            xobject_resource = ""
            if xobject_refs:
                xobject_resource = " /XObject << " + " ".join(
                    f"/{name} {ref} 0 R" for name, ref in xobject_refs.items()
                ) + " >>"
            objects[page_ref - 1] = (
                "<< /Type /Page "
                f"/Parent {pages_ref} 0 R "
                f"/MediaBox [0 0 {page.width:.2f} {page.height:.2f}] "
                f"/Resources << /Font << /F1 {font_regular} 0 R /F2 {font_bold} 0 R /F3 {font_italic} 0 R >>{xobject_resource} >> "
                f"/Contents {content_ref} 0 R "
                f"/Annots [{annot_refs}] >>"
            ).encode("latin-1")

        objects[pages_ref - 1] = (
            f"<< /Type /Pages /Kids [{' '.join(f'{ref} 0 R' for ref in page_refs)}] /Count {len(page_refs)} >>"
        ).encode("latin-1")

        chunks = [b"%PDF-1.4\n"]
        offsets = [0]
        for index, obj in enumerate(objects, start=1):
            offsets.append(sum(len(chunk) for chunk in chunks))
            chunks.append(f"{index} 0 obj\n".encode("latin-1"))
            if index in content_refs:
                chunks.append(f"<< /Length {len(obj)} >>\nstream\n".encode("latin-1"))
                chunks.append(obj)
                chunks.append(b"\nendstream\n")
            else:
                chunks.append(obj)
                chunks.append(b"\n")
            chunks.append(b"endobj\n")
        xref_start = sum(len(chunk) for chunk in chunks)
        chunks.append(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("latin-1"))
        for offset in offsets[1:]:
            chunks.append(f"{offset:010d} 00000 n \n".encode("latin-1"))
        chunks.append(
            f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_ref} 0 R /Info {info_ref} 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode("latin-1")
        )
        return b"".join(chunks)


def _fitz_sync_embedded_font_names(document) -> None:
    """Point each font descriptor at the PostScript BaseFont name.

    MuPDF stores the TrueType full name (`Verdana Bold`) in /FontName while
    /BaseFont is the PostScript name (`Verdana-Bold`). Preview and Chrome then
    ignore the embedded file and substitute Arial.
    """
    for xref in range(1, document.xref_length()):
        try:
            font_type = document.xref_get_key(xref, "Type")
            subtype = document.xref_get_key(xref, "Subtype")
        except Exception:
            continue
        if font_type != ("name", "/Font") or subtype[1] not in {"/TrueType", "/Type1"}:
            continue
        try:
            base = document.xref_get_key(xref, "BaseFont")
            descriptor = document.xref_get_key(xref, "FontDescriptor")
        except Exception:
            continue
        if base[0] != "name" or descriptor[0] != "xref":
            continue
        try:
            descriptor_xref = int(str(descriptor[1]).split()[0])
            document.xref_set_key(descriptor_xref, "FontName", base[1])
        except Exception:
            continue


_WINANSI_EXTRA_UNICODES = {
    0x80: 0x20AC,
    0x82: 0x201A,
    0x83: 0x0192,
    0x84: 0x201E,
    0x85: 0x2026,
    0x86: 0x2020,
    0x87: 0x2021,
    0x88: 0x02C6,
    0x89: 0x2030,
    0x8A: 0x0160,
    0x8B: 0x2039,
    0x8C: 0x0152,
    0x8E: 0x017D,
    0x91: 0x2018,
    0x92: 0x2019,
    0x93: 0x201C,
    0x94: 0x201D,
    0x95: 0x2022,
    0x96: 0x2013,
    0x97: 0x2014,
    0x98: 0x02DC,
    0x99: 0x2122,
    0x9A: 0x0161,
    0x9B: 0x203A,
    0x9C: 0x0153,
    0x9E: 0x017E,
    0x9F: 0x0178,
}


def _winansi_tounicode_stream() -> bytes:
    mappings: list[str] = []
    for code in range(32, 256):
        unicode_value = _WINANSI_EXTRA_UNICODES.get(code)
        if unicode_value is None:
            if code == 0x7F or 0x80 <= code <= 0x9F:
                continue
            unicode_value = code
        mappings.append(f"<{code:02X}> <{unicode_value:04X}>")
    lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /WinAnsi-UCS def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<00> <FF>",
        "endcodespacerange",
    ]
    for start in range(0, len(mappings), 100):
        chunk = mappings[start : start + 100]
        lines.append(f"{len(chunk)} beginbfchar")
        lines.extend(chunk)
        lines.append("endbfchar")
    lines.extend(
        [
            "endcmap",
            "CMapName currentdict /CMap defineresource pop",
            "end",
            "end",
        ]
    )
    return "\n".join(lines).encode("latin-1")


def _fitz_add_winansi_tounicode(document) -> None:
    """Give simple WinAnsi fonts a ToUnicode map so Acrobat can edit text and links."""
    cmap_bytes = _winansi_tounicode_stream()
    cmap_xref: int | None = None
    for xref in range(1, document.xref_length()):
        try:
            font_type = document.xref_get_key(xref, "Type")
            subtype = document.xref_get_key(xref, "Subtype")
            encoding = document.xref_get_key(xref, "Encoding")
            tounicode = document.xref_get_key(xref, "ToUnicode")
        except Exception:
            continue
        if font_type != ("name", "/Font") or subtype[1] not in {"/TrueType", "/Type1"}:
            continue
        if encoding != ("name", "/WinAnsiEncoding"):
            continue
        if tounicode and tounicode[0] == "xref":
            continue
        if cmap_xref is None:
            cmap_xref = document.get_new_xref()
            document.update_object(cmap_xref, "<<>>")
            document.update_stream(cmap_xref, cmap_bytes)
        try:
            document.xref_set_key(xref, "ToUnicode", f"{cmap_xref} 0 R")
        except Exception:
            continue


def _fitz_mark_pdf_editable(document, title: str) -> None:
    """Label the file as a normal, unlocked PDF for full editors such as Acrobat."""
    document.set_metadata(
        {
            "title": str(title or "").strip() or "Address Book",
            "creator": "ContactsFreeShare",
            "producer": "ContactsFreeShare",
        }
    )


PHONE_NUMBER_SLOT = "000-000-0000"
PHONE_CODE_SLOT = "MM"


def _phone_stack_to_name_width(font_size: float, tw) -> float:
    """Width from the number start to the shared family-name column.

    Number + one space + a 2-letter slot sized for wide codes such as Ma + one space.
    """
    space = tw(" ", font_size)
    return tw(PHONE_NUMBER_SLOT, font_size) + space + tw(PHONE_CODE_SLOT, font_size) + space


def _contact_text_columns(
    margin_left: float,
    page_width: float,
    margin_right: float,
    font_size: float,
    tw,
) -> tuple[float, float, float, float, float]:
    """Shared name column one space after a 2-character phone-code slot."""
    space = tw(" ", font_size)
    phone_x = margin_left
    main_text_x = phone_x + _phone_stack_to_name_width(font_size, tw)
    phone_code_x = phone_x + tw(PHONE_NUMBER_SLOT, font_size) + space
    continuation_x = main_text_x + space
    name_max_width = max(24.0, page_width - margin_right - continuation_x)
    return phone_x, phone_code_x, main_text_x, continuation_x, name_max_width


def _text_width(text: str, size: float, font_family: str = "Arial", *, bold: bool = False) -> float:
    normalized = _normalize_pdf_text(text)
    measured = pdf_text_width(normalized, size, font_family, bold=bold)
    if measured is not None:
        return measured
    group = _pdf_font_group(font_family)
    if group == "courier":
        return len(normalized) * _PDF_COURIER_WIDTH * size / 1000.0
    table = _PDF_BOLD_FONT_WIDTHS if bold else _PDF_FONT_WIDTHS
    widths = table[group]
    fallback = 444 if group == "times" else 556
    return sum(widths.get(char, fallback) for char in normalized) * size / 1000.0


_MEETING_GRID_COLUMNS = 28
_DEFAULT_MEETING_TRIM_WIDTH_IN = 3.5
_DEFAULT_MEETING_MARGIN_IN = 0.14


def _default_meeting_content_width_pt() -> float:
    page_width = max(72.0, _DEFAULT_MEETING_TRIM_WIDTH_IN * 72.0)
    margin = max(6.0, _DEFAULT_MEETING_MARGIN_IN * 72.0)
    return page_width - (margin * 2)


def _meeting_column_gap_pt(
    column_gap_px: object,
    *,
    grid_width_pt: float = 0.0,
    preview_printable_width_px: object = None,
) -> float:
    """Convert editor canvas gap px to PDF points for the target printable width."""
    try:
        px = max(0.0, float(column_gap_px or 0))
    except (TypeError, ValueError):
        px = 0.0
    try:
        preview_width_px = max(0.0, float(preview_printable_width_px or 0))
    except (TypeError, ValueError):
        preview_width_px = 0.0
    if preview_width_px > 0 and grid_width_pt > 0:
        return px * (grid_width_pt / preview_width_px)
    return px * 0.75


def _meeting_grid_cell_bounds(
    col_start: object,
    col_span: object,
    grid_width: float,
    *,
    column_gap_pt: float = 0.0,
    column_count: int = _MEETING_GRID_COLUMNS,
) -> tuple[float, float]:
    count = max(1, int(column_count or _MEETING_GRID_COLUMNS))
    start = max(1, min(count, int(col_start or 1)))
    span = max(1, min(count - start + 1, int(col_span or 1)))
    gap = max(0.0, column_gap_pt)
    gap_total = gap * max(0, count - 1)
    track_width = (grid_width - gap_total) / count if grid_width > gap_total else grid_width / count
    x = (start - 1) * (track_width + gap)
    width = span * track_width + max(0, span - 1) * gap
    return x, max(0.0, width)


def _short_date() -> str:
    return datetime.now().strftime("%m/%d/%Y")


def _map_url(provider: str, coordinates: str, address_text: str = "") -> str:
    value = re.sub(r"\s*,\s*", ",", _clean(coordinates))
    if not value:
        value = re.sub(r"\s*\n\s*", ", ", _clean(address_text))
    if not value:
        return ""
    host = "maps.apple.com" if provider == "apple" else "maps.google.com"
    return f"http://{host}/?q={quote(value, safe=',')}"


def _phone_digits(value: object) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _plain_text(value: object) -> str:
    return _normalize_pdf_text(_clean(unescape(re.sub(r"<[^>]+>", " ", str(value or "")))))


class _InlineHtmlStyleParser(HTMLParser):
    _STYLE_TAGS = {
        "b": "bold",
        "strong": "bold",
        "i": "italic",
        "em": "italic",
        "u": "underline",
    }

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[list[dict[str, Any]]] = [[]]
        self._stack: list[set[str]] = []

    def _active(self) -> set[str]:
        active: set[str] = set()
        for flags in self._stack:
            active.update(flags)
        return active

    @staticmethod
    def _flags_from_attrs(attrs: list[tuple[str, str | None]]) -> set[str]:
        style = ""
        for key, value in attrs or []:
            if str(key or "").lower() == "style":
                style = str(value or "")
                break
        flags: set[str] = set()
        for part in style.split(";"):
            if ":" not in part:
                continue
            key, value = part.split(":", 1)
            name = key.strip().lower()
            raw = value.strip().lower()
            if name == "font-style" and (raw.startswith("italic") or raw.startswith("oblique")):
                flags.add("italic")
            elif name == "font-weight" and (
                raw in {"bold", "bolder"} or (raw.isdigit() and int(raw) >= 600)
            ):
                flags.add("bold")
            elif name == "text-decoration" and "underline" in raw:
                flags.add("underline")
        return flags

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = str(tag or "").lower()
        if tag == "br":
            self.lines.append([])
            return
        flags: set[str] = set()
        mapped = self._STYLE_TAGS.get(tag)
        if mapped:
            flags.add(mapped)
        flags.update(self._flags_from_attrs(attrs))
        self._stack.append(flags)

    def handle_endtag(self, tag: str) -> None:
        if str(tag or "").lower() == "br":
            return
        if self._stack:
            self._stack.pop()

    def _append_text(self, data: str) -> None:
        text = _normalize_pdf_text(unescape(data))
        if not text:
            return
        active = self._active()
        run = {
            "text": text,
            "bold": "bold" in active,
            "italic": "italic" in active,
            "underline": "underline" in active,
        }
        if (
            self.lines[-1]
            and self.lines[-1][-1]["bold"] == run["bold"]
            and self.lines[-1][-1]["italic"] == run["italic"]
            and self.lines[-1][-1]["underline"] == run["underline"]
        ):
            self.lines[-1][-1]["text"] += text
        else:
            self.lines[-1].append(run)

    def handle_data(self, data: str) -> None:
        self._append_text(data)


def _split_styled_runs_on_newlines(runs: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    if not runs:
        return [[]]
    expanded: list[list[dict[str, Any]]] = [[]]
    for run in runs:
        text = _normalize_pdf_text(run.get("text"))
        if not text:
            continue
        parts = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        for index, part in enumerate(parts):
            if index:
                expanded.append([])
            if part:
                expanded[-1].append(
                    {
                        "text": part,
                        "bold": bool(run.get("bold")),
                        "italic": bool(run.get("italic")),
                        "underline": bool(run.get("underline")),
                    }
                )
    return expanded or [[]]


def _html_styled_lines(value: object) -> list[list[dict[str, Any]]]:
    raw = str(value or "")
    if not raw.strip():
        return [[]]
    if "<" not in raw:
        lines = [_normalize_pdf_text(unescape(line)) for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
        return [
            [{"text": line, "bold": False, "italic": False, "underline": False}]
            for line in lines
            if line
        ] or [[]]

    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"<br\s*/?>", "<br>", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"</(?:div|p|li)\s*>", "<br>", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"<(?:div|p|li)[^>]*>", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\n", "<br>", normalized)
    parser = _InlineHtmlStyleParser()
    parser.feed(normalized)
    parser.close()
    lines: list[list[dict[str, Any]]] = []
    for parsed_line in parser.lines or [[]]:
        lines.extend(_split_styled_runs_on_newlines(parsed_line))
    while lines and not any(run.get("text") for run in lines[-1]):
        lines.pop()
    return lines or [[]]


def _append_styled_meeting_pdf_lines(
    page: _PdfPage,
    *,
    x_base: float,
    y: float,
    cell_width: float,
    align: str,
    styled_lines: list[list[dict[str, Any]]],
    font_size: float,
    line_step: float,
    tw,
    font_family: str = "Arial",
    cell_bold: bool = False,
    cell_italic: bool = False,
    cell_underline: bool = False,
) -> None:
    for line_index, runs in enumerate(styled_lines or [[]]):
        prepared: list[dict[str, Any]] = []
        for run in runs:
            text = _normalize_pdf_text(run.get("text"))
            if not text:
                continue
            is_bold = cell_bold or bool(run.get("bold"))
            prepared.append(
                {
                    "text": text,
                    "bold": is_bold,
                    "italic": (cell_italic or bool(run.get("italic"))) and not is_bold,
                    "underline": cell_underline or bool(run.get("underline")),
                }
            )
        if not prepared:
            continue
        line_y = y - (line_index * line_step)
        total_width = sum(
            _text_width(item["text"], font_size, font_family, bold=bool(item["bold"]))
            for item in prepared
        )
        line_x = x_base
        if align in {"center", "auto"}:
            line_x += max(0.0, (cell_width - total_width) / 2)
        elif align == "right":
            line_x += max(0.0, cell_width - total_width)
        cursor_x = line_x
        for item in prepared:
            text = item["text"]
            text_width = _text_width(text, font_size, font_family, bold=bool(item["bold"]))
            page.lines.append(
                _PdfLine(
                    text=text,
                    x=cursor_x,
                    y=line_y,
                    size=font_size,
                    bold=bool(item["bold"]),
                    italic=bool(item["italic"]),
                )
            )
            if item["underline"]:
                underline_y = line_y - (font_size * 0.18)
                page.rules.append(_PdfRule(cursor_x, underline_y, cursor_x + text_width, underline_y, 0.35))
            cursor_x += text_width


def _html_line_count(value: object) -> int:
    return max(1, len(_html_styled_lines(value)))


def _html_text_lines(value: object) -> list[str]:
    lines = []
    for styled_line in _html_styled_lines(value):
        text = "".join(str(run.get("text") or "") for run in styled_line).strip()
        if text:
            lines.append(text)
    return lines


def _meeting_cell_styled_line_count(html: object) -> int:
    return max(1, len(_html_styled_lines(html)))


def _flow_column_segments(column: dict) -> list[dict]:
    segments = column.get("segments")
    if isinstance(segments, list) and segments:
        return segments
    return [{"html": column.get("html"), "align": column.get("align") or "left"}]


def _flow_column_styled_line_count(column: dict) -> int:
    segments = column.get("segments")
    if isinstance(segments, list) and segments:
        counts = [
            _meeting_cell_styled_line_count(segment.get("html"))
            for segment in segments
            if str(segment.get("html") or "").strip()
        ]
        return max(1, sum(counts)) if counts else 1
    return _meeting_cell_styled_line_count(column.get("html"))


def _meeting_row_font_size_pt(row: dict, default: float) -> float:
    for key in ("font_size_pt", "effective_font_size_pt"):
        try:
            size = float(row.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if size > 0:
            return max(4.0, size)
    return max(4.0, default)


def _meeting_row_line_height(row: dict, default: float) -> float:
    try:
        value = float(row.get("line_height") or 0)
    except (TypeError, ValueError):
        value = 0.0
    if value <= 0:
        try:
            value = float(default or 1.2)
        except (TypeError, ValueError):
            value = 1.2
    return max(0.8, min(3.0, value))


def _flow_row_print_line_count(row: dict) -> int:
    stored = row.get("flow_row_line_count")
    if stored is not None:
        try:
            return max(1, int(stored))
        except (TypeError, ValueError):
            pass
    from app.services.field_list_service import _flow_row_print_line_count as count_flow_row

    return count_flow_row(row)


def _meeting_row_line_count(
    row: dict,
    *,
    content_width: float | None = None,
    base_font_size: float = 7.0,
    font_family: str = "Arial",
) -> int:
    kind = _clean(row.get("kind"))
    if kind in {"blank", "divider"}:
        return 1
    if kind == "flow":
        return _flow_row_print_line_count(row)
    counts = [_meeting_cell_styled_line_count(cell.get("html")) for cell in row.get("cells") or []]
    return max([1, *counts])


def _meeting_row_chunks(rows: list[dict]) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    table_chunk: list[dict] = []
    in_table = False
    current_group = ""
    grouped_rows: list[dict] = []

    def flush_group() -> None:
        nonlocal current_group, grouped_rows
        if grouped_rows:
            chunks.append(grouped_rows)
        current_group = ""
        grouped_rows = []

    for row in rows:
        group = _clean(row.get("keep_together_group"))
        if group:
            if in_table:
                table_chunk.append(row)
                if bool(row.get("is_table_end_row")):
                    chunks.append(table_chunk)
                    table_chunk = []
                    in_table = False
                continue
            if current_group and group != current_group:
                flush_group()
            current_group = group
            grouped_rows.append(row)
            continue
        flush_group()
        if bool(row.get("is_table_start_row")) and not in_table:
            if table_chunk:
                chunks.append(table_chunk)
            table_chunk = [row]
            in_table = True
            if bool(row.get("is_table_end_row")):
                chunks.append(table_chunk)
                table_chunk = []
                in_table = False
            continue
        if in_table:
            table_chunk.append(row)
            if bool(row.get("is_table_end_row")):
                chunks.append(table_chunk)
                table_chunk = []
                in_table = False
            continue
        chunks.append([row])
        if table_chunk:
            chunks.append(table_chunk)
    flush_group()
    return chunks


def _fit_text(text: str, size: float, max_width: float, font_family: str = "Arial") -> str:
    value = _clean(text)
    if _text_width(value, size, font_family) <= max_width:
        return value
    suffix = "..."
    while value and _text_width(value + suffix, size, font_family) > max_width:
        value = value[:-1].rstrip()
    return (value + suffix) if value else suffix


def _wrap_text_lines(text: str, size: float, max_width: float, font_family: str = "Arial") -> list[str]:
    value = _clean(text)
    if not value:
        return []
    if _text_width(value, size, font_family) <= max_width:
        return [value]
    words = value.split()
    if not words:
        return [_fit_text(value, size, max_width, font_family)]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if _text_width(trial, size, font_family) <= max_width:
            current = trial
            continue
        lines.append(_fit_text(current, size, max_width, font_family))
        current = word
    if current:
        lines.append(_fit_text(current, size, max_width, font_family))
    return lines or [_fit_text(value, size, max_width, font_family)]


def _index_contact_name(name: object) -> str:
    return _clean(str(name or "").split(";", 1)[0])


def _cover_image_from_data_url(data_url: object, pixel_width: object, pixel_height: object) -> tuple[bytes, int, int] | None:
    raw = str(data_url or "").strip()
    if not raw:
        return None
    match = re.match(r"^data:image/jpeg;base64,(?P<data>[A-Za-z0-9+/=\s]+)$", raw)
    if not match:
        return None
    try:
        image_data = base64.b64decode(re.sub(r"\s+", "", match.group("data")), validate=True)
        width = int(float(pixel_width or 0))
        height = int(float(pixel_height or 0))
    except (ValueError, TypeError, binascii.Error):
        return None
    if not image_data or width <= 0 or height <= 0:
        return None
    return image_data, width, height


def _decode_base64_payload(data: object) -> bytes | None:
    raw = str(data or "").strip()
    if not raw:
        return None
    match = re.match(r"^data:[^;]+;base64,(?P<payload>[A-Za-z0-9+/=\s]+)$", raw)
    if match:
        raw = match.group("payload")
    try:
        decoded = base64.b64decode(re.sub(r"\s+", "", raw), validate=True)
    except (ValueError, TypeError, binascii.Error):
        return None
    return decoded or None


def _full_bleed_image_page(
    image_data: bytes,
    pixel_width: int,
    pixel_height: int,
    page_width: float,
    page_height: float,
    *,
    filter_name: str = "DCTDecode",
    margin_left: float = 0.0,
    margin_right: float = 0.0,
    margin_top: float = 0.0,
    margin_bottom: float = 0.0,
) -> _PdfPage:
    image_x = max(0.0, float(margin_left))
    image_y = max(0.0, float(margin_bottom))
    image_width = max(1.0, float(page_width) - image_x - max(0.0, float(margin_right)))
    image_height = max(1.0, float(page_height) - max(0.0, float(margin_top)) - image_y)
    page = _PdfPage(page_width, page_height)
    page.images.append(
        _PdfImage(
            image_data,
            pixel_width,
            pixel_height,
            image_x,
            image_y,
            image_width,
            image_height,
            filter_name=filter_name,
        )
    )
    return page


def _parse_insert_crop(raw: object) -> dict[str, float]:
    values = {"x": 0.5, "y": 0.5, "zoom": 1.0}
    if isinstance(raw, dict):
        payload = raw
    else:
        text = _clean(raw)
        if not text:
            return values
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return values
        payload = parsed if isinstance(parsed, dict) else {}
    try:
        values["x"] = min(1.0, max(0.0, float(payload.get("x", 0.5))))
    except (TypeError, ValueError):
        pass
    try:
        values["y"] = min(1.0, max(0.0, float(payload.get("y", 0.5))))
    except (TypeError, ValueError):
        pass
    try:
        values["zoom"] = min(3.0, max(1.0, float(payload.get("zoom", 1.0))))
    except (TypeError, ValueError):
        pass
    return values


def _insert_crop_window(
    source_width: float,
    source_height: float,
    output_width: float,
    output_height: float,
    crop: dict[str, float],
) -> tuple[float, float, float, float]:
    zoom = max(1.0, float(crop.get("zoom") or 1.0))
    crop_x = min(1.0, max(0.0, float(crop.get("x") or 0.5)))
    crop_y = min(1.0, max(0.0, float(crop.get("y") or 0.5)))
    source_ratio = source_width / max(source_height, 1.0)
    target_ratio = output_width / max(output_height, 1.0)
    if source_ratio > target_ratio:
        sh = source_height / zoom
        sw = sh * target_ratio
    else:
        sw = source_width / zoom
        sh = sw / target_ratio
    sx = max(0.0, source_width - sw) * crop_x
    sy = max(0.0, source_height - sh) * crop_y
    return sx, sy, sw, sh


def _pixmap_packed_rgb(pixmap) -> bytes:
    width = int(pixmap.width)
    height = int(pixmap.height)
    n = int(pixmap.n)
    stride = int(pixmap.stride)
    row_bytes = width * n
    samples = bytes(pixmap.samples)
    if n != 3:
        raise ValueError("print pixmap must be RGB")
    if stride == row_bytes:
        return samples
    packed = bytearray(height * row_bytes)
    for row in range(height):
        start = row * stride
        packed[row * row_bytes : (row + 1) * row_bytes] = samples[start : start + row_bytes]
    return bytes(packed)


def _pdf_page_to_print_image(
    pdf_bytes: bytes,
    page_width: float,
    page_height: float,
    crop: dict[str, float] | None = None,
) -> tuple[bytes, int, int, str] | None:
    try:
        from app.services.pymupdf_runtime import PymupdfUnavailableError, ensure_pymupdf

        ensure_pymupdf()
        import fitz
    except (ImportError, PymupdfUnavailableError):
        return None
    if not pdf_bytes:
        return None
    document = None
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
        if document.page_count < 1:
            return None
        page = document[0]
        source_rect = page.rect
        sx, sy, sw, sh = _insert_crop_window(
            source_rect.width,
            source_rect.height,
            page_width,
            page_height,
            crop or {"x": 0.5, "y": 0.5, "zoom": 1.0},
        )
        clip = fitz.Rect(source_rect.x0 + sx, source_rect.y0 + sy, source_rect.x0 + sx + sw, source_rect.y0 + sy + sh)
        pixel_width = max(1, int(page_width * (600.0 / 72.0)))
        pixel_height = max(1, int(page_height * (600.0 / 72.0)))
        matrix = fitz.Matrix(pixel_width / max(clip.width, 1.0), pixel_height / max(clip.height, 1.0))
        pixmap = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
        if pixmap.n != 3:
            pixmap = fitz.Pixmap(fitz.csRGB, pixmap)
        packed = _pixmap_packed_rgb(pixmap)
        return zlib.compress(packed, 9), int(pixmap.width), int(pixmap.height), "FlateDecode"
    except Exception:
        return None
    finally:
        if document is not None:
            document.close()


def _pdf_first_page_to_jpeg(pdf_bytes: bytes, page_width: float, page_height: float) -> tuple[bytes, int, int] | None:
    try:
        from app.services.pymupdf_runtime import PymupdfUnavailableError, ensure_pymupdf

        ensure_pymupdf()
        import fitz
    except (ImportError, PymupdfUnavailableError):
        return None
    if not pdf_bytes:
        return None
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
        if document.page_count < 1:
            return None
        page = document[0]
        pixel_width = max(1, int(page_width * (600.0 / 72.0)))
        pixel_height = max(1, int(page_height * (600.0 / 72.0)))
        source_rect = page.rect
        scale = max(pixel_width / max(source_rect.width, 1.0), pixel_height / max(source_rect.height, 1.0))
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        if pixmap.width == pixel_width and pixmap.height == pixel_height:
            cropped = pixmap
        else:
            clip_x = max(0, (pixmap.width - pixel_width) // 2)
            clip_y = max(0, (pixmap.height - pixel_height) // 2)
            clip = fitz.IRect(clip_x, clip_y, clip_x + pixel_width, clip_y + pixel_height)
            cropped = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, pixel_width, pixel_height), 0)
            cropped.copy(pixmap, clip)
        jpeg_bytes = cropped.tobytes("jpeg", jpg_quality=95)
        return jpeg_bytes, cropped.width, cropped.height
    except (RuntimeError, ValueError, TypeError):
        return None


def _build_insert_pages(
    *,
    include_insert_file: bool,
    insert_file_data: str,
    insert_file_mime: str,
    insert_file_width: int | str,
    insert_file_height: int | str,
    page_width: float,
    page_height: float,
    insert_file_crop: object = None,
    margin_left: float = 0.0,
    margin_right: float = 0.0,
    margin_top: float = 0.0,
    margin_bottom: float = 0.0,
) -> list[_PdfPage]:
    if not include_insert_file:
        return []
    mime = _clean(insert_file_mime).lower()
    crop = _parse_insert_crop(insert_file_crop)
    if mime == "application/pdf":
        pdf_bytes = _decode_base64_payload(insert_file_data)
        rendered = _pdf_page_to_print_image(pdf_bytes or b"", page_width, page_height, crop) if pdf_bytes else None
        if not rendered:
            fallback = _pdf_first_page_to_jpeg(pdf_bytes or b"", page_width, page_height) if pdf_bytes else None
            if not fallback:
                return []
            image_data, pixel_width, pixel_height = fallback
            return [
                _full_bleed_image_page(
                    image_data,
                    pixel_width,
                    pixel_height,
                    page_width,
                    page_height,
                    margin_left=margin_left,
                    margin_right=margin_right,
                    margin_top=margin_top,
                    margin_bottom=margin_bottom,
                )
            ]
        image_data, pixel_width, pixel_height, filter_name = rendered
        return [
            _full_bleed_image_page(
                image_data,
                pixel_width,
                pixel_height,
                page_width,
                page_height,
                filter_name=filter_name,
                margin_left=margin_left,
                margin_right=margin_right,
                margin_top=margin_top,
                margin_bottom=margin_bottom,
            )
        ]
    cover_image = _cover_image_from_data_url(insert_file_data, insert_file_width, insert_file_height)
    if not cover_image:
        return []
    image_data, pixel_width, pixel_height = cover_image
    return [
        _full_bleed_image_page(
            image_data,
            pixel_width,
            pixel_height,
            page_width,
            page_height,
            margin_left=margin_left,
            margin_right=margin_right,
            margin_top=margin_top,
            margin_bottom=margin_bottom,
        )
    ]


def _pdf_color(value: object) -> tuple[float, float, float]:
    text = _clean(value)
    match = re.match(r"^#?([0-9a-fA-F]{6})$", text)
    if not match:
        return (1.0, 1.0, 1.0)
    raw = match.group(1)
    return tuple(int(raw[index:index + 2], 16) / 255.0 for index in (0, 2, 4))


def _cover_title_lines(text: object, size: float, max_width: float) -> list[str]:
    return [line.rstrip() for line in str(text or "").splitlines() if line.strip()]


# Field/page footers used to sit at margin_bottom / 2, which prints too close to
# the cut edge. Lift them inside the unused bottom band. Content pagination still
# keys off margin_bottom, so body lines do not reflow.
_PAGE_FOOTER_LIFT_PT = 4.0


def _page_footer_y(margin_bottom: float) -> float:
    half = float(margin_bottom) * 0.5
    return min(half + _PAGE_FOOTER_LIFT_PT, max(half, float(margin_bottom) - 1.0))


def _separator_footer_y(margin_bottom: float) -> float:
    return float(margin_bottom) + _PAGE_FRAME_DOUBLE_GAP_PT + 2.2


def _append_rect_rules(page: _PdfPage, x1: float, y1: float, x2: float, y2: float, width: float) -> None:
    page.rules.append(_PdfRule(x1, y1, x2, y1, width))
    page.rules.append(_PdfRule(x1, y2, x2, y2, width))
    page.rules.append(_PdfRule(x1, y1, x1, y2, width))
    page.rules.append(_PdfRule(x2, y1, x2, y2, width))


def _draw_page_margin_frame(
    page: _PdfPage,
    *,
    margin_left: float,
    margin_right: float,
    margin_top: float,
    margin_bottom: float,
) -> None:
    left = float(margin_left)
    right = page.width - float(margin_right)
    bottom = float(margin_bottom)
    top = page.height - float(margin_top)
    if right - left < 12 or top - bottom < 12:
        return
    _append_rect_rules(page, left, bottom, right, top, _PAGE_FRAME_OUTER_WIDTH_PT)
    gap = _PAGE_FRAME_DOUBLE_GAP_PT
    inner_left = left + gap
    inner_right = right - gap
    inner_bottom = bottom + gap
    inner_top = top - gap
    if inner_right - inner_left > 8 and inner_top - inner_bottom > 8:
        _append_rect_rules(page, inner_left, inner_bottom, inner_right, inner_top, _PAGE_FRAME_INNER_WIDTH_PT)


def _heading_font_sizes(
    base_font_size: float,
    compact: bool,
    title_font_size: float | None = None,
    toc_font_size: float | None = None,
) -> tuple[float, float]:
    try:
        title_size = float(title_font_size) if title_font_size not in (None, "") else 0.0
    except (TypeError, ValueError):
        title_size = 0.0
    try:
        item_size = float(toc_font_size) if toc_font_size not in (None, "") else 0.0
    except (TypeError, ValueError):
        item_size = 0.0
    if title_size <= 0:
        title_size = base_font_size if compact else max(base_font_size + 4.0, 12.0)
    if item_size <= 0:
        item_size = base_font_size if compact else max(base_font_size + 3.0, 10.0)
    return max(1.0, title_size), max(1.0, item_size)


def _build_toc_pages(
    *,
    entries: list[_TocEntry],
    book_title: str,
    page_width: float,
    page_height: float,
    margin_left: float,
    margin_right: float,
    margin_top: float,
    margin_bottom: float,
    base_font_size: float,
    page_offset: int,
    link_page_offset: int | None = None,
    compact: bool = False,
    font_family: str = "Arial",
    title_font_size: float | None = None,
    toc_font_size: float | None = None,
) -> list[_PdfPage]:
    if not entries:
        return []
    link_offset = page_offset if link_page_offset is None else int(link_page_offset)

    def tw(text: str, size: float) -> float:
        return _text_width(text, size, font_family)

    def fit(text: str, size: float, max_width: float) -> str:
        return _fit_text(text, size, max_width, font_family)

    toc_pages: list[_PdfPage] = []
    title_size, item_size = _heading_font_sizes(
        base_font_size, compact, title_font_size, toc_font_size
    )
    field_size = item_size
    line_step = item_size * 1.15
    content_width = page_width - margin_left - margin_right
    title_text = fit(f"{_clean(book_title) or 'Address Book'} - {_short_date()}", title_size, content_width)
    indent = item_size * 1.2
    page_number_width = max(tw("888", item_size), item_size * 2.1)
    page_number_x = page_width - margin_right - page_number_width
    item_max_width = max(24.0, page_number_x - margin_left - indent - (item_size * 0.5))
    y = 0.0

    def add_toc_page() -> _PdfPage:
        nonlocal y
        page = _PdfPage(page_width, page_height)
        toc_pages.append(page)
        y = page_height - margin_top
        if len(toc_pages) == 1:
            y -= line_step * 0.85
            title_x = max(margin_left, (page_width - tw(title_text, title_size)) / 2)
            page.lines.append(_PdfLine(title_text, title_x, y, title_size, bold=True))
            y -= line_step * 1.7
        else:
            y -= line_step * 1.25
        return page

    def add_footer(page: _PdfPage) -> None:
        text = f"Table of Contents - Page {len(toc_pages)}"
        page.lines.append(
            _PdfLine(
                text,
                x=max(margin_left, (page_width - tw(text, item_size)) / 2),
                y=_page_footer_y(margin_bottom),
                size=item_size,
            )
        )

    page = add_toc_page()
    current_field = ""
    for entry in entries:
        if entry.is_heading_only:
            if y - line_step < margin_bottom:
                add_footer(page)
                page = add_toc_page()
            entry.toc_page_index = len(toc_pages) - 1
            display_page = str(entry.page + page_offset)
            page_text_width = tw(display_page, item_size)
            target_page_index = max(0, entry.page + link_offset - 1)
            item_text = fit(entry.meeting_heading, field_size, content_width - page_number_width - item_size)
            item_width = tw(item_text, field_size)
            number_x = page_width - margin_right - page_text_width
            dots_x = margin_left + item_width + (item_size * 0.35)
            dots_width = max(0.0, number_x - dots_x)
            dot_count = max(0, int(dots_width / max(1.0, tw(".", item_size))))
            page.lines.append(_PdfLine(item_text, margin_left, y, field_size, bold=True, link_page_index=target_page_index))
            if dot_count:
                page.lines.append(_PdfLine("." * dot_count, dots_x, y, item_size, bold=True, link_page_index=target_page_index))
            page.lines.append(_PdfLine(display_page, number_x, y, item_size, link_page_index=target_page_index))
            y -= line_step
            current_field = ""
            continue
        needs_field = bool(entry.field_name) and entry.field_name != current_field
        required_lines = 2 if needs_field else 1
        if y - (required_lines * line_step) < margin_bottom:
            add_footer(page)
            page = add_toc_page()
        if needs_field:
            current_field = entry.field_name
            page.lines.append(_PdfLine(current_field, margin_left, y, field_size, bold=True))
            y -= line_step
        display_page = str(entry.page + page_offset)
        page_text_width = tw(display_page, item_size)
        target_page_index = max(0, entry.page + link_offset - 1)
        entry.toc_page_index = len(toc_pages) - 1
        text_x = margin_left + indent
        item_text = fit(entry.meeting_heading, item_size, item_max_width)
        item_width = tw(item_text, item_size)
        number_x = page_width - margin_right - page_text_width
        dots_x = text_x + item_width + (item_size * 0.35)
        dots_width = max(0.0, number_x - dots_x)
        dot_count = max(0, int(dots_width / max(1.0, tw(".", item_size))))
        if dot_count:
            page.lines.append(_PdfLine("." * dot_count, dots_x, y, item_size, bold=True, link_page_index=target_page_index))
        page.lines.append(_PdfLine(item_text, text_x, y, item_size, link_page_index=target_page_index))
        page.lines.append(_PdfLine(display_page, number_x, y, item_size, link_page_index=target_page_index))
        y -= line_step

    add_footer(page)
    return toc_pages


def _shift_content_footers(pages: list[_PdfPage], page_offset: int, font_family: str = "Arial") -> None:
    if page_offset <= 0:
        return
    footer_pattern = re.compile(r"^(?P<label>.+ - Page )(?P<page>\d+)$")
    for page in pages:
        for line in page.lines:
            match = footer_pattern.match(line.text)
            if not match or match.group("label") == "Table of Contents - Page ":
                continue
            line.text = f"{match.group('label')}{int(match.group('page')) + page_offset}"
            line.x = max(0.0, (page.width - _text_width(line.text, line.size, font_family)) / 2)


def _build_index_pages(
    *,
    entries: list[_IndexEntry],
    page_width: float,
    page_height: float,
    margin_left: float,
    margin_right: float,
    margin_top: float,
    margin_bottom: float,
    base_font_size: float,
    start_page_number: int,
    title_link_page_index: int | None = None,
    link_page_offset: int = 0,
    compact: bool = False,
    font_family: str = "Arial",
    title_font_size: float | None = None,
    toc_font_size: float | None = None,
    index_font_size: float | None = None,
    two_column: bool = False,
) -> list[_PdfPage]:
    if not entries:
        return []

    def tw(text: str, size: float) -> float:
        return _text_width(text, size, font_family)

    def fit(text: str, size: float, max_width: float) -> str:
        return _fit_text(text, size, max_width, font_family)

    sorted_entries = sorted(entries, key=lambda item: (_sort_key(item.name), item.page))
    index_pages: list[_PdfPage] = []
    title_size, item_size = _heading_font_sizes(
        base_font_size,
        compact,
        title_font_size,
        index_font_size if index_font_size not in (None, "") else toc_font_size,
    )
    letter_size = item_size
    line_step = item_size * 1.15
    content_width = page_width - margin_left - margin_right
    gutter = item_size * 1.15 if two_column else 0.0
    column_count = 2 if two_column else 1
    column_width = max(36.0, (content_width - gutter) / column_count)
    page_number_width = max(tw("888", item_size), item_size * 2.1)
    name_max_width = max(24.0, column_width - page_number_width - (item_size * 0.5))
    y = 0.0
    body_top_y = 0.0
    column = 0

    def column_x(column_index: int) -> float:
        return margin_left + (column_index * (column_width + gutter))

    def index_page_number() -> int:
        return start_page_number + len(index_pages) - 1

    def add_index_page() -> _PdfPage:
        nonlocal y, body_top_y, column
        page = _PdfPage(page_width, page_height)
        index_pages.append(page)
        y = page_height - margin_top
        y -= line_step * 0.85
        title = "Index"
        title_x = max(margin_left, (page_width - tw(title, title_size)) / 2)
        page.lines.append(_PdfLine(title, title_x, y, title_size, bold=True, link_page_index=title_link_page_index))
        y -= line_step * 1.7
        body_top_y = y
        column = 0
        if two_column:
            divider_x = margin_left + column_width + (gutter / 2.0)
            page.rules.append(
                _PdfRule(
                    divider_x,
                    _page_footer_y(margin_bottom) + (item_size * 1.4),
                    divider_x,
                    y + (line_step * 0.2),
                    0.55,
                )
            )
        return page

    def add_footer(page: _PdfPage) -> None:
        text = f"Index - Page {index_page_number()}"
        page.lines.append(
            _PdfLine(
                text,
                x=max(margin_left, (page_width - tw(text, item_size)) / 2),
                y=_page_footer_y(margin_bottom),
                size=item_size,
            )
        )

    def ensure_room(required_lines: int) -> None:
        nonlocal page, y, column
        if y - (required_lines * line_step) >= margin_bottom:
            return
        if two_column and column == 0:
            column = 1
            y = body_top_y
            if y - (required_lines * line_step) >= margin_bottom:
                return
        add_footer(page)
        page = add_index_page()

    page = add_index_page()
    current_letter = ""
    for entry in sorted_entries:
        first_char = (_clean(entry.name)[:1] or "#").upper()
        letter = first_char if first_char.isalpha() else "#"
        needs_letter = letter != current_letter
        required_lines = 2 if needs_letter else 1
        ensure_room(required_lines)
        left = column_x(column)
        if needs_letter:
            current_letter = letter
            page.lines.append(_PdfLine(letter, left, y, letter_size, bold=True))
            y -= line_step

        display_page = str(entry.page)
        page_text_width = tw(display_page, item_size)
        target_page_index = max(0, entry.page + int(link_page_offset) - 1)
        name_text = fit(entry.name, item_size, name_max_width)
        name_width = tw(name_text, item_size)
        number_x = left + column_width - page_text_width
        dots_x = left + name_width + (item_size * 0.35)
        dots_width = max(0.0, number_x - dots_x)
        dot_count = max(0, int(dots_width / max(1.0, tw(".", item_size))))
        page.lines.append(_PdfLine(name_text, left, y, item_size, link_page_index=target_page_index, link_page_y=entry.target_y))
        if dot_count:
            page.lines.append(_PdfLine("." * dot_count, dots_x, y, item_size, bold=True, link_page_index=target_page_index, link_page_y=entry.target_y))
        page.lines.append(_PdfLine(display_page, number_x, y, item_size, link_page_index=target_page_index, link_page_y=entry.target_y))
        y -= line_step

    add_footer(page)
    return index_pages


def _clamp_impose_indices(indices: list[int], total_pages: int) -> list[int]:
    return [index if 0 <= index < total_pages else -1 for index in indices]


def _build_cut_sheet_maps(total_pages: int) -> list[dict[str, list[int]]]:
    padded = int(math.ceil(max(1, total_pages) / 8.0) * 8)
    quarter = padded // 4
    sheets: list[dict[str, list[int]]] = []
    for sheet_index in range(max(1, quarter // 2)):
        front_number = (sheet_index * 2) + 1
        back_number = front_number + 1
        front = [
            front_number - 1,
            front_number + quarter - 1,
            front_number + (2 * quarter) - 1,
            front_number + (3 * quarter) - 1,
        ]
        back = [
            back_number + quarter - 1,
            back_number - 1,
            back_number + (3 * quarter) - 1,
            back_number + (2 * quarter) - 1,
        ]
        sheets.append(
            {
                "front": _clamp_impose_indices(front, total_pages),
                "back": _clamp_impose_indices(back, total_pages),
            }
        )
    return sheets


def _build_booklet_sheet_maps(total_pages: int) -> list[dict[str, list[int]]]:
    padded = int(math.ceil(max(1, total_pages) / 8.0) * 8)
    sheet_count = padded // 8
    quarter = padded // 4
    sheets: list[dict[str, list[int]]] = []

    def norm(page_number: int) -> int:
        return page_number - 1 if 1 <= page_number <= total_pages else -1

    for sheet_index in range(sheet_count):
        left = padded - (2 * sheet_index)
        right = 1 + (2 * sheet_index)
        sheets.append(
            {
                "front": [
                    norm(left),
                    norm(right),
                    norm(left - quarter),
                    norm(right + quarter),
                ],
                "back": [
                    norm(right + 1),
                    norm(left - 1),
                    norm(right + quarter + 1),
                    norm(left - quarter - 1),
                ],
            }
        )
    return sheets


def _shift_page_x(page: _PdfPage, dx: float) -> None:
    if abs(dx) < 0.01:
        return
    for line in page.lines:
        line.x += dx
    for rule in page.rules:
        rule.x1 += dx
        rule.x2 += dx
    for image in page.images:
        image.x += dx


def _mirror_side_spiral_even_page_margins(
    pages: list[_PdfPage], margin_left: float, margin_right: float
) -> None:
    """Keep the larger binding margin on the spiral edge after a leaf is turned.

    Odd pages keep Left/Right as set. Even pages swap them so the extra gutter
    sits on the bound edge (the right side of the verso).
    """
    dx = float(margin_right) - float(margin_left)
    if abs(dx) < 0.01:
        return
    for index, page in enumerate(pages):
        if index % 2 == 1:
            _shift_page_x(page, dx)


# Front (odd) sheets are left-flush. Back (even) sheets are right-flush so the
# 4-up block registers through the paper after a long-edge duplex flip.

def _letter_block_offset_x(page_width: float, *, right_justify: bool) -> float:
    letter_width = 8.5 * 72.0
    block_width = page_width * 2.0
    spare = max(0.0, letter_width - block_width)
    if right_justify:
        return spare
    return 0.0


def _letter_positions(page_width: float, page_height: float, *, right_justify: bool) -> list[tuple[float, float]]:
    letter_height = 11.0 * 72.0
    block_height = page_height * 2.0
    offset_x = _letter_block_offset_x(page_width, right_justify=right_justify)
    # Top-aligned: sheet top edge is the top trim.
    # Layout margins (Top/Left/…) stay inside each logical page: cut mark → text.
    top_y = letter_height - page_height
    bottom_y = letter_height - block_height
    return [
        (offset_x, top_y),
        (offset_x + page_width, top_y),
        (offset_x, bottom_y),
        (offset_x + page_width, bottom_y),
    ]


def _draw_cut_marks(page: _PdfPage, slot_width: float, slot_height: float, *, right_justify: bool) -> None:
    letter_height = page.height
    block_height = slot_height * 2.0
    # Front marks sit on the left sheet edge with the left-flush block.
    # Back marks follow the right-flush block.
    if right_justify:
        offset_x = _letter_block_offset_x(slot_width, right_justify=True)
    else:
        offset_x = 0.0
    x0 = offset_x
    x1 = offset_x + slot_width
    x2 = offset_x + (slot_width * 2.0)
    y0 = letter_height
    y1 = letter_height - slot_height
    y2 = letter_height - block_height
    tick = 4.0

    def plus(x: float, y: float) -> None:
        page.rules.append(_PdfRule(x - tick, y, x + tick, y, 0.5))
        page.rules.append(_PdfRule(x, y - tick, x, y + tick, 0.5))

    for x in [x0, x1, x2]:
        plus(x, y0)
        plus(x, y1)
        plus(x, y2)


BOOKLET_FOLD_GUTTER_PER_SHEET_PT = 0.36
BOOKLET_FOLD_GUTTER_MAX_PT = 8.64


def _booklet_fold_gutter_offset(slot_index: int, sheet_index: int, sheet_count: int) -> float:
    if sheet_count <= 1:
        return 0.0
    outside_sheet_depth = max(0, sheet_count - sheet_index - 1)
    offset = min(BOOKLET_FOLD_GUTTER_MAX_PT, outside_sheet_depth * BOOKLET_FOLD_GUTTER_PER_SHEET_PT)
    if slot_index in {0, 2}:
        return -offset
    return offset


def _clone_page_into_slot(
    dest: _PdfPage,
    source: _PdfPage,
    slot_x: float,
    slot_y: float,
    *,
    rotate_180: bool = False,
    content_offset_x: float = 0.0,
    font_family: str = "Arial",
) -> None:
    def rotate_point(x: float, y: float) -> tuple[float, float]:
        return slot_x + source.width - x + content_offset_x, slot_y + source.height - y

    for rule in source.rules:
        if rotate_180:
            x1, y1 = rotate_point(rule.x1, rule.y1)
            x2, y2 = rotate_point(rule.x2, rule.y2)
            dest.rules.append(_PdfRule(x1, y1, x2, y2, rule.width))
        else:
            dest.rules.append(
                _PdfRule(
                    slot_x + rule.x1 + content_offset_x,
                    slot_y + rule.y1,
                    slot_x + rule.x2 + content_offset_x,
                    slot_y + rule.y2,
                    rule.width,
                )
            )

    for image in source.images:
        if rotate_180:
            dest.images.append(
                _PdfImage(
                    image.data,
                    image.pixel_width,
                    image.pixel_height,
                    slot_x + source.width - image.x - image.width + content_offset_x,
                    slot_y + source.height - image.y - image.height,
                    image.width,
                    image.height,
                    rotate_180=not image.rotate_180,
                    filter_name=image.filter_name,
                )
            )
        else:
            dest.images.append(
                _PdfImage(
                    image.data,
                    image.pixel_width,
                    image.pixel_height,
                    slot_x + image.x + content_offset_x,
                    slot_y + image.y,
                    image.width,
                    image.height,
                    rotate_180=image.rotate_180,
                    filter_name=image.filter_name,
                )
            )

    for line in source.lines:
        line_width = line.width or _text_width(line.text, line.size, font_family)
        if rotate_180:
            x = slot_x + source.width - line.x - line_width + content_offset_x
            y = slot_y + source.height - line.y - line.size
        else:
            x = slot_x + line.x + content_offset_x
            y = slot_y + line.y
        dest.lines.append(
            _PdfLine(
                text=line.text,
                x=x,
                y=y,
                size=line.size,
                bold=line.bold,
                width=line.width,
                color=line.color,
                effect=line.effect,
                rotate_180=rotate_180 or line.rotate_180,
                outer_header=line.outer_header,
            )
        )


def _impose_pages_to_letter(
    pages: list[_PdfPage],
    binding: str,
    font_family: str = "Arial",
    *,
    include_cut_marks: bool = True,
) -> list[_PdfPage]:
    if not pages:
        return []
    mode = _clean(binding).lower()
    if mode not in {"side_spiral", "top_spiral", "stapled_folded"}:
        mode = "side_spiral"
    page_width = pages[0].width
    page_height = pages[0].height
    letter_width = 8.5 * 72.0
    letter_height = 11.0 * 72.0
    positions_front = _letter_positions(page_width, page_height, right_justify=False)
    positions_back = _letter_positions(page_width, page_height, right_justify=True)
    sheet_maps = _build_booklet_sheet_maps(len(pages)) if mode == "stapled_folded" else _build_cut_sheet_maps(len(pages))
    imposed_pages: list[_PdfPage] = []

    sheet_count = len(sheet_maps)
    for sheet_index, sheet_map in enumerate(sheet_maps):
        for side in ("front", "back"):
            sheet = _PdfPage(letter_width, letter_height)
            positions = positions_front if side == "front" else positions_back
            rotate_back = mode == "top_spiral" and side == "back"
            for slot_index, page_index in enumerate(sheet_map[side]):
                if page_index < 0 or page_index >= len(pages):
                    continue
                slot_x, slot_y = positions[slot_index]
                fold_offset_x = _booklet_fold_gutter_offset(slot_index, sheet_index, sheet_count) if mode == "stapled_folded" else 0.0
                _clone_page_into_slot(
                    sheet,
                    pages[page_index],
                    slot_x,
                    slot_y,
                    rotate_180=rotate_back,
                    content_offset_x=fold_offset_x,
                    font_family=font_family,
                )
            if include_cut_marks:
                _draw_cut_marks(sheet, page_width, page_height, right_justify=(side == "back"))
            imposed_pages.append(sheet)

    return imposed_pages


def build_address_book_pdf(
    *,
    fields: list[dict],
    meeting_preview_sections: list[dict],
    settings: dict[str, Any],
    layout: dict[str, Any],
    print_order_mode: str,
    print_order_json: str,
    cover_image_data: str = "",
    cover_image_width: int | str = 0,
    cover_image_height: int | str = 0,
    cover_title_enabled: bool = False,
    cover_title_text: str = "",
    cover_title_font_size: int | str = 30,
    cover_title_color: str = "#ffffff",
    cover_title_effect: str = "shadow",
    cover_title_x: int | float | str = 0.5,
    cover_title_y: int | float | str = 0.24,
    cover_title_preview_width: int | float | str = 0,
    cover_date_enabled: bool = False,
    cover_date_text: str = "",
    cover_date_font_size: int | str = 18,
    cover_date_color: str = "#ffffff",
    cover_date_x: int | float | str = 0.5,
    cover_date_y: int | float | str = 0.82,
    include_insert_file: bool = False,
    insert_file_data: str = "",
    insert_file_mime: str = "",
    insert_file_width: int | str = 0,
    insert_file_height: int | str = 0,
    insert_file_crop: object = None,
    paper_book_binding: str = "",
    paper_book_cut_marks: bool = True,
) -> bytes:
    ordered_fields = order_address_book_fields(fields, print_order_mode, print_order_json)
    page_width = max(72.0, float(layout.get("trim_width_in") or 3.5) * 72.0)
    page_height = max(72.0, float(layout.get("trim_height_in") or 5.5) * 72.0)
    margin_left = max(6.0, float(layout.get("margin_left_in") or 0.14) * 72.0)
    margin_right = max(6.0, float(layout.get("margin_right_in") or 0.14) * 72.0)
    margin_top = max(6.0, float(layout.get("margin_top_in") or 0.14) * 72.0)
    margin_bottom = max(6.0, float(layout.get("margin_bottom_in") or 0.14) * 72.0)
    font_size = max(4.0, float(layout.get("base_font_size_pt") or 7.0))
    font_family = _normalize_pdf_font_family(layout.get("font_family"))
    book_line_height = max(0.8, float(layout.get("line_height") or 1.2))
    current_meeting_line_height = book_line_height
    line_step = font_size * book_line_height

    def meeting_spacing_value(meeting: dict | None = None) -> float:
        if not meeting:
            return book_line_height
        try:
            value = float(meeting.get("line_height") or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value <= 0:
            return book_line_height
        return max(0.8, min(3.0, value))

    def apply_meeting_spacing(meeting: dict | None = None) -> None:
        nonlocal line_step, current_meeting_line_height
        current_meeting_line_height = meeting_spacing_value(meeting)
        line_step = font_size * current_meeting_line_height
    provider = "apple" if settings.get("address_map_provider") == "apple" else "google"
    address_align_right = settings.get("address_align") != "left"
    address_italic = bool(settings.get("address_italic", 1))
    meeting_page_mode = _clean(settings.get("meeting_page_mode") or "one_meeting").lower()
    pack_complete_meetings = meeting_page_mode == "complete_meetings"
    field_separator_pages = bool(settings.get("field_separator_pages"))
    double_column_index = bool(settings.get("double_column_index"))
    include_bible_study = bool(settings.get("include_bible_study_union_info", 1))

    writer = _PdfWriter(
        page_width,
        page_height,
        font_family=font_family,
        title=str(layout.get("book_title") or "Address Book"),
    )
    page = writer.add_page()
    y = page_height - margin_top
    page_number = 1
    content_width = page_width - margin_left - margin_right
    from app.services.meeting_toc_service import meeting_is_unlisted_in_toc, unlisted_toc_meeting_keys

    toc_entries: list[_TocEntry] = []
    index_entries: list[_IndexEntry] = []
    current_toc_entry: _TocEntry | None = None
    unlisted_toc_meetings = unlisted_toc_meeting_keys()

    def tw(text: str, size: float) -> float:
        return _text_width(text, size, font_family)

    def fit_line(text: str, x: float, size: float | None = None) -> str:
        line_size = size or font_size
        max_width = max(0.0, page_width - margin_right - x)
        return _fit_text(text, line_size, max_width, font_family)

    def new_page(field_name: str = "", meeting_heading: str = "") -> None:
        nonlocal page, y, page_number
        page_number += 1
        page = writer.add_page()
        y = page_height - margin_top
        if meeting_heading:
            heading_line = add_center(f"{meeting_heading} - (continued)", font_size + 1, bold=True)
            if current_toc_entry:
                current_toc_entry.heading_lines.append(heading_line)
            add_rule()

    def ensure_space(lines_needed: int, field_name: str = "", meeting_heading: str = "") -> None:
        if y - (lines_needed * line_step) < margin_bottom:
            add_footer(field_name)
            new_page(field_name, meeting_heading)

    def table_chunk_step_total(chunk: list[dict]) -> float:
        total = 0.0
        for row in chunk:
            row_font_size = _meeting_row_font_size_pt(row, font_size)
            row_line_step = row_font_size * _meeting_row_line_height(row, current_meeting_line_height)
            row_line_count = _meeting_row_line_count(row, **meeting_row_count_kwargs)
            total += row_line_count * row_line_step
        return total

    def ensure_table_chunk_space(chunk: list[dict], field_name: str = "", meeting_heading: str = "") -> None:
        needed = table_chunk_step_total(chunk)
        if y - needed >= margin_bottom:
            return
        add_footer(field_name)
        new_page(field_name, meeting_heading)

    def add_text(
        text: str,
        x: float,
        *,
        size: float | None = None,
        bold: bool = False,
        italic: bool = False,
        uri: str = "",
    ) -> None:
        page.lines.append(
            _PdfLine(
                text=text,
                x=x,
                y=y,
                size=size or font_size,
                bold=bold,
                italic=italic,
                uri=uri,
            )
        )

    def add_contact_name_line(line: str, x: float, *, bold_last_name: bool) -> None:
        safe_line = fit_line(line, x)
        if not bold_last_name:
            add_text(safe_line, x)
            return
        comma_index = safe_line.find(",")
        if comma_index <= 0:
            add_text(safe_line, x)
            return
        last_name = safe_line[:comma_index]
        rest = safe_line[comma_index:]
        rest_x = x + _text_width(last_name, font_size, font_family, bold=True)
        page.lines.append(_PdfLine(text=last_name, x=x, y=y, size=font_size, bold=True))
        page.lines.append(_PdfLine(text=fit_line(rest, rest_x), x=rest_x, y=y, size=font_size))

    def contact_name_lines(contact: dict[str, Any], name: str, max_width: float) -> list[str]:
        from app.services.field_list_service import _wrap_contact_name_lines

        payload = {
            "name": name,
            "label": name,
            "children": list(contact.get("children") or []),
            "other_relationships": list(contact.get("other_relationships") or []),
        }
        lines = _wrap_contact_name_lines(payload, lambda text: tw(text, font_size), max_width)
        return lines or ([name] if name else [])

    def add_center(text: str, size: float, *, bold: bool = False, link_page_index: int | None = None) -> _PdfLine:
        nonlocal y
        x = (page_width - tw(text, size)) / 2
        line = _PdfLine(text=text, x=max(margin_left, x), y=y, size=size, bold=bold, link_page_index=link_page_index)
        page.lines.append(line)
        y -= line_step
        return line

    def add_rule() -> None:
        nonlocal y
        page.rules.append(_PdfRule(margin_left, y + (line_step * 0.3), page_width - margin_right, y + (line_step * 0.3)))
        y -= line_step

    def add_footer(field_name: str, *, framed: bool = False) -> None:
        text = f"{field_name or 'All Fields'} - Page {page_number}"
        page.lines.append(
            _PdfLine(
                text=text,
                x=max(margin_left, (page_width - tw(text, font_size)) / 2),
                y=_separator_footer_y(margin_bottom) if framed else _page_footer_y(margin_bottom),
                size=font_size,
                bold=True,
            )
        )

    def _continued_heading_text(meeting_heading: str) -> str:
        return f"{meeting_heading} - (continued)"

    def _page_content_lines() -> list[_PdfLine]:
        footer_cutoff = margin_bottom + font_size
        return [line for line in page.lines if line.y > footer_cutoff]

    def _page_is_continued_only(meeting_heading: str) -> bool:
        content_lines = _page_content_lines()
        if not content_lines:
            return bool(page.rules)
        continued = _continued_heading_text(meeting_heading)
        return all(line.text == continued for line in content_lines)

    def _reset_current_page_content() -> None:
        page.lines.clear()
        page.rules.clear()

    def start_standalone_table_page() -> None:
        nonlocal y
        if _page_is_continued_only(heading):
            _reset_current_page_content()
            y = page_height - margin_top
            return
        if _page_content_lines():
            add_footer(field_name)
        new_page()

    def start_print_new_page_for_table() -> None:
        content_lines = _page_content_lines()
        if not content_lines:
            return
        heading_texts = {heading, _continued_heading_text(heading)}
        if all(line.text in heading_texts for line in content_lines):
            return
        add_footer(field_name)
        new_page(field_name, heading)

    def chunk_is_table_chunk(chunk: list[dict]) -> bool:
        return any(bool(row.get("is_table_start_row")) for row in chunk)

    def add_meeting_preview_row(row: dict) -> None:
        nonlocal y
        kind = _clean(row.get("kind"))
        row_font_size = _meeting_row_font_size_pt(row, font_size)
        row_line_step = row_font_size * _meeting_row_line_height(row, current_meeting_line_height)
        column_gap_pt = _meeting_column_gap_pt(
            row.get("column_gap_px"),
            grid_width_pt=content_width,
            preview_printable_width_px=row.get("preview_printable_width_px"),
        )
        grid_column_count = max(1, int(row.get("grid_column_count") or _MEETING_GRID_COLUMNS))
        row_line_count = _meeting_row_line_count(
            row,
            content_width=content_width,
            base_font_size=row_font_size,
            font_family=font_family,
        )

        def add_row_underline() -> None:
            if bool(row.get("is_underlined_row")):
                line_y = y - (row_line_count * row_line_step) + (row_line_step * 0.75)
                page.rules.append(_PdfRule(margin_left, line_y, page_width - margin_right, line_y))

        if kind == "blank":
            y -= line_step
            return
        if kind == "divider":
            add_rule()
            return
        if kind == "flow":
            grid_width = content_width

            def draw_flow_column(column: dict, band_y: float) -> int:
                cell_x, cell_width = _meeting_grid_cell_bounds(
                    column.get("col_start"),
                    column.get("col_span"),
                    grid_width,
                    column_gap_pt=column_gap_pt,
                    column_count=grid_column_count,
                )
                segment_y = band_y
                column_lines = 0
                for segment in _flow_column_segments(column):
                    styled_lines = _html_styled_lines(segment.get("html"))
                    if not any(any(run.get("text") for run in line) for line in styled_lines):
                        continue
                    align = _clean(segment.get("align"))
                    _append_styled_meeting_pdf_lines(
                        page,
                        x_base=margin_left + cell_x,
                        y=segment_y,
                        cell_width=cell_width,
                        align=align,
                        styled_lines=styled_lines,
                        font_size=row_font_size,
                        line_step=row_line_step,
                        tw=tw,
                        font_family=font_family,
                    )
                    segment_line_count = max(
                        1,
                        len([line for line in styled_lines if any(run.get("text") for run in line)]),
                    )
                    column_lines = max(column_lines, segment_line_count)
                    segment_y -= row_line_step * segment_line_count
                return max(1, column_lines)

            primary_line_counts: dict[int, int] = {}
            for column in row.get("columns") or []:
                cell_id = int(column.get("cell_id") or 0)
                lines = draw_flow_column(column, y)
                if cell_id:
                    primary_line_counts[cell_id] = lines

            row_extent_lines = _flow_row_print_line_count(row)
            for column in row.get("stacked_columns") or []:
                parent_id = int(column.get("parent_cell_id") or 0)
                parent_lines = primary_line_counts.get(parent_id, 1)
                # Leave one blank line between the parent column's content and the
                # stacked cell below it, matching the on-screen meeting preview
                # (whose grid row spacing puts a line of space before the cell).
                stacked_top = parent_lines + 1
                lines = draw_flow_column(column, y - (row_line_step * stacked_top))
                row_extent_lines = max(row_extent_lines, stacked_top + lines)

            add_row_underline()
            y -= row_line_step * row_extent_lines
            return
        for cell in row.get("cells") or []:
            styled_lines = _html_styled_lines(cell.get("html"))
            if not any(any(run.get("text") for run in line) for line in styled_lines):
                continue
            cell_x, cell_width = _meeting_grid_cell_bounds(
                cell.get("col_start"),
                cell.get("col_span"),
                content_width,
                column_gap_pt=column_gap_pt,
                column_count=grid_column_count,
            )
            align = _clean(cell.get("align"))
            _append_styled_meeting_pdf_lines(
                page,
                x_base=margin_left + cell_x,
                y=y,
                cell_width=cell_width,
                align=align,
                styled_lines=styled_lines,
                font_size=row_font_size,
                line_step=row_line_step,
                tw=tw,
                font_family=font_family,
                cell_bold=bool(cell.get("is_bold")),
                cell_italic=bool(cell.get("is_italic")),
                cell_underline=bool(cell.get("is_underlined")),
            )
        add_row_underline()
        y -= row_line_step * row_line_count

    def meeting_rows(field_name: str, meeting_name: str) -> list[dict]:
        selected_field = _match_key(field_name)
        selected_meeting = _match_key(meeting_name)
        for section in meeting_preview_sections:
            meeting_key = _match_key(section.get("meeting"))
            field_key = _match_key(section.get("field"))
            meeting_matches = (
                meeting_key == selected_meeting
                or meeting_key.startswith(selected_meeting)
                or selected_meeting.startswith(meeting_key)
            )
            field_matches = (
                field_key == selected_field
                or (selected_field and field_key.startswith(selected_field))
                or (field_key and selected_field.startswith(field_key))
            )
            if meeting_matches and field_matches:
                return list(section.get("rows") or [])
        for section in meeting_preview_sections:
            meeting_key = _match_key(section.get("meeting"))
            if meeting_key == selected_meeting or meeting_key.startswith(selected_meeting) or selected_meeting.startswith(meeting_key):
                return list(section.get("rows") or [])
        return []

    meeting_row_count_kwargs = {
        "content_width": content_width,
        "base_font_size": font_size,
        "font_family": font_family,
    }

    def estimate_contact_block_height(contact: dict[str, Any]) -> float:
        phones = list(contact.get("phones") or [])
        addresses = list(contact.get("address_entries") or contact.get("addresses") or [])
        name = _clean(contact.get("name"))
        phone_x, phone_code_x, main_text_x, continuation_x, name_max_width = _contact_text_columns(
            margin_left, page_width, margin_right, font_size, tw
        )
        space_width = tw(" ", font_size)
        # Addresses never use the phone column, even when right-aligned.
        address_max_width = max(24, page_width - margin_right - (main_text_x + space_width))
        address_line_count = 0
        for address in addresses:
            entry = address if isinstance(address, dict) else {"text": address}
            address_text = _clean(entry.get("text"))
            address_line_count += len(
                address_display_lines(
                    address_text,
                    street_address=_clean(entry.get("street_address")),
                    extended_address=_clean(entry.get("extended_address")),
                    city=_clean(entry.get("city")),
                    region=_clean(entry.get("region")),
                    postal_code=_clean(entry.get("postal_code")),
                    label=_clean(entry.get("address_type")),
                    max_width=address_max_width,
                    text_width=lambda value, size=font_size: tw(value, size),
                )
            )
        name_lines = contact_name_lines(contact, name, name_max_width)
        phones_drawn = min(len(phones), len(name_lines))
        extra_rows = max(0, len(phones) - phones_drawn, address_line_count)
        return (len(name_lines) + extra_rows + 0.25) * line_step

    def estimate_bible_block_height(field_name: str, meeting_name: str) -> float:
        if not include_bible_study:
            return 0.0
        rows = meeting_rows(field_name, meeting_name)
        if not rows:
            return 0.0
        total = line_step
        for row in rows:
            row_font_size = _meeting_row_font_size_pt(row, font_size)
            row_line_step = row_font_size * _meeting_row_line_height(row, current_meeting_line_height)
            total += _meeting_row_line_count(row, **meeting_row_count_kwargs) * row_line_step
        return total + line_step

    def estimate_complete_meeting_height(meeting: dict[str, Any], field_name: str, meeting_name: str) -> float:
        height = line_step * 4
        for contact in meeting.get("contacts") or []:
            height += estimate_contact_block_height(contact)
        height += estimate_bible_block_height(field_name, meeting_name)
        return height

    meeting_separator_lines = 2

    def start_field_separator_page(field_name: str, field_meetings: list[dict]) -> None:
        nonlocal y
        apply_meeting_spacing(None)
        if _page_content_lines():
            previous_field_name = (
                _clean(ordered_fields[field_index - 1].get("name")) if field_index else field_name
            )
            add_footer(previous_field_name)
            new_page()
        _, meeting_size = _heading_font_sizes(
            font_size,
            bool(_clean(paper_book_binding)),
            layout.get("title_font_size_pt"),
            layout.get("toc_font_size_pt"),
        )
        field_title_size = meeting_size
        title_step = field_title_size * max(0.8, float(layout.get("line_height") or 1.2))
        meeting_step = meeting_size * max(0.8, float(layout.get("line_height") or 1.2))
        frame_left = margin_left + _PAGE_FRAME_SIDE_INSET_PT
        frame_right = margin_right + _PAGE_FRAME_SIDE_INSET_PT
        frame_bottom = margin_bottom + _PAGE_FRAME_BOTTOM_INSET_PT
        frame_width = max(24.0, page_width - frame_left - frame_right)
        right_edge = page_width - frame_right
        elder_x = frame_left + meeting_size * 1.6
        name_max_width = frame_width

        rows: list[tuple[str, str, str]] = []
        for meeting in field_meetings:
            if _is_field_end_list_meeting(meeting):
                continue
            meeting_name = _meeting_base_name(_clean(meeting.get("name")), meeting.get("home_last_name"))
            if not meeting_name:
                continue
            elder = _separator_elder_contact(meeting)
            rows.append((meeting_name, _separator_elder_name(elder), _separator_phone_text(elder)))

        def title_baseline() -> float:
            title_from_top = max(_FIELD_SEPARATOR_TITLE_INSET_PT, margin_top + _PAGE_FRAME_SIDE_INSET_PT)
            return page_height - title_from_top - field_title_size

        def draw_separator_title(continued: bool = False) -> None:
            nonlocal y
            y = title_baseline()
            title_text = f"{field_name} (continued)" if continued else field_name
            for line_text in _wrap_text_lines(title_text, field_title_size, frame_width, font_family):
                x = max(frame_left, (page_width - tw(line_text, field_title_size)) / 2)
                page.lines.append(_PdfLine(text=line_text, x=x, y=y, size=field_title_size, bold=True))
                y -= title_step
            y -= title_step * 0.55

        def finish_separator_leaf() -> None:
            add_footer(field_name, framed=True)
            _draw_page_margin_frame(
                page,
                margin_left=margin_left,
                margin_right=margin_right,
                margin_top=margin_top,
                margin_bottom=margin_bottom,
            )

        def start_overflow_page() -> None:
            finish_separator_leaf()
            new_page()
            draw_separator_title(continued=True)

        draw_separator_title()
        for meeting_name, elder_name, phone_text in rows:
            name_lines = _wrap_text_lines(meeting_name, meeting_size, name_max_width, font_family) or [meeting_name]
            needed = meeting_step * (len(name_lines) + (1 if elder_name or phone_text else 0))
            if y - needed < frame_bottom:
                start_overflow_page()
            for index, line_text in enumerate(name_lines):
                if index and y - meeting_step < frame_bottom:
                    start_overflow_page()
                page.lines.append(_PdfLine(text=line_text, x=frame_left, y=y, size=meeting_size, bold=True))
                y -= meeting_step
            if elder_name or phone_text:
                if y - meeting_step < frame_bottom:
                    start_overflow_page()
                fitted_phone = phone_text
                phone_x = right_edge - tw(fitted_phone, meeting_size) if fitted_phone else right_edge
                fitted_elder = elder_name
                if fitted_elder:
                    elder_max = max(24.0, phone_x - elder_x - tw(" ", meeting_size))
                    if tw(fitted_elder, meeting_size) > elder_max:
                        fitted_elder = _fit_text(fitted_elder, meeting_size, elder_max, font_family)
                    page.lines.append(_PdfLine(text=fitted_elder, x=elder_x, y=y, size=meeting_size))
                    if fitted_phone:
                        _append_dot_leaders(
                            page,
                            elder_x + tw(fitted_elder, meeting_size),
                            phone_x,
                            y,
                            meeting_size,
                            font_family,
                        )
                elif fitted_phone:
                    _append_dot_leaders(page, elder_x, phone_x, y, meeting_size, font_family)
                if fitted_phone:
                    page.lines.append(_PdfLine(text=fitted_phone, x=phone_x, y=y, size=meeting_size))
                y -= meeting_step
            y -= meeting_step * 0.25
        finish_separator_leaf()
        new_page()

    for field_index, field in enumerate(ordered_fields):
        field_name = _clean(field.get("name"))
        meetings = list(field.get("meetings") or [])
        # Complete mtgs/page may pack multiple meetings from the same field onto one
        # page, but a new field always starts on a fresh page.
        if field_separator_pages:
            start_field_separator_page(field_name, meetings)
        elif pack_complete_meetings and field_index > 0 and _page_content_lines():
            previous_field_name = _clean(ordered_fields[field_index - 1].get("name"))
            add_footer(previous_field_name)
            new_page()
        for meeting_index, meeting in enumerate(meetings):
            apply_meeting_spacing(meeting)
            meeting_name = _clean(meeting.get("name"))
            heading = _meeting_heading(meeting_name, meeting.get("home_last_name"))
            is_field_end_list = _is_field_end_list_meeting(meeting)
            is_last_meeting = meeting_index == len(meetings) - 1 and field is ordered_fields[-1]
            if pack_complete_meetings and _page_content_lines():
                needed_height = (
                    estimate_complete_meeting_height(meeting, field_name, meeting_name)
                    + (line_step * meeting_separator_lines)
                )
                if y - needed_height < margin_bottom:
                    add_footer(field_name)
                    new_page()
                else:
                    y -= line_step * meeting_separator_lines
            ensure_space(4, field_name, heading)
            if meeting_is_unlisted_in_toc(field_name, meeting_name, unlisted_toc_meetings):
                current_toc_entry = None
            else:
                current_toc_entry = _TocEntry(field_name=field_name, meeting_heading=heading, page=page_number)
                toc_entries.append(current_toc_entry)
            heading_line = add_center(heading, font_size + 1, bold=True)
            if current_toc_entry:
                current_toc_entry.heading_lines.append(heading_line)
            add_rule()
            for contact in meeting.get("contacts", []):
                phones = list(contact.get("phones") or [])
                addresses = list(contact.get("address_entries") or contact.get("addresses") or [])
                name = _clean(contact.get("name"))
                phone_x, phone_code_x, main_text_x, continuation_x, name_max_width = _contact_text_columns(
                    margin_left, page_width, margin_right, font_size, tw
                )
                space_width = tw(" ", font_size)
                # Addresses never use the phone column, even when right-aligned.
                address_max_width = max(24, page_width - margin_right - (main_text_x + space_width))
                address_lines: list[tuple[str, str, bool]] = []
                for address in addresses:
                    entry = address if isinstance(address, dict) else {"text": address}
                    address_text = _clean(entry.get("text"))
                    coordinates = _clean(entry.get("coordinates"))
                    has_coordinates = bool(entry.get("has_coordinates") or coordinates)
                    uri = _map_url(provider, coordinates, address_text)
                    for line in address_display_lines(
                        address_text,
                        street_address=_clean(entry.get("street_address")),
                        extended_address=_clean(entry.get("extended_address")),
                        city=_clean(entry.get("city")),
                        region=_clean(entry.get("region")),
                        postal_code=_clean(entry.get("postal_code")),
                        label=_clean(entry.get("address_type")),
                        max_width=address_max_width,
                        text_width=lambda value, size=font_size: tw(value, size),
                    ):
                        address_lines.append((line, uri, has_coordinates))
                name_lines = contact_name_lines(contact, name, name_max_width)
                from app.services.field_list_service import print_book_note_display, print_book_note_runs, print_note_font_size

                def note_run_size(run: dict) -> float:
                    return print_note_font_size(font_size, run)

                def note_runs_width(runs: list[dict]) -> float:
                    return sum(
                        _text_width(
                            str(run.get("text") or ""),
                            note_run_size(run),
                            font_family,
                            bold=bool(run.get("bold")),
                        )
                        for run in runs
                    )

                def wrapped_note_runs(value: object) -> list[dict]:
                    body = print_book_note_runs(value)
                    if not body:
                        return []
                    wrapper = {"text": "", "bold": False, "italic": False, "underline": False, "size_delta": 0.0}
                    return [{**wrapper, "text": "("}, *body, {**wrapper, "text": ")"}]

                def add_note_runs(runs: list[dict], x: float) -> None:
                    cursor = x
                    for run in runs:
                        text = str(run.get("text") or "")
                        if not text:
                            continue
                        size = note_run_size(run)
                        width = _text_width(text, size, font_family, bold=bool(run.get("bold")))
                        is_bold = bool(run.get("bold"))
                        add_text(text, cursor, size=size, bold=is_bold, italic=bool(run.get("italic")) and not is_bold)
                        if run.get("underline"):
                            rule_y = y - (size * 0.18)
                            page.rules.append(_PdfRule(cursor, rule_y, cursor + width, rule_y, 0.35))
                        cursor += width

                pb_values = [
                    item
                    for item in (contact.get("print_book_name") or [])
                    if print_book_note_display(item).strip()
                ]
                pa_values = [
                    item
                    for item in (contact.get("print_after_address") or [])
                    if print_book_note_display(item).strip()
                ]
                pb_text = " ".join(f"({print_book_note_display(value)})" for value in pb_values)
                pb_inline_runs: list[dict] = []
                for index, value in enumerate(pb_values):
                    if index:
                        pb_inline_runs.append(
                            {"text": " ", "bold": False, "italic": False, "underline": False, "size_delta": 0.0}
                        )
                    pb_inline_runs.extend(wrapped_note_runs(value))
                pb_inline_text = ""
                pb_own_line = ""
                pb_own_runs: list[dict] = []
                if pb_text:
                    first_name_line = name_lines[0] if name_lines else ""
                    fits_on_name_line = bool(name_lines) and (
                        main_text_x + tw(first_name_line, font_size) + tw(" ", font_size) + note_runs_width(pb_inline_runs)
                        <= page_width - margin_right
                    )
                    if fits_on_name_line:
                        pb_inline_text = pb_text
                    else:
                        pb_own_line = pb_text
                        pb_own_runs = list(pb_inline_runs)
                phones_in_names = min(len(name_lines), len(phones))
                below_count = (1 if pb_own_line else 0) + len(address_lines) + len(pa_values)
                continuation_lines = max(0, len(phones) - phones_in_names, below_count)
                row_lines = len(name_lines) + continuation_lines
                ensure_space(row_lines + 1, field_name, heading)
                index_name = _index_contact_name(name)
                if index_name:
                    index_entries.append(_IndexEntry(name=index_name, page=page_number, target_y=min(page_height, y + (font_size * 2.4))))

                def draw_phone(phone_item: object, *, use_full_custom_label: bool = False) -> None:
                    phone_value = _clean(phone_item.get("value") if isinstance(phone_item, dict) else phone_item)
                    digits = _phone_digits(phone_value)
                    if len(digits) == 10:
                        call_text = f"{digits[:3]}-{digits[3:6]}"
                        text_text = digits[6:]
                        # Use hierarchical forms (tel:// / sms://). Plain "sms:digits" is
                        # treated as a relative file path by macOS Preview and shows
                        # "File sms:… not found" instead of opening Messages.
                        page.lines.append(_PdfLine(call_text, phone_x, y, font_size, uri=f"tel://{digits}", width=tw(call_text, font_size)))
                        page.lines.append(_PdfLine("-", phone_x + tw(call_text, font_size), y, font_size))
                        page.lines.append(_PdfLine(text_text, phone_x + tw(f"{call_text}-", font_size), y, font_size, uri=f"sms://{digits}", width=tw(text_text, font_size)))
                    else:
                        add_text(phone_value, phone_x)
                    custom_label = _clean(phone_item.get("custom_label") if isinstance(phone_item, dict) else "")
                    phone_code = custom_label if use_full_custom_label and custom_label else _clean(phone_item.get("code") if isinstance(phone_item, dict) else "")
                    add_text(phone_code, phone_code_x)

                phones_drawn = 0
                for index, line in enumerate(name_lines):
                    if index > 0:
                        y -= line_step
                    if index < len(phones):
                        draw_phone(phones[index], use_full_custom_label=(index > 0))
                        phones_drawn += 1
                    line_x = main_text_x if index == 0 else continuation_x
                    add_contact_name_line(line, line_x, bold_last_name=(index == 0))
                    if index == 0 and pb_inline_text:
                        add_note_runs(pb_inline_runs, line_x + tw(line, font_size) + space_width)
                y -= line_step
                # Lines rendered below the name share rows with any remaining phones,
                # so the note never pushes a phone onto a line of its own. The pb note
                # (when it did not fit on the name line) comes first, then the address
                # lines. Both follow the address alignment.
                below_lines: list[tuple[str, str, str, bool, list]] = []
                if pb_own_line:
                    below_lines.append(("pb", pb_own_line, "", False, pb_own_runs))
                for address_line, address_uri, address_has_coordinates in address_lines:
                    below_lines.append(("addr", address_line, address_uri, address_has_coordinates, []))
                for pa_value in pa_values:
                    below_lines.append(("pa", print_book_note_display(pa_value), "", False, wrapped_note_runs(pa_value)))
                for line_index in range(max(0, len(phones) - phones_drawn, len(below_lines))):
                    ensure_space(1, field_name, heading)
                    phone_index = phones_drawn + line_index
                    if phone_index < len(phones):
                        draw_phone(phones[phone_index], use_full_custom_label=True)
                    if line_index < len(below_lines):
                        kind, line, uri, has_coordinates, note_runs = below_lines[line_index]
                        # Never draw address text under the phone column.
                        min_address_x = main_text_x + space_width
                        x = min_address_x
                        if address_align_right:
                            line_width = note_runs_width(note_runs) if kind in ("pb", "pa") else tw(line, font_size)
                            x = max(
                                min_address_x,
                                page_width - margin_right - line_width,
                            )
                        if kind in ("pb", "pa"):
                            add_note_runs(note_runs, x)
                        else:
                            add_text(line, x, uri=uri, italic=address_italic and not has_coordinates)
                    y -= line_step
                y -= line_step * 0.25
            bible_rows = meeting_rows(field_name, meeting_name) if include_bible_study else []
            if bible_rows:
                bible_chunks = _meeting_row_chunks(bible_rows)
                first_chunk = bible_chunks[0] if bible_chunks else []
                first_chunk_lines = sum(
                    _meeting_row_line_count(row, **meeting_row_count_kwargs) for row in first_chunk
                )
                first_chunk_unassociated = (not is_field_end_list) and any(
                    bool(row.get("is_table_start_row")) and bool(row.get("no_meeting_association"))
                    for row in first_chunk
                )
                first_chunk_print_new_page = any(
                    bool(row.get("is_table_start_row")) and bool(row.get("print_new_page"))
                    for row in first_chunk
                )
                first_chunk_is_table = chunk_is_table_chunk(first_chunk)
                if first_chunk_print_new_page and not first_chunk_unassociated:
                    start_print_new_page_for_table()
                if not first_chunk_unassociated:
                    if first_chunk_is_table and first_chunk:
                        first_row_lines = _meeting_row_line_count(first_chunk[0], **meeting_row_count_kwargs) + 1
                        ensure_space(first_row_lines, field_name, heading)
                    else:
                        ensure_space(first_chunk_lines + 1, field_name, heading)
                    y -= line_step
                for chunk in bible_chunks:
                    chunk_lines = sum(_meeting_row_line_count(row, **meeting_row_count_kwargs) for row in chunk)
                    no_meeting_association = (not is_field_end_list) and any(
                        bool(row.get("is_table_start_row")) and bool(row.get("no_meeting_association"))
                        for row in chunk
                    )
                    print_new_page = any(
                        bool(row.get("is_table_start_row")) and bool(row.get("print_new_page"))
                        for row in chunk
                    )
                    is_table_chunk = chunk_is_table_chunk(chunk)
                    keep_together = any(_clean(row.get("keep_together_group")) for row in chunk)
                    if no_meeting_association:
                        start_standalone_table_page()
                    elif print_new_page:
                        start_print_new_page_for_table()
                    if is_table_chunk:
                        ensure_table_chunk_space(
                            chunk,
                            field_name,
                            "" if no_meeting_association else heading,
                        )
                        for row in chunk:
                            add_meeting_preview_row(row)
                        continue
                    if keep_together:
                        ensure_space(chunk_lines, field_name, heading)
                        for row in chunk:
                            add_meeting_preview_row(row)
                        continue
                    for row in chunk:
                        row_lines = _meeting_row_line_count(row, **meeting_row_count_kwargs)
                        ensure_space(row_lines, field_name, heading)
                        add_meeting_preview_row(row)
            if pack_complete_meetings:
                if is_last_meeting:
                    add_footer(field_name)
            else:
                add_footer(field_name)
                if not is_last_meeting:
                    new_page()

    if not writer.pages:
        writer.add_page()
    content_page_count = len(writer.pages)
    cover_image = _cover_image_from_data_url(cover_image_data, cover_image_width, cover_image_height)
    front_page_count = 1 if cover_image else 0
    insert_pages = _build_insert_pages(
        include_insert_file=include_insert_file,
        insert_file_data=insert_file_data,
        insert_file_mime=insert_file_mime,
        insert_file_width=insert_file_width,
        insert_file_height=insert_file_height,
        page_width=page_width,
        page_height=page_height,
        insert_file_crop=insert_file_crop,
        margin_left=margin_left,
        margin_right=margin_right,
        margin_top=margin_top,
        margin_bottom=margin_bottom,
    )
    insert_page_count = len(insert_pages)
    compact_toc_index = bool(_clean(paper_book_binding))
    index_toc_entry = (
        _TocEntry(field_name="", meeting_heading="Index", page=content_page_count + 1, is_heading_only=True)
        if index_entries
        else None
    )
    all_toc_entries = [*toc_entries, *([index_toc_entry] if index_toc_entry else [])]
    if all_toc_entries:
        toc_pages: list[_PdfPage] = []
        toc_page_count = 0
        for _ in range(4):
            number_offset = toc_page_count
            toc_pages = _build_toc_pages(
                entries=all_toc_entries,
                book_title=str(layout.get("book_title") or "Address Book"),
                page_width=page_width,
                page_height=page_height,
                margin_left=margin_left,
                margin_right=margin_right,
                margin_top=margin_top,
                margin_bottom=margin_bottom,
                base_font_size=font_size,
                page_offset=number_offset,
                link_page_offset=number_offset + front_page_count + insert_page_count,
                compact=compact_toc_index,
                font_family=font_family,
                title_font_size=layout.get("title_font_size_pt"),
                toc_font_size=layout.get("toc_font_size_pt"),
            )
            if len(toc_pages) == toc_page_count:
                break
            toc_page_count = len(toc_pages)
        number_offset = len(toc_pages)
        _shift_content_footers(writer.pages, number_offset, font_family)
        toc_start_index = front_page_count + insert_page_count
        for entry in toc_entries:
            for heading_line in entry.heading_lines:
                heading_line.link_page_index = toc_start_index + entry.toc_page_index
        for entry in index_entries:
            entry.page += number_offset
        index_pages = _build_index_pages(
            entries=index_entries,
            page_width=page_width,
            page_height=page_height,
            margin_left=margin_left,
            margin_right=margin_right,
            margin_top=margin_top,
            margin_bottom=margin_bottom,
            base_font_size=font_size,
            start_page_number=number_offset + content_page_count + 1,
            title_link_page_index=toc_start_index + index_toc_entry.toc_page_index if index_toc_entry else None,
            link_page_offset=insert_page_count + front_page_count,
            compact=compact_toc_index,
            font_family=font_family,
            title_font_size=layout.get("title_font_size_pt"),
            toc_font_size=layout.get("toc_font_size_pt"),
            index_font_size=layout.get("index_font_size_pt"),
            two_column=double_column_index,
        )
        cover_pages: list[_PdfPage] = []
        if cover_image:
            image_data, image_width, image_height = cover_image
            cover_page = _PdfPage(page_width, page_height)
            cover_page.images.append(_PdfImage(image_data, image_width, image_height, 0, 0, page_width, page_height))
            if cover_title_enabled:
                try:
                    preview_font_size = min(max(float(cover_title_font_size or 30), 8.0), 120.0)
                except (TypeError, ValueError):
                    preview_font_size = 30.0
                try:
                    preview_width = max(float(cover_title_preview_width or 0), 1.0)
                except (TypeError, ValueError):
                    preview_width = 0.0
                title_size = preview_font_size
                if preview_width > 1:
                    title_size = preview_font_size * (page_width / preview_width)
                title_size = min(max(title_size, 4.0), 72.0)
                title_lines = _cover_title_lines(cover_title_text, title_size, page_width * 0.86)
                title_color = _pdf_color(cover_title_color)
                title_effect = _clean(cover_title_effect).lower()
                if title_effect not in {"outline", "shadow", "embossed", "engraved", "bevel", "none"}:
                    title_effect = "shadow"
                title_step = title_size * 1.18
                try:
                    title_x_ratio = min(max(float(cover_title_x), 0.05), 0.95)
                    title_y_ratio = min(max(float(cover_title_y), 0.05), 0.95)
                except (TypeError, ValueError):
                    title_x_ratio = 0.5
                    title_y_ratio = 0.24
                center_x = page_width * title_x_ratio
                center_y = page_height * (1.0 - title_y_ratio)
                title_y = center_y + (((len(title_lines[:6]) - 1) * title_step) / 2)
                for line_index, title_line in enumerate(title_lines[:6]):
                    line_width = tw(title_line, title_size)
                    cover_page.lines.append(
                        _PdfLine(
                            title_line,
                            x=min(max(0.0, center_x - (line_width / 2)), max(0.0, page_width - line_width)),
                            y=title_y - (line_index * title_step),
                            size=title_size,
                            bold=True,
                            color=title_color,
                            effect=title_effect,
                        )
                    )
            if cover_date_enabled and _clean(cover_date_text):
                try:
                    date_preview_font_size = min(max(float(cover_date_font_size or 18), 8.0), 120.0)
                except (TypeError, ValueError):
                    date_preview_font_size = 18.0
                try:
                    preview_width = max(float(cover_title_preview_width or 0), 1.0)
                except (TypeError, ValueError):
                    preview_width = 0.0
                date_size = date_preview_font_size
                if preview_width > 1:
                    date_size = date_preview_font_size * (page_width / preview_width)
                date_size = min(max(date_size, 4.0), 72.0)
                date_text = _fit_text(cover_date_text, date_size, page_width * 0.92)
                date_color = _pdf_color(cover_date_color)
                try:
                    date_x_ratio = min(max(float(cover_date_x), 0.05), 0.95)
                    date_y_ratio = min(max(float(cover_date_y), 0.05), 0.95)
                except (TypeError, ValueError):
                    date_x_ratio = 0.5
                    date_y_ratio = 0.82
                center_x = page_width * date_x_ratio
                center_y = page_height * (1.0 - date_y_ratio)
                line_width = tw(date_text, date_size)
                cover_page.lines.append(
                    _PdfLine(
                        date_text,
                        x=min(max(0.0, center_x - (line_width / 2)), max(0.0, page_width - line_width)),
                        y=center_y,
                        size=date_size,
                        bold=True,
                        color=date_color,
                    )
                )
            cover_pages.append(cover_page)
        writer.pages = cover_pages + insert_pages + toc_pages + writer.pages + index_pages
    if _clean(paper_book_binding).lower() == "side_spiral":
        _mirror_side_spiral_even_page_margins(writer.pages, margin_left, margin_right)
    if _clean(paper_book_binding):
        writer.pages = _impose_pages_to_letter(
            writer.pages,
            paper_book_binding,
            font_family,
            include_cut_marks=bool(paper_book_cut_marks),
        )
    return writer.bytes()
