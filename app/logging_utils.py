from __future__ import annotations

from pathlib import Path

MAX_ERROR_LOG_LINES = 200


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
