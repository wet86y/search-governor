from __future__ import annotations
import json
import os
import shlex
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from .sources import SourceSpec
from .normalizer import raw_to_candidate
from .models import Candidate


REQUEST_PARAM_KEYS = [
    "per_provider_count",
    "search_depth",
    "freshness",
    "date_after",
    "date_before",
    "topic",
    "include_domains",
    "exclude_domains",
    "locale",
    "language",
    "country",
    "include_provider_answer",
]

def _is_requested(value: Any) -> bool:
    return value not in (None, "", [], {}, False)


def parse_adapter_report(stderr: str) -> tuple[dict[str, Any], str]:
    reports: list[dict[str, Any]] = []
    kept_lines: list[str] = []
    for line in (stderr or "").splitlines():
        if line.startswith("SG_REPORT_JSON="):
            try:
                report = json.loads(line.split("=", 1)[1])
            except Exception:
                kept_lines.append(line)
                continue
            if isinstance(report, dict):
                reports.append(report)
        else:
            kept_lines.append(line)

    merged: dict[str, Any] = {}
    applied: dict[str, Any] = {}
    for report in reports:
        merged.update({k: v for k, v in report.items() if k != "applied_params"})
        if isinstance(report.get("applied_params"), dict):
            applied.update(report["applied_params"])
    if applied:
        merged["applied_params"] = applied
    return merged, "\n".join(kept_lines).strip()


def build_param_report(source: SourceSpec, request: dict[str, Any], adapter_report: dict[str, Any]) -> dict[str, Any]:
    supports = source.config.get("supports") if isinstance(source.config.get("supports"), dict) else {}
    applied_params = adapter_report.get("applied_params") if isinstance(adapter_report.get("applied_params"), dict) else {}
    out: dict[str, Any] = {}
    for key in REQUEST_PARAM_KEYS:
        declared = supports.get(key, False)
        requested = _is_requested(request.get(key))
        adapter_entry = applied_params.get(key)
        if isinstance(adapter_entry, dict):
            applied = bool(adapter_entry.get("applied"))
            method = adapter_entry.get("method") or "adapter_reported"
            value = adapter_entry.get("value") if "value" in adapter_entry else request.get(key)
            reason = adapter_entry.get("reason")
        elif adapter_entry is not None:
            applied = bool(adapter_entry)
            method = "adapter_reported_boolean"
            value = request.get(key)
            reason = None
        else:
            applied = False
            value = request.get(key)
            if declared in (True, "partial") and requested:
                method = "declared_supported_but_adapter_did_not_report"
                reason = "adapter did not emit applied_params for this requested parameter"
            elif declared in (False, "ignored"):
                method = "unsupported_or_ignored"
                reason = "provider source.json declares unsupported or ignored"
            else:
                method = "not_requested_or_unknown"
                reason = None
        out[key] = {
            "requested": requested,
            "value": value,
            "declared_support": declared,
            "applied_by_adapter": applied,
            "method": method,
        }
        if reason:
            out[key]["reason"] = reason
    return out


