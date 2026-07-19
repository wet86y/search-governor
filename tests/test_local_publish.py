from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.publish_local_release import (
    LocalPublishError,
    require_clean_committed_head,
    require_consistent_version,
)


ROOT = Path(__file__).resolve().parents[1]


class LocalPublishTests(unittest.TestCase):
    def test_accepts_only_a_clean_committed_head(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary) / "checkout"
            checkout.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
            subprocess.run(["git", "config", "user.name", "Search Governor Tests"], cwd=checkout, check=True)
            subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=checkout, check=True)
            tracked = checkout / "tracked.txt"
            tracked.write_text("committed\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=checkout, check=True)
            subprocess.run(["git", "commit", "-qm", "initial"], cwd=checkout, check=True)

            expected = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=checkout, text=True, capture_output=True, check=True
            ).stdout.strip()
            self.assertEqual(expected, require_clean_committed_head(checkout))

            tracked.write_text("uncommitted\n", encoding="utf-8")
            with self.assertRaisesRegex(LocalPublishError, "Working tree must be clean"):
                require_clean_committed_head(checkout)

    def test_release_versions_are_consistent(self) -> None:
        self.assertEqual("0.1.3", require_consistent_version(ROOT))


if __name__ == "__main__":
    unittest.main()
