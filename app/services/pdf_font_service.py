from __future__ import annotations

import os
import struct
import zlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.services.preset_service import FONT_FAMILY_OPTIONS

_FONT_FILE_CANDIDATES = {
    "Arial": {
        "regular": ("Arial.ttf", "arial.ttf", "ARIAL.TTF"),
        "bold": ("Arial Bold.ttf", "arialbd.ttf", "Arial-Bold.ttf", "ARIALBD.TTF"),
        "italic": ("Arial Italic.ttf", "ariali.ttf", "Arial-Italic.ttf", "ARIALI.TTF"),
    },
    "Helvetica": {
        "regular": ("Helvetica.ttf", "Helvetica.otf", "helvetica.ttf"),
        "bold": ("Helvetica Bold.ttf", "Helvetica-Bold.ttf", "helvetica-bold.ttf"),
        "italic": ("Helvetica Italic.ttf", "Helvetica-Oblique.ttf", "helvetica-oblique.ttf"),
    },
    "Verdana": {
        "regular": ("Verdana.ttf", "verdana.ttf", "VERDANA.TTF"),
        "bold": ("Verdana Bold.ttf", "verdanab.ttf", "Verdana-Bold.ttf", "VERDANAB.TTF"),
        "italic": ("Verdana Italic.ttf", "verdanai.ttf", "Verdana-Italic.ttf", "VERDANAI.TTF"),
    },
    "Tahoma": {
        "regular": ("Tahoma.ttf", "tahoma.ttf", "TAHOMA.TTF"),
        "bold": ("Tahoma Bold.ttf", "tahomabd.ttf", "Tahoma-Bold.ttf", "TAHOMABD.TTF"),
        "italic": ("Tahoma Italic.ttf", "tahomai.ttf", "Tahoma-Italic.ttf"),
    },
    "Times New Roman": {
        "regular": ("Times New Roman.ttf", "times.ttf", "timesnr.ttf", "TIMES.TTF"),
        "bold": ("Times New Roman Bold.ttf", "timesbd.ttf", "Times New Roman-Bold.ttf", "TIMESBD.TTF"),
        "italic": ("Times New Roman Italic.ttf", "timesi.ttf", "Times New Roman-Italic.ttf", "TIMESI.TTF"),
    },
    "Georgia": {
        "regular": ("Georgia.ttf", "georgia.ttf", "GEORGIA.TTF"),
        "bold": ("Georgia Bold.ttf", "georgiab.ttf", "Georgia-Bold.ttf", "GEORGIAB.TTF"),
        "italic": ("Georgia Italic.ttf", "georgiai.ttf", "Georgia-Italic.ttf", "GEORGIAI.TTF"),
    },
    "Courier New": {
        "regular": ("Courier New.ttf", "cour.ttf", "COUR.TTF"),
        "bold": ("Courier New Bold.ttf", "courbd.ttf", "Courier New-Bold.ttf", "COURBD.TTF"),
        "italic": ("Courier New Italic.ttf", "couri.ttf", "Courier New-Italic.ttf", "COURI.TTF"),
    },
}


@dataclass(frozen=True)
class _PdfFontBBox:
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class PdfFontFace:
    name: str
    buffer: bytes
    bbox: _PdfFontBBox
    flags: dict
    ascender: float
    descender: float
    italic_angle: float
    advances: dict[int, float]
    postscript_name: str = ""

    def text_length(self, text: str, fontsize: float) -> float:
        return sum(self.advances.get(ord(character), 0.5) for character in text) * float(fontsize)

    def glyph_advance(self, codepoint: int) -> float:
        return self.advances.get(int(codepoint), 0.5)


def _font_search_dirs() -> list[Path]:
    windir = Path(os.environ.get("WINDIR") or r"C:\Windows")
    return [
        Path("/System/Library/Fonts/Supplemental"),
        Path("/System/Library/Fonts"),
        Path("/Library/Fonts"),
        Path.home() / "Library" / "Fonts",
        windir / "Fonts",
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
    ]


