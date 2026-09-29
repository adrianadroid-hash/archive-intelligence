"""Smoke tests for the package skeleton."""

import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import archive_intelligence  # noqa: E402


class TestPackage(unittest.TestCase):
    def test_version_exposed(self) -> None:
        self.assertEqual(archive_intelligence.__version__, "0.1.0")

    def test_main_entrypoint(self) -> None:
        from archive_intelligence.__main__ import main

        self.assertEqual(main(), 0)


if __name__ == "__main__":
    unittest.main()
