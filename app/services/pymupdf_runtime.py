from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

from app.config import PROJECT_ROOT


_INSTALL_LOCK = threading.Lock()
_PYMUPDF_READY = False
_PYMUPDF_REQUIREMENT = "pymupdf"


class PymupdfUnavailableError(RuntimeError):
    """Raised when PyMuPDF cannot be imported or installed."""


def _read_pymupdf_requirement() -> str:
    requirements_path = PROJECT_ROOT / "requirements.txt"
    try:
        for raw_line in requirements_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            name = line.split(";", 1)[0].strip()
            if name.lower().startswith("pymupdf"):
                return name
    except OSError:
        pass
    return _PYMUPDF_REQUIREMENT


def _import_fitz():
    import fitz

    return fitz


def ensure_pymupdf(*, allow_install: bool = True) -> None:
    """Import PyMuPDF, installing it on first use when pip and network are available."""
    global _PYMUPDF_READY
    if _PYMUPDF_READY:
        return

    with _INSTALL_LOCK:
        if _PYMUPDF_READY:
            return
        try:
            _import_fitz()
        except ImportError:
            if not allow_install:
                raise PymupdfUnavailableError(
                    "PyMuPDF is not installed. Connect to the Internet and try the PDF insert again."
                ) from None
            requirement = _read_pymupdf_requirement()
            try:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", requirement],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or exc.stdout or "").strip()
                message = "PyMuPDF could not be installed automatically."
                if detail:
                    message = f"{message} {detail}"
                else:
                    message = (
                        f"{message} Connect to the Internet and try the PDF insert again."
                    )
                raise PymupdfUnavailableError(message) from exc
            try:
                _import_fitz()
            except ImportError as exc:
                raise PymupdfUnavailableError(
                    "PyMuPDF installed but could not be imported. Restart ContactsFreeShare and try again."
                ) from exc
        _PYMUPDF_READY = True