def collect_from_source(source: SourceSpec, request: dict[str, Any]) -> tuple[list[Candidate], dict[str, Any]]:
    from .config import load_dotenv

    load_dotenv()
    cfg = source.config
    entrypoint = cfg.get("entrypoint")
    if not entrypoint:
        return [], {"source": source.id, "ok": False, "error": "missing entrypoint"}
    req = dict(request)
    req["source_id"] = source.id
    req["source_config"] = cfg
    cwd = source.path
    timeout = int(cfg.get("timeout_sec") or request.get("timeout_sec") or 30)
    cmd = shlex.split(entrypoint)
    env = os.environ.copy()
    try:
        p = subprocess.run(
            cmd,
            input=json.dumps(req, ensure_ascii=False),
            text=True,
            capture_output=True,
            cwd=str(cwd),
            env=env,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return [], {
            "source": source.id,
            "ok": False,
            "error": f"timeout after {timeout}s",
            "param_report": build_param_report(source, req, {}),
        }
    except OSError as exc:
        return [], {
            "source": source.id,
            "ok": False,
            "error": f"adapter process could not start: {exc.strerror or type(exc).__name__}",
            "param_report": build_param_report(source, req, {}),
        }
    adapter_report, clean_stderr = parse_adapter_report(p.stderr or "")
    if p.returncode != 0:
        return [], {
            "source": source.id,
            "ok": False,
            "error": clean_stderr or f"exit {p.returncode}",
            "adapter_report": adapter_report,
            "param_report": build_param_report(source, req, adapter_report),
        }
    candidates: list[Candidate] = []
    bad_lines = 0
    for line in p.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except Exception:
            bad_lines += 1
            continue
        cand = raw_to_candidate(raw, source.id)
        if cand:
            candidates.append(cand)
        else:
            bad_lines += 1
    report = {
        "source": source.id,
        "ok": not (bad_lines and not candidates),
        "count": len(candidates),
        "bad_lines": bad_lines,
        "stderr": clean_stderr,
        "adapter_report": adapter_report,
        "param_report": build_param_report(source, req, adapter_report),
    }
    if bad_lines and not candidates:
        report["error"] = "adapter returned no valid Candidate JSONL"
    return candidates, report


def _collection_failure_report(source: SourceSpec, source_request: dict[str, Any], exc: Exception) -> dict[str, Any]:
    return {
        "source": source.id,
        "ok": False,
        "error": f"provider collection failed: {type(exc).__name__}",
        "count": 0,
        "bad_lines": 0,
        "param_report": build_param_report(source, source_request, {}),
    }


def collect_all(
    sources: list[SourceSpec],
    request: dict[str, Any],
    collection_report: dict[str, Any] | None = None,
) -> tuple[list[Candidate], list[dict[str, Any]]]:
    started_at = time.perf_counter()
    provider_counts = request.get("provider_counts") if isinstance(request.get("provider_counts"), dict) else {}
    prepared: list[tuple[SourceSpec, dict[str, Any], Any]] = []
    for s in sources:
        source_request = dict(request)
        requested_count = source_request.get("per_provider_count")
        if s.id in provider_counts:
            requested_count = int(provider_counts[s.id])
            source_request["per_provider_count"] = requested_count
        max_results = ((s.config.get("limits") or {}).get("max_results"))
        if max_results and requested_count:
            effective_count = min(int(requested_count), int(max_results))
            source_request["per_provider_count"] = effective_count
        prepared.append((s, source_request, requested_count))

    concurrency = max(1, len(sources))
    results: list[tuple[list[Candidate], dict[str, Any]] | None] = [None] * len(prepared)

    def collect_one(index: int) -> tuple[int, list[Candidate], dict[str, Any]]:
        source, source_request, _ = prepared[index]
        try:
            items, report = collect_from_source(source, source_request)
        except Exception as exc:
            items, report = [], _collection_failure_report(source, source_request, exc)
        return index, items, report

    if concurrency == 1:
        for index in range(len(prepared)):
            result_index, items, report = collect_one(index)
            results[result_index] = (items, report)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(collect_one, index) for index in range(len(prepared))]
            for future in as_completed(futures):
                result_index, items, report = future.result()
                results[result_index] = (items, report)

    all_candidates: list[Candidate] = []
    reports: list[dict[str, Any]] = []
    for index, (s, source_request, requested_count) in enumerate(prepared):
        result = results[index]
        if result is None:
            items, report = [], _collection_failure_report(s, source_request, RuntimeError("missing result"))
        else:
            items, report = result
        report.setdefault("count", len(items))
        report.setdefault("bad_lines", 0)
        report.setdefault("param_report", build_param_report(s, source_request, {}))
        report["requested_count"] = requested_count
        report["effective_count"] = source_request.get("per_provider_count")
        if requested_count != source_request.get("per_provider_count"):
            report["count_capped"] = True
        all_candidates.extend(items)
        reports.append(report)

    if collection_report is not None:
        collection_report.update(
            {
                "mode": "serial" if concurrency == 1 else "concurrent",
                "max_concurrency": concurrency,
                "provider_count": len(sources),
                "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
            }
        )
    return all_candidates, reports
