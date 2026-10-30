import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.routes.address_book import (
    _address_book_pdf_drive_job_status,
    _redirect_after_address_book_pdf_ready,
)


class AddressBookPdfDrivePreviewTests(unittest.TestCase):
    def test_preview_opens_before_drive_upload_finishes(self) -> None:
        started = threading.Event()
        finish = threading.Event()

        def slow_upload(filename: str, content: bytes) -> dict:
            started.set()
            self.assertTrue(finish.wait(timeout=5))
            return {"file_id": "drive-file"}

        with tempfile.TemporaryDirectory() as tmp:
            preview_dir = Path(tmp)
            with (
                patch("app.routes.address_book.ADDRESS_BOOK_PDF_PREVIEW_DIR", preview_dir),
                patch("app.routes.address_book._address_book_pdf_can_save_to_drive", return_value=True),
                patch(
                    "app.routes.address_book.upload_address_book_pdf_to_google_drive",
                    side_effect=slow_upload,
                ),
            ):
                response = _redirect_after_address_book_pdf_ready(
                    "AlMsLa.pdf",
                    b"%PDF-1.4 test",
                    12,
                )
                self.assertEqual(response.status_code, 303)
                location = str(response.headers.get("location") or "")
                self.assertIn("/address-book/pdf/view/AlMsLa.pdf", location)
                self.assertIn("notice=address_book_pdf_saving_to_drive", location)
                self.assertTrue((preview_dir / "AlMsLa.pdf").exists())
                self.assertTrue(started.wait(timeout=2))
                self.assertEqual(_address_book_pdf_drive_job_status("AlMsLa.pdf")["status"], "pending")
                finish.set()
                status = "pending"
                for _ in range(50):
                    status = _address_book_pdf_drive_job_status("AlMsLa.pdf")["status"]
                    if status == "done":
                        break
                    time.sleep(0.05)
                self.assertEqual(status, "done")
                self.assertEqual(
                    _address_book_pdf_drive_job_status("AlMsLa.pdf")["notice"],
                    "address_book_pdf_uploaded",
                )

    def test_preview_skips_drive_when_not_an_editor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            preview_dir = Path(tmp)
            with (
                patch("app.routes.address_book.ADDRESS_BOOK_PDF_PREVIEW_DIR", preview_dir),
                patch("app.routes.address_book._address_book_pdf_can_save_to_drive", return_value=False),
                patch("app.routes.address_book.upload_address_book_pdf_to_google_drive") as upload,
            ):
                response = _redirect_after_address_book_pdf_ready(
                    "AlMsLa.pdf",
                    b"%PDF-1.4 test",
                    12,
                )
                self.assertEqual(response.status_code, 303)
                self.assertIn("notice=address_book_pdf_created", str(response.headers.get("location") or ""))
                upload.assert_not_called()
