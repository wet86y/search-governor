from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from search_governor.collector import build_param_report, collect_from_source, parse_adapter_report
from search_governor.config import ConfigError, load_json
from search_governor.sources import SourceSpec


class ContractTests(unittest.TestCase):
    def test_duplicate_json_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "duplicate.json"
            path.write_text('{"weights":{"demo":1,"demo":2}}', encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "Duplicate JSON key: demo"):
                load_json(path)

    def test_structured_stderr_report_is_removed_from_diagnostics(self) -> None:
        report, stderr = parse_adapter_report(
            'diagnostic\nSG_REPORT_JSON={"applied_params":{"freshness":{"applied":true}}}\n'
        )
        self.assertEqual("diagnostic", stderr)
        self.assertTrue(report["applied_params"]["freshness"]["applied"])

    def test_declared_but_unreported_parameter_is_visible(self) -> None:
        spec = SourceSpec(
            id="demo",
            path=None,  # type: ignore[arg-type]
            config={"supports": {"freshness": True}},
            enabled=True,
        )
        report = build_param_report(spec, {"freshness": "week"}, {})
        self.assertEqual("declared_supported_but_adapter_did_not_report", report["freshness"]["method"])

    def test_adapter_timeout_is_reported_without_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.joinpath("adapter.py").write_text("import time\ntime.sleep(2)\n", encoding="utf-8")
            spec = SourceSpec(
                id="slow-demo",
                path=root,
                config={"entrypoint": "python3 adapter.py", "timeout_sec": 1, "supports": {}},
                enabled=True,
            )
            candidates, report = collect_from_source(spec, {"query": "timeout contract"})
            self.assertEqual([], candidates)
            self.assertFalse(report["ok"])
            self.assertEqual("timeout after 1s", report["error"])

    def test_all_invalid_jsonl_is_reported_as_provider_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.joinpath("adapter.py").write_text("print('not-json')\n", encoding="utf-8")
            spec = SourceSpec(
                id="invalid-demo",
                path=root,
                config={"entrypoint": "python3 adapter.py", "supports": {}},
                enabled=True,
            )
            candidates, report = collect_from_source(spec, {"query": "invalid contract"})
            self.assertEqual([], candidates)
            self.assertFalse(report["ok"])
            self.assertEqual(1, report["bad_lines"])
