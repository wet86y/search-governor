from __future__ import annotations

import unittest

from search_governor.models import Candidate
from search_governor.pipeline import allocate_provider_counts, fallback_deep_article, resolve_provider_preset


class PipelineTests(unittest.TestCase):
    def test_budget_allocation_is_exact(self) -> None:
        counts = allocate_provider_counts(["a", "b"], {"a": 2, "b": 1}, 9)
        self.assertEqual(9, sum(counts.values()))
        self.assertGreater(counts["a"], counts["b"])

    def test_mode_default_preset(self) -> None:
        cfg = {
            "default_preset": "configured",
            "mode_default_presets": {"fast": "fast-local"},
            "presets": {"configured": {"weights": {}}, "fast-local": {"weights": {"a": 1}}},
        }
        name, preset, source = resolve_provider_preset(cfg, None, "fast")
        self.assertEqual("fast-local", name)
        self.assertEqual({"a": 1}, preset["weights"])
        self.assertEqual("mode_default:fast", source)

    def test_deterministic_deep_fallback_is_labeled(self) -> None:
        candidate = Candidate(id="example", title="Example", url="https://example.com", snippet="Evidence text", provider="mock", rank=1)
        candidate.final_score = 0.75
        report = fallback_deep_article("query", [candidate])
        self.assertTrue(report["json"]["fallback"])
        self.assertIn("not a synthesized conclusion", report["markdown"])
