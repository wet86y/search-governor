from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from search_governor.collector import collect_all
from search_governor.models import Candidate
from search_governor.sources import SourceSpec


def source(source_id: str) -> SourceSpec:
    return SourceSpec(id=source_id, path=Path("."), config={"supports": {}}, enabled=True)


def candidate(source_id: str) -> Candidate:
    return Candidate(source_id, source_id, f"https://{source_id}.example/", "result", source_id, 1)


class CollectorConcurrencyTests(unittest.TestCase):
    def test_all_selected_providers_overlap_and_reports_keep_request_order(self) -> None:
        sources = [source("provider_a"), source("provider_b"), source("provider_c")]
        delays = {"provider_a": 0.15, "provider_b": 0.10, "provider_c": 0.05}
        active = 0
        max_active = 0
        lock = threading.Lock()
        all_started = threading.Event()

        def fake_collect(spec: SourceSpec, _request: dict):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
                if active == len(sources):
                    all_started.set()
            all_started.wait(timeout=1)
            time.sleep(delays[spec.id])
            with lock:
                active -= 1
            return [candidate(spec.id)], {"source": spec.id, "ok": True, "count": 1, "bad_lines": 0, "param_report": {}}

        collection_report: dict = {}
        with patch("search_governor.collector.collect_from_source", side_effect=fake_collect):
            started = time.perf_counter()
            candidates, reports = collect_all(sources, {"per_provider_count": 1}, collection_report)
            elapsed = time.perf_counter() - started

        self.assertEqual(len(sources), max_active)
        self.assertLess(elapsed, sum(delays.values()))
        self.assertEqual([spec.id for spec in sources], [item.provider for item in candidates])
        self.assertEqual([spec.id for spec in sources], [report["source"] for report in reports])
        self.assertEqual("concurrent", collection_report["mode"])
        self.assertEqual(3, collection_report["max_concurrency"])
        self.assertEqual(3, collection_report["provider_count"])
        self.assertIn("elapsed_ms", collection_report)

    def test_single_provider_failure_is_isolated(self) -> None:
        sources = [source("provider_a"), source("provider_b"), source("provider_c")]

        def fake_collect(spec: SourceSpec, _request: dict):
            if spec.id == "provider_b":
                raise RuntimeError("provider secret detail")
            return [candidate(spec.id)], {"source": spec.id, "ok": True, "count": 1, "bad_lines": 0, "param_report": {}}

        with patch("search_governor.collector.collect_from_source", side_effect=fake_collect):
            candidates, reports = collect_all(sources, {"per_provider_count": 1})

        self.assertEqual(["provider_a", "provider_c"], [item.provider for item in candidates])
        self.assertFalse(reports[1]["ok"])
        self.assertEqual("provider_b", reports[1]["source"])
        self.assertNotIn("secret detail", reports[1]["error"])

    def test_single_provider_uses_serial_mode(self) -> None:
        sources = [source("provider_a")]
        calls: list[str] = []

        def fake_collect(spec: SourceSpec, _request: dict):
            calls.append(spec.id)
            return [candidate(spec.id)], {"source": spec.id, "ok": True, "count": 1, "bad_lines": 0, "param_report": {}}

        report: dict = {}
        with patch("search_governor.collector.collect_from_source", side_effect=fake_collect):
            collect_all(sources, {"per_provider_count": 1}, report)

        self.assertEqual([spec.id for spec in sources], calls)
        self.assertEqual("serial", report["mode"])
        self.assertEqual(1, report["max_concurrency"])

if __name__ == "__main__":
    unittest.main()
