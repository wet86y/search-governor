from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SkillBuilderTests(unittest.TestCase):
    def test_browser_fallback_resolves_repository_root(self) -> None:
        browser_fetch = ROOT / "integrations" / "openclaw" / "browser_fetch.py"
        text = browser_fetch.read_text(encoding="utf-8")
        self.assertIn("SG_HOME = Path(__file__).resolve().parents[2]", text)

    def test_generated_skill_uses_new_cli_and_local_extension(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            local_extension = Path(tmp) / "local.md"
            output_dir = Path(tmp) / "out"
            local_extension.write_text(
                "<!-- description-suffix: , including platform-demo search -->\n\n"
                "## Local extension\n\nUse provider `platform-demo` only when explicitly requested.\n",
                encoding="utf-8",
            )
            subprocess.run(
                [
                    "python3",
                    str(ROOT / "scripts" / "build_openclaw_skill.py"),
                    "--root",
                    str(ROOT),
                    "--local-extension",
                    str(local_extension),
                    "--output-dir",
                    str(output_dir),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            generated = (output_dir / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn(str((ROOT / "bin" / "sg").resolve()), generated)
            self.assertIn("platform-demo", generated)
            self.assertIn("explicitly requested platform search, including platform-demo search; routes", generated)
            self.assertNotIn("description-suffix:", generated)
            self.assertNotIn("{{SG_BIN}}", generated)
            self.assertNotIn("{{LOCAL_DESCRIPTION_SUFFIX}}", generated)
            self.assertNotIn(".openclaw/workspace/skills/openclaw-search-governor/bin/sg", generated)

    def test_generated_skill_preserves_agent_operating_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "out"
            subprocess.run(
                [
                    "python3",
                    str(ROOT / "scripts" / "build_openclaw_skill.py"),
                    "--root",
                    str(ROOT),
                    "--local-extension",
                    "/dev/null",
                    "--output-dir",
                    str(output_dir),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            generated = (output_dir / "SKILL.md").read_text(encoding="utf-8")
            for required in (
                "searchGovernor.runId",
                "search_governor_status",
                "search_governor_read",
                "--point-question",
                "--allow-analysis-fallback",
                "provider-declared inline content or native fetch",
                "auth_required",
                "Do not call files under `managed_sources/` directly",
            ):
                self.assertIn(required, generated)

    def test_deployer_archives_old_skill_and_installs_generated_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            source = temp / "source"
            target = temp / "workspace" / "skills" / "openclaw-search-governor"
            archive_root = temp / "archive"
            source.mkdir()
            target.mkdir(parents=True)
            source.joinpath("SKILL.md").write_text(
                '---\nname: "openclaw-search-governor"\ndescription: "generated"\n---\n\nnew runtime\n',
                encoding="utf-8",
            )
            target.joinpath("SKILL.md").write_text("old runtime\n", encoding="utf-8")
            subprocess.run(
                [
                    "python3",
                    str(ROOT / "scripts" / "deploy_openclaw_skill.py"),
                    str(source),
                    str(target),
                    "--archive-root",
                    str(archive_root),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("new runtime", target.joinpath("SKILL.md").read_text(encoding="utf-8"))
            archives = list(archive_root.iterdir())
            self.assertEqual(1, len(archives))
            self.assertEqual("old runtime\n", archives[0].joinpath("SKILL.md").read_text(encoding="utf-8"))
