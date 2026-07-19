from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from search_governor.config import load_config
from search_governor.fetcher import resolve_browser_fallback_script
from search_governor.paths import app_config_dir, app_home, config_dir, data_dir, runtime_home, sources_dir
from search_governor.pipeline import start_deferred_fetch


class SplitPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app = self.root / "release"
        self.runtime = self.root / "runtime"
        (self.app / "config").mkdir(parents=True)
        (self.runtime / "config").mkdir(parents=True)
        self.previous = {
            key: os.environ.get(key)
            for key in ("SG_APP_HOME", "SG_RUNTIME_HOME", "SG_HOME", "SEARCH_GOVERNOR_DISABLE_LOCAL")
        }
        os.environ["SG_APP_HOME"] = str(self.app)
        os.environ["SG_RUNTIME_HOME"] = str(self.runtime)
        os.environ["SG_HOME"] = str(self.runtime)
        os.environ.pop("SEARCH_GOVERNOR_DISABLE_LOCAL", None)

    def tearDown(self) -> None:
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def test_app_and_runtime_roots_are_separate(self) -> None:
        self.assertEqual(self.app.resolve(), app_home())
        self.assertEqual(self.runtime.resolve(), runtime_home())
        self.assertEqual(self.app.resolve() / "config", app_config_dir())
        self.assertEqual(self.runtime.resolve() / "config", config_dir())
        self.assertEqual(self.runtime.resolve() / "managed_sources", sources_dir())
        self.assertEqual(self.runtime.resolve() / "data", data_dir())

    def test_runtime_config_overlays_release_baseline(self) -> None:
        (self.app / "config" / "demo.json").write_text(
            json.dumps({"enabled": False, "nested": {"public": 1, "value": "base"}}), encoding="utf-8"
        )
        (self.runtime / "config" / "demo.local.json").write_text(
            json.dumps({"enabled": True, "nested": {"value": "local"}}), encoding="utf-8"
        )
        self.assertEqual(
            {"enabled": True, "nested": {"public": 1, "value": "local"}},
            load_config("demo"),
        )

    def test_deferred_fetch_uses_release_script_and_runtime_run_dir(self) -> None:
        script = self.app / "scripts" / "fetch_background.py"
        script.parent.mkdir(parents=True)
        script.write_text("# fixture\n", encoding="utf-8")
        run_dir = self.runtime / "data" / "runs" / "fixture"
        run_dir.mkdir(parents=True)

        with patch("search_governor.pipeline.subprocess.Popen") as popen:
            popen.return_value.pid = 4321
            report = start_deferred_fetch(run_dir, 5)

        command = popen.call_args.args[0]
        self.assertEqual(str(script), command[1])
        self.assertEqual(str(run_dir), command[3])
        self.assertEqual(str(self.app), popen.call_args.kwargs["cwd"])
        self.assertEqual({"started": True, "pid": 4321, "script": str(script)}, report)

    def test_relative_browser_fallback_uses_release_root(self) -> None:
        self.assertEqual(
            self.app / "integrations" / "openclaw" / "browser_fetch.py",
            resolve_browser_fallback_script("integrations/openclaw/browser_fetch.py"),
        )


if __name__ == "__main__":
    unittest.main()
