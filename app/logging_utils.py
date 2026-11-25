from __future__ import annotations

import traceback
from datetime import datetime
from pathlib import Path

from app.network_errors import describe_request_error

MAX_ERROR_LOG_LINES = 200
ERROR_LOG_SEPARATOR = "=" * 72


def _trim_log_if_needed(path: Path, *, max_lines: int = MAX_ERROR_LOG_LINES) -> None:
    if max_lines <= 0:
        return
    try:
        if not path.is_file():
            return
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines(keepends=True)
        if len(lines) <= max_lines:
            return
        kept = "".join(lines[-max_lines:])
        path.write_text(kept, encoding="utf-8")
    except OSError:
        return


def append_log_line(path: Path, line: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line.rstrip("\n"))
            handle.write("\n")
        _trim_log_if_needed(path)
        return True
    except OSError:
        return False


def format_error_log_block(*, method: str, url: str, exc: BaseException) -> str:
    timestamp = datetime.now().isoformat(timespec="seconds")
    summary = describe_request_error(exc)
    error_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return (
        f"{ERROR_LOG_SEPARATOR}\n"
        f"[{timestamp}] {method} {url}\n"
        f"Summary: {summary}\n"
        f"{error_text.rstrip()}\n"
        f"{ERROR_LOG_SEPARATOR}"
    )


def append_log_block(path: Path, text: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text.rstrip("\n"))
            handle.write("\n")
        _trim_log_if_needed(path)
        return True
    except OSError:
        return False
