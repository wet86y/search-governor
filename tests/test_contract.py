from __future__ import annotations

import unittest

from search_governor.collector import build_param_report, parse_adapter_report
from search_governor.sources import SourceSpec


class ContractTests(unittest.TestCase):
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
