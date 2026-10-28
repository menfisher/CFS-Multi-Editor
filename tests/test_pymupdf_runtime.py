from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from app.services import pymupdf_runtime


class PymupdfRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        pymupdf_runtime._PYMUPDF_READY = False

    def tearDown(self) -> None:
        pymupdf_runtime._PYMUPDF_READY = False

    @patch("app.services.pymupdf_runtime._import_fitz")
    def test_ensure_pymupdf_skips_install_when_already_importable(self, import_fitz) -> None:
        pymupdf_runtime.ensure_pymupdf()
        import_fitz.assert_called_once_with()
        import_fitz.reset_mock()
        pymupdf_runtime.ensure_pymupdf()
        import_fitz.assert_not_called()

    @patch("app.services.pymupdf_runtime.subprocess.run")
    @patch("app.services.pymupdf_runtime._import_fitz")
    def test_ensure_pymupdf_installs_on_first_use(self, import_fitz, run) -> None:
        import_fitz.side_effect = [ImportError("missing"), object()]
        pymupdf_runtime.ensure_pymupdf()
        run.assert_called_once()
        args = run.call_args.args[0]
        self.assertEqual(args[0:3], [sys.executable, "-m", "pip"])
        self.assertEqual(args[3], "install")
        self.assertTrue(str(args[4]).startswith("pymupdf"))

    @patch("app.services.pymupdf_runtime.subprocess.run")
    @patch("app.services.pymupdf_runtime._import_fitz", side_effect=ImportError("missing"))
    def test_ensure_pymupdf_raises_when_install_fails(self, _import_fitz, run) -> None:
        from subprocess import CalledProcessError

        run.side_effect = CalledProcessError(1, "pip", stderr="network down")
        with self.assertRaises(pymupdf_runtime.PymupdfUnavailableError):
            pymupdf_runtime.ensure_pymupdf()


if __name__ == "__main__":
    unittest.main()
