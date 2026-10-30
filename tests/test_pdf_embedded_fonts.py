import unittest
from unittest.mock import patch

import fitz

from app.services.address_book_pdf_service import (
    _PdfImage,
    _PdfLine,
    _PdfWriter,
    _contact_text_columns,
    _text_width,
    build_address_book_pdf,
)
from app.services.pdf_font_service import load_pdf_font, resolve_pdf_font_path


def _pdf_font_names(pdf: bytes) -> set[str]:
    document = fitz.open(stream=pdf, filetype="pdf")
    try:
        names = set()
        for page in document:
            for font in page.get_fonts(full=True):
                names.add(str(font[3] or ""))
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        if span.get("text", "").strip():
                            names.add(str(span.get("font") or ""))
        return names
    finally:
        document.close()


class PdfEmbeddedFontTests(unittest.TestCase):
    def test_system_verdana_is_embedded_as_truetype(self) -> None:
        if not resolve_pdf_font_path("Verdana", "regular"):
            self.skipTest("Verdana is not installed on this computer")
        writer = _PdfWriter(200, 200, font_family="Verdana")
        page = writer.add_page()
        page.lines.append(_PdfLine("Verdana Sample", x=20, y=100, size=12))
        pdf = writer.bytes()
        names = " ".join(_pdf_font_names(pdf)).lower()
        self.assertIn("verdana", names)
        self.assertNotIn("helvetica", names)
        self.assertNotIn("arial", names)
        self.assertIn(b"/FontFile2", pdf)
        self.assertIn(b"/BaseFont/Verdana", pdf)
        self.assertNotIn(b"/CFSRegular", pdf)
        self.assertIn(b"/WinAnsiEncoding", pdf)
        self.assertIn(b"/ToUnicode", pdf)
        self.assertNotIn(b"/Verdana#20", pdf)
        document = fitz.open(stream=pdf, filetype="pdf")
        try:
            self.assertFalse(document.is_encrypted)
            self.assertFalse(document.needs_pass)
            self.assertTrue(document.permissions & fitz.PDF_PERM_MODIFY)
            self.assertTrue(document.permissions & fitz.PDF_PERM_ANNOTATE)
            self.assertTrue(document.permissions & fitz.PDF_PERM_ASSEMBLE)
            self.assertEqual(document.metadata.get("creator"), "ContactsFreeShare")
            self.assertEqual(document.metadata.get("producer"), "ContactsFreeShare")
        finally:
            document.close()

    def test_verdana_contact_name_wraps_like_the_html_preview(self) -> None:
        if not resolve_pdf_font_path("Verdana", "regular"):
            self.skipTest("Verdana is not installed on this computer")
        def tw(text: str, size: float) -> float:
            return _text_width(text, size, "Verdana")

        _phone_x, _code_x, _main_x, _cont_x, name_max = _contact_text_columns(
            0.14 * 72, 3.5 * 72, 0.14 * 72, 7, tw
        )
        full = "GRAHAM, Mark & Stephanie; Ruth, Harrison, Extra"
        head = "GRAHAM, Mark & Stephanie;"
        self.assertGreater(tw(full, 7), name_max)
        self.assertLessEqual(tw(head, 7), name_max)

    def test_address_book_pdf_keeps_verdana_instead_of_arial(self) -> None:
        if not resolve_pdf_font_path("Verdana", "regular"):
            self.skipTest("Verdana is not installed on this computer")
        with patch(
            "app.services.meeting_toc_service.unlisted_toc_meeting_keys",
            return_value=set(),
        ):
            pdf = build_address_book_pdf(
                fields=[
                    {
                        "name": "Field A",
                        "meetings": [
                            {
                                "name": "Meeting 1",
                                "contacts": [
                                    {
                                        "name": "DOE, John",
                                        "phones": [{"value": "5551234567", "code": "HM"}],
                                        "addresses": [],
                                        "children": [],
                                        "other_relationships": [],
                                    }
                                ],
                            }
                        ],
                    }
                ],
                meeting_preview_sections=[],
                settings={
                    "address_map_provider": "google",
                    "address_align": "right",
                    "include_bible_study_union_info": 0,
                    "meeting_page_mode": "one_meeting",
                },
                layout={
                    "trim_width_in": 3.5,
                    "trim_height_in": 5.5,
                    "margin_left_in": 0.14,
                    "margin_right_in": 0.14,
                    "margin_top_in": 0.14,
                    "margin_bottom_in": 0.14,
                    "base_font_size_pt": 7,
                    "line_height": 1.2,
                    "font_family": "Verdana",
                    "book_title": "Verdana Book",
                },
                print_order_mode="alphabetical",
                print_order_json="",
            )
        names = " ".join(_pdf_font_names(pdf)).lower()
        self.assertIn("verdana", names)
        self.assertNotIn("helvetica", names)
        self.assertNotIn("arial", names)
        self.assertIn(b"/FontFile2", pdf)

    def test_helvetica_keeps_the_built_in_pdf_font_when_no_ttf_is_installed(self) -> None:
        if resolve_pdf_font_path("Helvetica", "regular"):
            self.skipTest("A Helvetica TTF is installed, so it would be embedded")
        writer = _PdfWriter(200, 200, font_family="Helvetica")
        page = writer.add_page()
        page.lines.append(_PdfLine("Helvetica Sample", x=20, y=100, size=12))
        pdf = writer.bytes()
        names = " ".join(_pdf_font_names(pdf)).lower()
        self.assertIn("helv", names)
        self.assertNotIn("verdana", names)
        self.assertNotIn(b"/FontFile2", pdf)

    def test_flate_rgb_insert_image_does_not_crash_the_writer(self) -> None:
        import zlib

        rgb = bytes([20, 80, 160]) * (8 * 10)
        writer = _PdfWriter(80, 100, font_family="Verdana")
        page = writer.add_page()
        page.images.append(
            _PdfImage(
                zlib.compress(rgb, 9),
                8,
                10,
                0,
                0,
                80,
                100,
                filter_name="FlateDecode",
            )
        )
        page.lines.append(_PdfLine("Verdana Sample", x=8, y=20, size=10))
        pdf = writer.bytes()
        document = fitz.open(stream=pdf, filetype="pdf")
        try:
            self.assertTrue(document[0].get_images())
            names = " ".join(_pdf_font_names(pdf)).lower()
            self.assertIn("verdana", names)
        finally:
            document.close()

    def test_embedded_font_width_differs_from_helvetica_fallback(self) -> None:
        if load_pdf_font("Verdana", "regular") is None:
            self.skipTest("Verdana is not installed on this computer")
        verdana = _text_width("Washington", 12, "Verdana")
        helvetica = _text_width("Washington", 12, "Helvetica")
        self.assertNotAlmostEqual(verdana, helvetica, places=2)


if __name__ == "__main__":
    unittest.main()
