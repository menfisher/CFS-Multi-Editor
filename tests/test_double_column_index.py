import unittest

from app.services.address_book_pdf_service import _IndexEntry, _build_index_pages


def _index_pages(two_column: bool) -> list:
    names = [f"Name{index:03d}, Person" for index in range(40)]
    entries = [_IndexEntry(name=name, page=8 + index, target_y=100.0) for index, name in enumerate(names)]
    return _build_index_pages(
        entries=entries,
        page_width=3.5 * 72,
        page_height=2.4 * 72,
        margin_left=0.15 * 72,
        margin_right=0.15 * 72,
        margin_top=0.15 * 72,
        margin_bottom=0.15 * 72,
        base_font_size=7.0,
        start_page_number=20,
        two_column=two_column,
        compact=True,
    )


class DoubleColumnIndexTests(unittest.TestCase):
    def test_two_column_index_uses_left_and_right_name_columns(self):
        pages = _index_pages(True)
        self.assertTrue(pages)
        name_xs = sorted({
            round(line.x, 1)
            for line in pages[0].lines
            if "," in line.text
        })
        self.assertGreaterEqual(len(name_xs), 2)
        self.assertGreater(name_xs[-1] - name_xs[0], 70)

    def test_two_column_index_draws_a_center_divider(self):
        pages = _index_pages(True)
        self.assertTrue(pages[0].rules)
        divider = pages[0].rules[0]
        self.assertAlmostEqual(divider.x1, divider.x2, places=1)
        page_center = (3.5 * 72) / 2
        self.assertLess(abs(divider.x1 - page_center), 18)

    def test_single_column_index_keeps_names_on_the_left(self):
        pages = _index_pages(False)
        name_xs = {round(line.x, 1) for line in pages[0].lines if "," in line.text}
        self.assertEqual(len(name_xs), 1)
        self.assertLess(next(iter(name_xs)), 20)
        self.assertFalse(pages[0].rules)


if __name__ == "__main__":
    unittest.main()
