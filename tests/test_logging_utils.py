import tempfile
import unittest
from pathlib import Path

from app.logging_utils import MAX_ERROR_LOG_LINES, _trim_log_if_needed, append_log_line


class LoggingUtilsTests(unittest.TestCase):
    def test_trim_keeps_only_the_newest_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "errors.log"
            path.write_text("".join(f"old {index}\n" for index in range(250)), encoding="utf-8")
            _trim_log_if_needed(path, max_lines=10)
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines, [f"old {index}" for index in range(240, 250)])

    def test_append_trims_after_writing_the_new_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "errors.log"
            path.write_text("".join(f"old {index}\n" for index in range(MAX_ERROR_LOG_LINES)), encoding="utf-8")
            self.assertTrue(append_log_line(path, "newest"))
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), MAX_ERROR_LOG_LINES)
            self.assertEqual(lines[0], "old 1")
            self.assertEqual(lines[-1], "newest")


if __name__ == "__main__":
    unittest.main()
