import unittest

from app.services.address_book_pdf_service import (
    _PdfLine,
    _PdfPage,
    _PdfRule,
    _draw_cut_marks,
    _impose_pages_to_letter,
    _letter_block_offset_x,
    _letter_positions,
    _mirror_side_spiral_even_page_margins,
    _page_footer_y,
)


class AddressBookLetterImpositionTests(unittest.TestCase):
    def test_front_odd_pages_are_left_flush(self) -> None:
        page_width = 3.5 * 72.0
        page_height = 5.0 * 72.0
        letter_height = 11.0 * 72.0
        positions = _letter_positions(page_width, page_height, right_justify=False)
        self.assertEqual(positions[0][0], 0.0)
        self.assertEqual(positions[1][0], page_width)
        self.assertEqual(positions[0][1], letter_height - page_height)
        self.assertEqual(positions[2][1], letter_height - (2.0 * page_height))
        self.assertEqual(positions[0][1] + page_height, letter_height)

    def test_back_even_pages_are_fully_right_flush(self) -> None:
        page_width = 3.5 * 72.0
        letter_width = 8.5 * 72.0
        spare = letter_width - (page_width * 2.0)
        positions = _letter_positions(page_width, 5.0 * 72.0, right_justify=True)
        self.assertEqual(positions[0][0], spare)
        self.assertEqual(positions[1][0] + page_width, letter_width)
        self.assertEqual(_letter_block_offset_x(page_width, right_justify=True), spare)

    def test_front_cut_marks_align_with_left_flush_content(self) -> None:
        page_width = 3.5 * 72.0
        page_height = 5.0 * 72.0
        letter_height = 11.0 * 72.0
        letter = _PdfPage(8.5 * 72.0, letter_height)
        _draw_cut_marks(letter, page_width, page_height, right_justify=False)
        xs = {round(rule.x1, 3) for rule in letter.rules} | {round(rule.x2, 3) for rule in letter.rules}
        ys = {round(rule.y1, 3) for rule in letter.rules} | {round(rule.y2, 3) for rule in letter.rules}
        self.assertIn(0.0, xs)
        self.assertIn(round(page_width, 3), xs)
        self.assertIn(round(page_width * 2.0, 3), xs)
        self.assertEqual(_letter_positions(page_width, page_height, right_justify=False)[0][0], 0.0)
        self.assertIn(round(letter_height, 3), ys)

    def test_even_pages_swap_side_spiral_left_and_right_margins(self) -> None:
        margin_left = 0.3 * 72.0
        margin_right = 0.2 * 72.0
        odd = _PdfPage(3.5 * 72.0, 5.0 * 72.0)
        odd.lines.append(_PdfLine("odd", x=margin_left, y=20.0, size=8.0))
        odd.rules.append(_PdfRule(margin_left, 10.0, 200.0, 10.0))
        even = _PdfPage(3.5 * 72.0, 5.0 * 72.0)
        even.lines.append(_PdfLine("even", x=margin_left, y=20.0, size=8.0))
        even.rules.append(_PdfRule(margin_left, 10.0, 200.0, 10.0))
        _mirror_side_spiral_even_page_margins([odd, even], margin_left, margin_right)
        self.assertAlmostEqual(odd.lines[0].x, margin_left)
        self.assertAlmostEqual(odd.rules[0].x1, margin_left)
        self.assertAlmostEqual(even.lines[0].x, margin_right)
        self.assertAlmostEqual(even.rules[0].x1, margin_right)
        self.assertAlmostEqual(even.rules[0].x2, 200.0 + (margin_right - margin_left))

    def test_imposed_sheets_include_cut_marks_by_default(self) -> None:
        pages = [_PdfPage(3.5 * 72.0, 5.0 * 72.0) for _ in range(4)]
        imposed = _impose_pages_to_letter(pages, "side_spiral")
        self.assertTrue(imposed)
        self.assertGreater(sum(len(page.rules) for page in imposed), 0)

    def test_page_footer_sits_above_the_old_half_margin_without_entering_content(self) -> None:
        margin_bottom = 0.14 * 72.0
        footer_y = _page_footer_y(margin_bottom)
        self.assertGreater(footer_y, margin_bottom / 2.0)
        self.assertLess(footer_y, margin_bottom)

    def test_imposed_sheets_can_omit_cut_marks(self) -> None:
        pages = [_PdfPage(3.5 * 72.0, 5.0 * 72.0) for _ in range(4)]
        imposed = _impose_pages_to_letter(pages, "side_spiral", include_cut_marks=False)
        self.assertTrue(imposed)
        self.assertEqual(sum(len(page.rules) for page in imposed), 0)


if __name__ == "__main__":
    unittest.main()
