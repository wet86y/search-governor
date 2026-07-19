from __future__ import annotations

import unittest
from unittest.mock import patch

from search_governor import deep_analyzer
from search_governor.models import Candidate
from search_governor.content_cleaner import clean_text
from search_governor.dedupe import dedupe
from search_governor.fetcher import browser_fallback_allowed
from search_governor.pipeline import PipelineError, allocate_provider_counts, fallback_deep_article, mode_defaults, resolve_provider_preset


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

    def test_mode_budgets_are_compatibility_stable(self) -> None:
        self.assertEqual(15, mode_defaults("fast")["total_provider_count"])
        self.assertEqual(40, mode_defaults("full")["total_provider_count"])
        self.assertEqual(40, mode_defaults("deep")["total_provider_count"])

    def test_known_template_allocations(self) -> None:
        speed_sources = ["source_a", "source_b", "source_c", "source_d"]
        speed = allocate_provider_counts(
            speed_sources,
            {"source_a": 0.4, "source_b": 0.2, "source_c": 0.2, "source_d": 0.2},
            mode_defaults("fast")["total_provider_count"],
        )
        self.assertEqual({"source_a": 6, "source_b": 3, "source_c": 3, "source_d": 3}, speed)
        total_sources = [f"source_{index}" for index in range(5)]
        self.assertEqual({source: 8 for source in total_sources}, allocate_provider_counts(total_sources, {source: 1 for source in total_sources}, 40))
        free_sources = [f"source_{index}" for index in range(4)]
        self.assertEqual({source: 10 for source in free_sources}, allocate_provider_counts(free_sources, {source: 1 for source in free_sources}, 40))

    def test_invalid_preset_weight_is_rejected(self) -> None:
        cfg = {"default_preset": "bad", "presets": {"bad": {"weights": {"a": 0}}}}
        with self.assertRaisesRegex(PipelineError, "invalid weight"):
            resolve_provider_preset(cfg, None, "fast")

    def test_exact_and_weak_candidate_dedupe_are_reported(self) -> None:
        candidates = [
            Candidate("a", "A stable result title", "https://example.test/a?tracking=1", "short", "source_a", 1, "example.test", "https://example.test/a"),
            Candidate("b", "A stable result title", "https://example.test/a", "a longer duplicate snippet", "source_b", 2, "example.test", "https://example.test/a"),
            Candidate("c", "A stable result title!", "https://example.test/b", "weak duplicate", "source_c", 3, "example.test", "https://example.test/b"),
        ]
        kept, report = dedupe(candidates)
        self.assertEqual(1, len(kept))
        self.assertEqual(1, report["exact_removed"])
        self.assertEqual(1, report["weak_removed"])
        self.assertEqual("a longer duplicate snippet", kept[0].snippet)

    def test_content_cleaner_drops_duplicate_lines(self) -> None:
        line = "This is substantive body content that must remain."
        cleaned, report = clean_text(
            f"{line}\n{line}\n",
            {"min_line_chars": 5, "drop_duplicate_lines": True, "max_chars_per_doc": 1000},
        )
        self.assertEqual(line, cleaned)
        self.assertEqual(1, report["dropped_duplicate_lines"])

    def test_browser_fallback_only_accepts_declared_failure_kinds(self) -> None:
        cfg = {"browser_fallback_enabled": True, "browser_fallback_error_kinds": ["blocked", "rate_limited", "empty"]}
        self.assertTrue(browser_fallback_allowed("blocked", cfg))
        self.assertFalse(browser_fallback_allowed("network", cfg))
        self.assertFalse(browser_fallback_allowed("blocked", {**cfg, "browser_fallback_enabled": False}))

    def test_source_evaluation_retries_one_transient_failure(self) -> None:
        candidate = Candidate("retry", "Retry example", "https://example.test/retry", "evidence", "source_a", 1)
        parsed = {"items": [{"rank": 1, "url": candidate.url, "source_quality_score": 0.8}]}
        with patch.object(deep_analyzer, "_chat_json", side_effect=[deep_analyzer.DeepAnalyzerError("transient"), (parsed, {"usage": {"total_tokens": 10}})]) as call:
            result = deep_analyzer._eval_single(candidate, 1, "query", "query", None, {"source_eval_attempts": 2}, 800, 1200)
        self.assertIsNotNone(result)
        self.assertEqual(2, call.call_count)
        self.assertEqual(candidate.url, result["item"]["url"])

    def test_deterministic_deep_fallback_is_labeled(self) -> None:
        candidate = Candidate(id="example", title="Example", url="https://example.com", snippet="Evidence text", provider="mock", rank=1)
        candidate.final_score = 0.75
        report = fallback_deep_article("query", [candidate])
        self.assertTrue(report["json"]["fallback"])
        self.assertIn("not a synthesized conclusion", report["markdown"])
