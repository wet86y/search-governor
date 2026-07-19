from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.deploy_local_release import deploy
from scripts.migrate_flat_runtime import migrate


ROOT = Path(__file__).resolve().parents[1]


class FlatRuntimeMigrationTests(unittest.TestCase):
    def test_moves_only_persistent_assets_and_records_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install_root = Path(temporary) / "search-governor"
            (install_root / ".git").mkdir(parents=True)
            (install_root / "managed_sources").mkdir()
            (install_root / "managed_sources" / "sources.json").write_text("{}\n", encoding="utf-8")
            (install_root / "connectors" / "mediacrawler").mkdir(parents=True)
            (install_root / "data" / "runs").mkdir(parents=True)
            (install_root / "integrations" / "openclaw" / "local").mkdir(parents=True)
            (install_root / "config").mkdir()
            (install_root / "config" / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
            (install_root / "config" / "presets.local.json").write_text("{}\n", encoding="utf-8")
            public_file = install_root / "README.md"
            public_file.write_text("legacy checkout\n", encoding="utf-8")

            result = migrate(install_root)

            runtime = install_root / "runtime"
            self.assertTrue((runtime / "managed_sources" / "sources.json").is_file())
            self.assertTrue((runtime / "connectors" / "mediacrawler").is_dir())
            self.assertTrue((runtime / "config" / ".env").is_file())
            self.assertTrue((runtime / "config" / "presets.local.json").is_file())
            self.assertTrue((runtime / "integrations" / "openclaw" / "local").is_dir())
            self.assertTrue(public_file.is_file())
            state = json.loads((runtime / "migration-state.json").read_text(encoding="utf-8"))
            self.assertEqual(result["moves"], state["moves"])
            self.assertEqual(6, len(state["moves"]))


class LocalReleaseDeploymentTests(unittest.TestCase):
    def test_deploys_git_archive_and_stable_wrapper(self) -> None:
        subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True)
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            install_root = base / "share" / "search-governor"
            bin_dir = base / "bin"

            state = deploy(ROOT, install_root, "HEAD", bin_dir, skip_venv=True)

            current = install_root / "current"
            release = install_root / "releases" / state["release_id"]
            self.assertTrue(current.is_symlink())
            self.assertEqual(Path("releases") / state["release_id"], Path(current.readlink()))
            self.assertTrue((release / "search_governor" / "cli.py").is_file())
            self.assertFalse((release / ".git").exists())
            self.assertFalse((release / "managed_sources").exists())
            self.assertTrue((install_root / "runtime" / "managed_sources").is_dir())
            self.assertIn("/current/bin/sg", (bin_dir / "sg").read_text(encoding="utf-8"))
            self.assertEqual(state, json.loads((install_root / "install-state.json").read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