def _find_font_file(filenames: tuple[str, ...]) -> Path | None:
    wanted = {name.lower(): name for name in filenames}
    for folder in _font_search_dirs():
        try:
            if not folder.is_dir():
                continue
            available = {item.name.lower(): item for item in folder.iterdir() if item.is_file()}
        except OSError:
            continue
        for key in wanted:
            match = available.get(key)
            if match and match.suffix.lower() in {".ttf", ".otf"}:
                return match
    return None


@lru_cache(maxsize=32)
def resolve_pdf_font_path(font_family: str, style: str = "regular") -> str | None:
    family = str(font_family or "").strip()
    if family not in FONT_FAMILY_OPTIONS:
        family = "Arial"
    faces = _FONT_FILE_CANDIDATES.get(family) or {}
    filenames = faces.get(style) or faces.get("regular") or ()
    path = _find_font_file(tuple(filenames))
    return str(path) if path else None


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from(">H", data, offset)[0]


def _i16(data: bytes, offset: int) -> int:
    return struct.unpack_from(">h", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def _ttf_tables(data: bytes) -> dict[str, bytes] | None:
    if len(data) < 12 or data[:4] not in {b"\x00\x01\x00\x00", b"true", b"OTTO"}:
        return None
    count = _u16(data, 4)
    tables: dict[str, bytes] = {}
    for index in range(count):
        record = 12 + index * 16
        if record + 16 > len(data):
            return None
        tag = data[record : record + 4].decode("latin-1")
        offset = _u32(data, record + 8)
        length = _u32(data, record + 12)
        if offset + length > len(data):
            return None
        tables[tag] = data[offset : offset + length]
    return tables


def _cmap_format4(table: bytes) -> dict[int, int]:
    if len(table) < 16:
        return {}
    seg_count = _u16(table, 6) // 2
    end_start = 14
    reserved = end_start + seg_count * 2
    start_start = reserved + 2
    delta_start = start_start + seg_count * 2
    range_start = delta_start + seg_count * 2
    if range_start + seg_count * 2 > len(table):
        return {}
    mapping: dict[int, int] = {}
    for index in range(seg_count):
        start = _u16(table, start_start + index * 2)
        end = _u16(table, end_start + index * 2)
        delta = _i16(table, delta_start + index * 2)
        range_offset = _u16(table, range_start + index * 2)
        for code in range(start, end + 1):
            if range_offset == 0:
                glyph = (code + delta) & 0xFFFF
            else:
                glyph_offset = range_start + index * 2 + range_offset + (code - start) * 2
                if glyph_offset + 2 > len(table):
                    continue
                glyph_id = _u16(table, glyph_offset)
                glyph = (glyph_id + delta) & 0xFFFF if glyph_id else 0
            if glyph:
                mapping[code] = glyph
    return mapping


def _character_to_glyph(cmap: bytes) -> dict[int, int]:
    if len(cmap) < 4:
        return {}
    count = _u16(cmap, 2)
    chosen: bytes | None = None
    for index in range(count):
        record = 4 + index * 8
        if record + 8 > len(cmap):
            break
        platform = _u16(cmap, record)
        encoding = _u16(cmap, record + 2)
        offset = _u32(cmap, record + 4)
        if offset >= len(cmap):
            continue
        table = cmap[offset:]
        if len(table) < 2 or _u16(table, 0) != 4:
            continue
        if (platform, encoding) in {(3, 1), (0, 3), (3, 0), (0, 4)}:
            chosen = table
            break
        if chosen is None:
            chosen = table
    return _cmap_format4(chosen) if chosen else {}


def _ttf_name(name_table: bytes, name_id: int) -> str:
    if len(name_table) < 6:
        return ""
    count = _u16(name_table, 2)
    storage = _u16(name_table, 4)
    best = ""
    for index in range(count):
        record = 6 + index * 12
        if record + 12 > len(name_table):
            break
        platform, encoding, _language, nid, length, offset = struct.unpack_from(">HHHHHH", name_table, record)
        if nid != name_id:
            continue
        start = storage + offset
        raw = name_table[start : start + length]
        if not raw:
            continue
        if platform in {0, 3}:
            text = raw.decode("utf-16-be", errors="ignore")
        else:
            text = raw.decode("mac_roman", errors="ignore")
        text = "".join(character for character in text if character.isprintable()).strip()
        if not text:
            continue
        if platform == 3 and encoding == 1:
            return text
        if not best:
            best = text
    return best


def _pdf_safe_font_name(value: str, fallback: str) -> str:
    cleaned = "".join(character for character in value if character.isalnum() or character in "-")
    return cleaned or fallback


def _glyph_advances(hmtx: bytes, number_of_h_metrics: int, units_per_em: int) -> list[float]:
    if number_of_h_metrics < 1 or units_per_em < 1:
        return []
    needed = number_of_h_metrics * 4
    if len(hmtx) < needed:
        return []
    widths = [_u16(hmtx, index * 4) / units_per_em for index in range(number_of_h_metrics)]
    extra = max(0, (len(hmtx) - needed) // 2)
    widths.extend([widths[-1]] * extra)
    return widths


def _parse_ttf_face(path: str, style: str) -> PdfFontFace | None:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if data[:4] == b"ttcf":
        return None
    tables = _ttf_tables(data)
    if not tables or "head" not in tables or "hhea" not in tables or "hmtx" not in tables:
        return None
    head = tables["head"]
    hhea = tables["hhea"]
    if len(head) < 44 or len(hhea) < 36:
        return None
    units_per_em = _u16(head, 18) or 1000
    bbox = _PdfFontBBox(
        x0=_i16(head, 36) / units_per_em,
        y0=_i16(head, 38) / units_per_em,
        x1=_i16(head, 40) / units_per_em,
        y1=_i16(head, 42) / units_per_em,
    )
    ascender = _i16(hhea, 4) / units_per_em
    descender = _i16(hhea, 6) / units_per_em
    number_of_h_metrics = _u16(hhea, 34)
    glyph_widths = _glyph_advances(tables["hmtx"], number_of_h_metrics, units_per_em)
    mapping = _character_to_glyph(tables.get("cmap") or b"")
    advances = {}
    fallback = glyph_widths[0] if glyph_widths else 0.5
    for code in range(32, 256):
        glyph = mapping.get(code, 0)
        if 0 <= glyph < len(glyph_widths):
            advances[code] = glyph_widths[glyph]
        else:
            advances[code] = fallback
    italic_angle = 0.0
    post = tables.get("post")
    if post and len(post) >= 8:
        italic_angle = struct.unpack_from(">l", post, 4)[0] / 65536.0
    os2 = tables.get("OS/2") or b""
    weight = _u16(os2, 4) if len(os2) >= 6 else 400
    selection = _u16(os2, 62) if len(os2) >= 64 else 0
    flags = {
        "mono": False,
        "serif": False,
        "italic": style == "italic" or bool(selection & 0x01) or italic_angle < 0,
        "bold": style == "bold" or weight >= 600 or bool(selection & 0x20),
    }
    fallback_name = "".join(character for character in Path(path).stem if character.isalnum()) or "BookFont"
    postscript_name = _pdf_safe_font_name(_ttf_name(tables.get("name") or b"", 6), fallback_name)
    return PdfFontFace(
        name=Path(path).stem,
        buffer=data,
        bbox=bbox,
        flags=flags,
        ascender=ascender,
        descender=descender,
        italic_angle=italic_angle,
        advances=advances,
        postscript_name=postscript_name,
    )


@lru_cache(maxsize=32)
def load_pdf_font(font_family: str, style: str = "regular") -> PdfFontFace | None:
    path = resolve_pdf_font_path(font_family, style)
    if not path:
        return None
    return _parse_ttf_face(path, style)


def pdf_text_width(text: str, size: float, font_family: str, *, bold: bool = False) -> float | None:
    font = load_pdf_font(font_family, "bold" if bold else "regular")
    if font is None:
        return None
    try:
        return float(font.text_length(text, fontsize=size))
    except Exception:
        return None


def _pdf_font_name(font_family: str, style: str, font: PdfFontFace | None = None) -> str:
    if font and font.postscript_name:
        return font.postscript_name
    base = "".join(ch for ch in str(font_family) if ch.isalnum()) or "BookFont"
    suffix = {"regular": "", "bold": "Bold", "italic": "Italic"}.get(style, "")
    return f"{base}{suffix}" if suffix else base


def pdf_font_resource_name(font_family: str, style: str = "regular") -> str:
    return _pdf_font_name(font_family, style, load_pdf_font(font_family, style))


def add_embedded_pdf_font(add_object, font, font_family: str, style: str) -> int | None:
    if font is None or not getattr(font, "buffer", None) or font.buffer[:4] == b"ttcf":
        return None
    data = bytes(font.buffer)
    compressed = zlib.compress(data, 9)
    fontfile_ref = add_object(
        (
            f"<< /Length {len(compressed)} /Length1 {len(data)} /Filter /FlateDecode >>\nstream\n"
        ).encode("latin-1")
        + compressed
        + b"\nendstream"
    )
    pdf_name = _pdf_font_name(font_family, style, font)
    bbox = font.bbox
    font_bbox = " ".join(
        str(int(round(value * 1000)))
        for value in (bbox.x0, bbox.y0, bbox.x1, bbox.y1)
    )
    flags = 32
    font_flags = font.flags if isinstance(font.flags, dict) else {}
    if font_flags.get("mono"):
        flags |= 1
    if font_flags.get("serif"):
        flags |= 2
    if style == "italic" or font_flags.get("italic"):
        flags |= 64
    ascent = int(round(float(font.ascender) * 1000))
    descent = int(round(float(font.descender) * 1000))
    stem_v = 120 if style == "bold" or font_flags.get("bold") else 80
    italic_angle = int(round(getattr(font, "italic_angle", -12 if style == "italic" else 0)))
    descriptor_ref = add_object(
        "<< /Type /FontDescriptor "
        f"/FontName /{pdf_name} /Flags {flags} /FontBBox [{font_bbox}] "
        f"/ItalicAngle {italic_angle} /Ascent {ascent} /Descent {descent} "
        f"/CapHeight {max(ascent, 0)} /StemV {stem_v} /FontFile2 {fontfile_ref} 0 R >>"
    )
    widths = []
    for code in range(32, 256):
        character = bytes([code]).decode("latin-1")
        try:
            widths.append(str(int(round(font.glyph_advance(ord(character)) * 1000))))
        except Exception:
            widths.append("500")
    return add_object(
        "<< /Type /Font /Subtype /TrueType "
        f"/BaseFont /{pdf_name} /Encoding /WinAnsiEncoding "
        f"/FirstChar 32 /LastChar 255 /Widths [{' '.join(widths)}] "
        f"/FontDescriptor {descriptor_ref} 0 R >>"
    )


def add_embedded_pdf_font_set(add_object, font_family: str) -> tuple[int, int, int] | None:
    regular = load_pdf_font(font_family, "regular")
    if regular is None:
        return None
    bold = load_pdf_font(font_family, "bold") or regular
    italic = load_pdf_font(font_family, "italic") or regular
    regular_ref = add_embedded_pdf_font(add_object, regular, font_family, "regular")
    if regular_ref is None:
        return None
    bold_ref = add_embedded_pdf_font(add_object, bold, font_family, "bold") or regular_ref
    italic_ref = add_embedded_pdf_font(add_object, italic, font_family, "italic") or regular_ref
    return regular_ref, bold_ref, italic_ref
