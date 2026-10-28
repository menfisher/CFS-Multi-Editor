from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.prune_vendor_wheels import copy_platform_wheels


class PruneVendorWheelsTests(unittest.TestCase):
    def test_copy_platform_wheels_can_exclude_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            dest = root / "dest"
            source.mkdir()
            (source / "fastapi-0.135.1-py3-none-any.whl").write_bytes(b"x")
            (source / "pymupdf-1.27.2.3-cp310-abi3-macosx_10_9_x86_64.whl").write_bytes(b"y")
            kept, skipped = copy_platform_wheels(
                source,
                dest,
                "mac",
                exclude_packages={"pymupdf"},
            )
            self.assertEqual(kept, 1)
            self.assertEqual(skipped, 0)
            names = sorted(path.name for path in dest.glob("*.whl"))
            self.assertEqual(names, ["fastapi-0.135.1-py3-none-any.whl"])


if __name__ == "__main__":
    unittest.main()
