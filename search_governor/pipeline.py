from __future__ import annotations
import json
import math
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from .config import load_all_configs
from .sources import load_sources
from .collector import collect_all
from .dedupe import dedupe, normalize_and_filter_dates
from .rules import score_candidates
from .reranker import apply_body_rerank, apply_rerank
from .fetcher import apply_inline_content, external_fetch_allowed, fetch_top
from .fetch_cache import cache_key_for_candidate, save_candidate_cache
from .content_cleaner import clean_top_content
from .brief import brief_from_args, build_ranking_context
from .deep_analyzer import (
    analyze_source_quality,
    apply_source_quality_blend,
    attach_analysis,
    generate_deep_article,
)
from .reporter import agent_candidate_result, agent_pipeline_summary, evidence_markdown, write_jsonl
from .gc import cleanup
from .paths import data_dir
from .paths import home


class PipelineError(RuntimeError):
    pass


def split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [x.strip() for x in value.split(",") if x.strip()]


def parse_provider_counts(value: str | None) -> dict[str, int]:
    out: dict[str, int] = {}
    if not value:
        return out
    for item in split_csv(value):
        if ":" not in item:
            raise PipelineError(f"Invalid --provider-counts item: {item}. Expected source:count")
        source_id, count_text = item.split(":", 1)
        source_id = source_id.strip()
        try:
            count = int(count_text.strip())
        except Exception as exc:
            raise PipelineError(f"Invalid count for provider {source_id}: {count_text}") from exc
        if count < 1:
            raise PipelineError(f"Provider count must be >= 1 for {source_id}")
        out[source_id] = count
    return out


def cap_provider_counts(counts: dict[str, int], per_provider_cap: int | None) -> dict[str, int]:
    if per_provider_cap is None:
        return dict(counts)
    return {source: min(count, per_provider_cap) for source, count in counts.items()}


def allocate_provider_counts(sources: list[str], weights: dict[str, float], total_count: int) -> dict[str, int]:
    weighted = {s: float(weights.get(s, 0.0)) for s in sources}
    if not any(v > 0 for v in weighted.values()):
        weighted = {s: 1.0 for s in sources}
    positive = {s: max(0.0, w) for s, w in weighted.items() if w > 0}
    if not positive:
        return {}
    total_weight = sum(positive.values())
    total_count = max(len(positive), int(total_count))
    exact = {s: total_count * w / total_weight for s, w in positive.items()}
    counts = {s: max(1, int(exact[s])) for s in positive}
    remainder = total_count - sum(counts.values())
    if remainder > 0:
        order = sorted(positive, key=lambda s: (exact[s] - int(exact[s]), exact[s]), reverse=True)
        for i in range(remainder):
            counts[order[i % len(order)]] += 1
    elif remainder < 0:
        order = sorted(positive, key=lambda s: (exact[s] - int(exact[s]), exact[s]))
        for source in order:
            if remainder == 0:
                break
            removable = min(counts[source] - 1, -remainder)
            if removable > 0:
                counts[source] -= removable
                remainder += removable
    return counts


def resolve_provider_preset(presets_cfg: dict[str, Any], name: str | None, mode: str) -> tuple[str, dict[str, Any], str]:
    preset_source = "cli"
    preset_name = name.strip() if isinstance(name, str) and name.strip() else None
    if preset_name is None:
        mode_defaults_cfg = presets_cfg.get("mode_default_presets", {})
        if isinstance(mode_defaults_cfg, dict):
            mode_preset = mode_defaults_cfg.get(mode)
            if isinstance(mode_preset, str) and mode_preset.strip():
                preset_name = mode_preset.strip()
                preset_source = f"mode_default:{mode}"
    if preset_name is None:
        preset_name = presets_cfg.get("default_preset", "total")
        preset_source = "default_preset"
    if not isinstance(preset_name, str) or not preset_name.strip():
        preset_name = "total"
        preset_source = "fallback"
    presets = presets_cfg.get("presets", {})
    preset = presets.get(preset_name)
    if not isinstance(preset, dict):
        raise PipelineError(f"Unknown provider preset from {preset_source}: {preset_name}")
    weights = preset.get("weights", {})
    if not isinstance(weights, dict):
        raise PipelineError(f"Provider preset {preset_name} must contain a weights object")
    for source_id, weight in weights.items():
        if not isinstance(source_id, str) or not source_id.strip():
            raise PipelineError(f"Provider preset {preset_name} contains an empty provider id")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(float(weight)) or float(weight) <= 0:
            raise PipelineError(f"Provider preset {preset_name} has invalid weight for {source_id}: {weight!r}")
    return preset_name, preset, preset_source


def provider_capabilities(source_specs: list[Any]) -> dict[str, dict[str, Any]]:
    caps: dict[str, dict[str, Any]] = {}
    for spec in source_specs:
        value = spec.config.get("capabilities", {})
        caps[spec.id] = value if isinstance(value, dict) else {}
    return caps


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


def start_deferred_fetch(run_dir: Path, return_count: int) -> dict[str, Any]:
    script = home() / "scripts" / "fetch_background.py"
    if not script.exists():
        return {"started": False, "error": f"missing script: {script}"}
    log = run_dir / "deferred_fetch.spawn.log"
    err = run_dir / "deferred_fetch.spawn.err"
    with log.open("ab") as stdout, err.open("ab") as stderr:
        proc = subprocess.Popen(
            [sys.executable, str(script), "--run-dir", str(run_dir), "--return-count", str(return_count)],
            cwd=str(home()),
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
    return {"started": True, "pid": proc.pid, "script": str(script)}


def write_stage(run_dir: Path, stage: str, status: str, **detail: Any) -> None:
    payload = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "stage": stage,
        "status": status,
        **detail,
    }
    with (run_dir / "stages.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def fallback_deep_article(query: str, candidates: list[Candidate]) -> dict[str, Any]:
    lines = [
        f"# Deep evidence outline: {query}",
        "",
        "> Generated without an analysis model. This is a deterministic evidence index, not a synthesized conclusion.",
        "",
        "## Evidence",
    ]
    items: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        excerpt = (candidate.fetched_content or candidate.snippet or "").strip()[:1200]
        lines.extend(
            [
                "",
                f"### {index}. {candidate.title}",
                f"- Provider: {candidate.provider}",
                f"- URL: {candidate.url}",
                f"- Score: {candidate.final_score:.4f}",
                "",
                excerpt or "No evidence text was available.",
            ]
        )
        items.append({"index": index, "title": candidate.title, "url": candidate.url, "provider": candidate.provider, "score": candidate.final_score, "excerpt": excerpt})
    return {
        "json": {"query": query, "fallback": True, "warning": "analysis model not configured", "evidence": items},
        "markdown": "\n".join(lines).rstrip() + "\n",
        "usage": None,
        "provider": "deterministic_fallback",
        "model": None,
    }


def mode_defaults(mode: str) -> dict[str, Any]:
    if mode == "full":
        return {
            "sources": [],
            "total_provider_count": 40,
            "per_provider_count": 10,
            "return_count": 8,
            "summary_count": 15,
            "fetch_mode": "sync",
        }
    if mode == "deep":
        return {
            "sources": [],
            "total_provider_count": 40,
            "per_provider_count": 10,
            "return_count": 8,
            "summary_count": 15,
            "fetch_mode": "sync",
        }
    return {
        "sources": [],
        "total_provider_count": 15,
        "per_provider_count": 5,
        "return_count": 5,
        "summary_count": 5,
    }


def search(args) -> dict[str, Any]:
    cfg = load_all_configs()
    if cfg["retention"].get("cleanup_on_search", True):
        cleanup(cfg["retention"])

    run_id = new_run_id()
    run_dir = data_dir() / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    mode = args.mode
    brief = brief_from_args(args)
    if mode == "deep":
        missing = brief.validate_for_deep()
        if missing:
            raise PipelineError(
                "Deep mode requires brief fields: "
                + ", ".join(missing)
                + ". Use --brief-file or explicit --point-question/--goal/--boundaries/--output-use."
            )
    ranking_query = build_ranking_context(args.query, brief, mode)
    defaults = mode_defaults(mode)
    write_stage(run_dir, "init", "ok", mode=mode)
    if mode == "deep":
        (run_dir / "ranking_context.txt").write_text(ranking_query, encoding="utf-8")

    preset_name, provider_preset, preset_source = resolve_provider_preset(cfg["provider_presets"], args.provider_preset, mode)
    preset_weights = provider_preset.get("weights", {}) if isinstance(provider_preset.get("weights", {}), dict) else {}

    all_sources = load_sources()
    if args.providers:
        requested_sources = split_csv(args.providers)
    else:
        requested_sources = list(preset_weights) or defaults.get("sources", [])
    if not requested_sources:
        raise PipelineError(
            "No search providers are configured. Register providers in managed_sources/sources.json "
            "or pass --providers explicitly. The bundled example provider is for tests only."
        )
    missing = [s for s in requested_sources if s not in all_sources]
    if missing:
        raise PipelineError(f"Unknown sources: {missing}")
    source_specs = [all_sources[s] for s in requested_sources]
    capabilities_by_provider = provider_capabilities(source_specs)
    disabled = [s.id for s in source_specs if not s.enabled]
    if disabled and not args.allow_disabled_sources:
        raise PipelineError(f"Disabled providers: {disabled}. Enable them in managed_sources/sources.json or pass --allow-disabled-sources for adapter development.")

    return_count = int(args.return_count or defaults.get("return_count", 5))
    if return_count < 1:
        raise PipelineError("--return-count must be >= 1")
    if return_count > 8 and not args.allow_large_return_count:
        raise PipelineError("--return-count > 8 is blocked by default. Use --allow-large-return-count only for controlled testing.")

    per_provider_count = args.per_provider_count or defaults.get("per_provider_count", 10)
    if per_provider_count > 20 and not args.allow_large_per_provider_count:
        raise PipelineError("--per-provider-count > 20 is blocked by default.")

    mode_provider_budget = int(defaults.get("total_provider_count", per_provider_count * len(requested_sources)))
    budget_policy = {
        "mode_default_total_provider_budget": mode_provider_budget,
        "provider_total_budget": mode_provider_budget,
        "override": False,
        "source": "mode_default",
        "per_provider_cap": args.per_provider_count,
    }
    if args.provider_total_budget is not None:
        if not args.allow_provider_total_budget_override:
            raise PipelineError("--provider-total-budget is debug-only. Pass --allow-provider-total-budget-override for controlled testing.")
        if args.provider_total_budget < len(requested_sources):
            raise PipelineError("--provider-total-budget must be at least the number of requested sources.")
        budget_policy.update({"provider_total_budget": int(args.provider_total_budget), "override": True, "source": "debug_override"})

    provider_weights = {s: preset_weights.get(s, 1.0) for s in requested_sources}
    provider_counts = allocate_provider_counts(requested_sources, provider_weights, int(budget_policy["provider_total_budget"]))
    provider_counts = cap_provider_counts(provider_counts, args.per_provider_count)
    explicit_provider_counts = parse_provider_counts(args.provider_counts)
    provider_counts.update(explicit_provider_counts)
    unknown_count_sources = [s for s in provider_counts if s not in requested_sources]
    if unknown_count_sources:
        raise PipelineError(f"Provider counts specified for non-requested sources: {unknown_count_sources}")
    allocated_total_provider_count = sum(provider_counts.values())
    if allocated_total_provider_count > int(budget_policy["provider_total_budget"]) and not args.allow_provider_total_budget_override:
        raise PipelineError(
            f"Provider counts total {allocated_total_provider_count} exceeds mode budget {budget_policy['provider_total_budget']}. "
            "Use --provider-total-budget with --allow-provider-total-budget-override for controlled debugging."
        )
    budget_policy["allocated_total_provider_count"] = allocated_total_provider_count
    budget_policy["explicit_provider_counts"] = bool(explicit_provider_counts)

    oversized = {s: c for s, c in provider_counts.items() if c > 20}
    if oversized and not (args.allow_large_per_provider_count or args.allow_provider_total_budget_override):
        raise PipelineError(f"provider_counts > 20 are blocked by default: {oversized}")

    params = {
        "query": args.query,
        "mode": mode,
        "provider_preset": preset_name,
        "provider_preset_source": preset_source,
        "sources": requested_sources,
        "per_provider_count": per_provider_count,
        "provider_counts": provider_counts,
        "total_provider_count": allocated_total_provider_count if provider_counts else None,
        "budget_policy": budget_policy,
        "return_count": return_count,
        "summary_count": int(args.return_count or defaults.get("summary_count", return_count)),
        "search_depth": args.search_depth,
        "freshness": args.freshness,
        "date_after": args.date_after,
        "date_before": args.date_before,
        "topic": args.topic,
        "include_domains": split_csv(args.include_domains),
        "exclude_domains": split_csv(args.exclude_domains),
        "locale": args.locale,
        "language": args.language,
        "country": args.country,
        "include_provider_answer": args.include_provider_answer,
        "timeout_sec": args.timeout_sec,
        "brief": brief.to_dict() if brief.any() else None,
        "ranking_query_mode": "brief_aware" if mode == "deep" else "query_only",
        "ranking_context": ranking_query if mode == "deep" else None,
        "ranking_context_path": "ranking_context.txt" if mode == "deep" else None,
    }

    write_stage(run_dir, "collect", "started", sources=requested_sources, provider_counts=provider_counts, per_provider_count=per_provider_count)
    raw_candidates, source_reports = collect_all(source_specs, params)
    if not raw_candidates:
        write_stage(run_dir, "collect", "failed", source_reports=source_reports)
        raise PipelineError(f"No candidates collected. Source reports: {source_reports}")
    write_stage(run_dir, "collect", "ok", count=len(raw_candidates), source_reports=source_reports)

    time_params = dict(params)
    time_params["provider_capabilities"] = capabilities_by_provider
    time_params["provider_supports"] = {spec.id: spec.config.get("supports", {}) for spec in source_specs}
    time_filtered, time_filter_report = normalize_and_filter_dates(raw_candidates, time_params)
    if not time_filtered:
        write_stage(run_dir, "time_filter", "failed", report=time_filter_report)
        raise PipelineError(f"No candidates after time filter. Time filter report: {time_filter_report}. Source reports: {source_reports}")
    write_stage(run_dir, "time_filter", "ok", count=len(time_filtered), report=time_filter_report)

    deduped, dedupe_report = dedupe(time_filtered)
    write_stage(run_dir, "dedupe", "ok", count=len(deduped), report=dedupe_report)
    score_candidates(
        deduped,
        args.query,
        cfg["scoring"],
        include_domains=params["include_domains"],
        exclude_domains=params["exclude_domains"],
        prefer_domains=[],
    )

    max_rerank_candidates = min(int(cfg["reranker"].get("max_candidates", 40)), int(budget_policy["provider_total_budget"]))
    prescreened = sorted(deduped, key=lambda c: c.rule_score, reverse=True)[:max_rerank_candidates]
    summary_count = int(params["summary_count"])
    write_stage(run_dir, "summary_rerank", "started", input_count=len(prescreened), summary_count=summary_count)
    ranked, rerank_report = apply_rerank(ranking_query, prescreened, cfg["reranker"], return_count=summary_count, allow_rule_fallback=args.allow_rule_fallback)
    write_stage(run_dir, "summary_rerank", "ok", input_count=len(prescreened), output_count=min(summary_count, len(ranked)), report=rerank_report)
    top = ranked[:summary_count]

    fetch_mode = "off" if args.no_fetch else defaults.get("fetch_mode", args.fetch_mode)
    fetcher_cfg = dict(cfg["fetcher"])
    fetcher_cfg["provider_capabilities"] = capabilities_by_provider
    fetcher_cfg["provider_source_paths"] = {spec.id: str(spec.path) for spec in source_specs}
    fetch_enabled = True
    cleanup_report = {"enabled": bool(cfg["content_cleaner"].get("enabled", True)), "processed": 0, "original_chars": 0, "cleaned_chars": 0, "dropped_noise_lines": 0, "dropped_short_lines": 0, "dropped_duplicate_lines": 0}
    deferred_fetch_report: dict[str, Any] = {"started": False}
    body_rerank_report: dict[str, Any] | None = None
    source_eval_report: dict[str, Any] | None = None
    deep_article_report: dict[str, Any] | None = None
    if mode in ("full", "deep") and fetch_enabled and fetch_mode != "off":
        write_stage(run_dir, "fetch_bodies", "started", count=len(top))
        top = fetch_top(top, fetcher_cfg, enabled=True)
        top, cleanup_report = clean_top_content(top, cfg["content_cleaner"])
        for c in top:
            if c.fetch_status == "ok":
                cache_payload = save_candidate_cache(c)
                c.extra["fetch_cache_key"] = cache_payload["cache_key"]
        write_stage(run_dir, "fetch_bodies", "ok", fetched_ok=sum(1 for c in top if c.fetch_status == "ok"), cleanup=cleanup_report)
        body_candidates = [c for c in top if c.fetched_content]
        write_stage(run_dir, "body_rerank", "started", input_count=len(body_candidates), return_count=return_count)
        body_ranked, body_rerank_report = apply_body_rerank(ranking_query, body_candidates or top, cfg["reranker"], allow_rule_fallback=args.allow_rule_fallback)
        top = body_ranked[:return_count]
        write_stage(run_dir, "body_rerank", "ok", output_count=len(top), report=body_rerank_report)

        write_stage(run_dir, "source_eval", "started", input_count=len(top), model=cfg["deep_analyzer"].get("model"))
        try:
            source_eval_report = analyze_source_quality(
                ranking_query=ranking_query,
                query=args.query,
                brief=brief,
                candidates=top,
                cfg=cfg["deep_analyzer"],
            )
            attach_analysis(top, source_eval_report)
            apply_source_quality_blend(top, cfg["deep_analyzer"])
            top = sorted(top, key=lambda c: c.final_score, reverse=True)[:return_count]
            eval_items = ((source_eval_report.get("analysis") or {}).get("items") or [])
            write_stage(
                run_dir,
                "source_eval",
                "ok" if source_eval_report.get("ok") else "partial",
                output_count=len(eval_items),
                input_count=len(top),
                report={k: source_eval_report.get(k) for k in ("ok", "model", "usage")},
            )
        except Exception as exc:
            source_eval_report = {"enabled": True, "ok": False, "error": str(exc)}
            write_stage(run_dir, "source_eval", "failed", error=str(exc))
            top = top[:return_count]

        if mode == "deep":
            write_stage(run_dir, "deep_article", "started", input_count=len(top), model=cfg["deep_analyzer"].get("model"))
            try:
                if not cfg["deep_analyzer"].get("enabled", False):
                    if not args.allow_analysis_fallback:
                        raise PipelineError("deep analysis model is not configured; use --allow-analysis-fallback for a deterministic evidence outline")
                    deep_article_report = fallback_deep_article(args.query, top)
                else:
                    deep_article_report = generate_deep_article(
                        query=args.query,
                        ranking_query=ranking_query,
                        brief=brief,
                        candidates=top,
                        cfg=cfg["deep_analyzer"],
                    )
                (run_dir / "deep_article.md").write_text(deep_article_report["markdown"], encoding="utf-8")
                (run_dir / "deep_article.json").write_text(
                    json.dumps(deep_article_report["json"], ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                write_stage(run_dir, "deep_article", "ok", usage=deep_article_report.get("usage"))
            except Exception as exc:
                write_stage(run_dir, "deep_article", "failed", error=str(exc))
                raise PipelineError(f"deep_article generation failed: {exc}") from exc
    elif fetch_mode == "sync" and fetch_enabled:
        write_stage(run_dir, "fetch_bodies", "started", count=len(top))
        top = fetch_top(top[:return_count], fetcher_cfg, enabled=True)
        top, cleanup_report = clean_top_content(top, cfg["content_cleaner"])
        for c in top:
            if c.fetch_status == "ok":
                cache_payload = save_candidate_cache(c)
                c.extra["fetch_cache_key"] = cache_payload["cache_key"]
        write_stage(run_dir, "fetch_bodies", "ok", fetched_ok=sum(1 for c in top if c.fetch_status == "ok"), cleanup=cleanup_report)
    elif fetch_mode == "defer" and fetch_enabled:
        top = apply_inline_content(top[:return_count], fetcher_cfg)
        top, cleanup_report = clean_top_content(top, cfg["content_cleaner"])
        for c in top:
            if c.fetch_status == "inline_content":
                continue
            if external_fetch_allowed(c, fetcher_cfg):
                c.fetch_status = "queued"
                c.extra["fetch_cache_key"] = cache_key_for_candidate(c)
            else:
                c.fetch_status = "external_fetch_disabled"
                c.fetch_error = "provider does not allow external body fetch"
    else:
        top = fetch_top(top[:return_count], fetcher_cfg, enabled=False)

    pipeline_report = {
        "run_id": run_id,
        "collected": len(raw_candidates),
        "after_time_filter": len(time_filtered),
        "after_dedupe": len(deduped),
        "rerank_input": len(prescreened),
        "returned": len(top),
        "fetched_ok": sum(1 for c in top if c.fetch_status == "ok"),
        "fetched_failed": sum(1 for c in top if c.fetch_status == "failed"),
        "fetch_auth_required": sum(1 for c in top if c.fetch_status == "auth_required"),
        "fetch_enabled": fetch_enabled and fetch_mode != "off",
        "fetch_mode": fetch_mode,
        "budget_policy": budget_policy,
        "provider_capabilities": capabilities_by_provider,
        "deferred_fetch": deferred_fetch_report,
        "body_rerank": body_rerank_report,
        "source_eval": {k: source_eval_report.get(k) for k in ("enabled", "ok", "provider", "model", "usage")} if source_eval_report else None,
        "deep_article": {"ok": bool(deep_article_report), "path": "deep_article.md"} if mode == "deep" else None,
        "content_cleanup": cleanup_report,
        "reranker_ok": rerank_report.get("ok"),
        "dedupe": dedupe_report,
        "time_filter": time_filter_report,
        "source_reports": source_reports,
    }

    if cfg["retention"].get("save_candidates", True):
        write_jsonl(run_dir / "candidates.jsonl", [c.to_dict() for c in raw_candidates])
    if cfg["retention"].get("save_deduped", True):
        write_jsonl(run_dir / "deduped.jsonl", [c.to_dict() for c in deduped])
    if cfg["retention"].get("save_reranked", True):
        write_jsonl(run_dir / "reranked.jsonl", [c.to_dict() for c in ranked])
    if body_rerank_report is not None:
        write_jsonl(run_dir / "body_reranked.jsonl", [c.to_dict() for c in top])
    if source_eval_report is not None:
        (run_dir / "source_eval.json").write_text(json.dumps(source_eval_report, ensure_ascii=False, indent=2), encoding="utf-8")
    if cfg["retention"].get("save_fetch_status", True):
        write_jsonl(
            run_dir / "fetch_status.jsonl",
            [
                {
                    "index": i,
                    "id": c.id,
                    "title": c.title,
                    "url": c.url,
                    "normalized_url": c.normalized_url,
                    "provider": c.provider,
                    "status": c.fetch_status,
                    "fetch_status": c.fetch_status,
                    "error": c.fetch_error,
                    "fetch_error": c.fetch_error,
                    "cache_key": c.extra.get("fetch_cache_key") or cache_key_for_candidate(c),
                }
                for i, c in enumerate(top)
            ],
        )
    if fetch_mode == "defer" and fetch_enabled:
        deferred_fetch_report.update(start_deferred_fetch(run_dir, return_count))
    run_json = {
        "run_id": run_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "params": params,
        "brief": brief.to_dict() if brief.any() else None,
        "pipeline": pipeline_report,
        "reranker": rerank_report,
    }
    (run_dir / "run.json").write_text(json.dumps(run_json, ensure_ascii=False, indent=2), encoding="utf-8")
    md = evidence_markdown(args.query, params, pipeline_report, top)
    if cfg["retention"].get("save_evidence", True):
        (run_dir / "evidence.md").write_text(md, encoding="utf-8")
    cleanup(cfg["retention"])
    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "markdown": md,
        "top": [agent_candidate_result(c) for c in top],
        "pipeline": agent_pipeline_summary(pipeline_report),
    }
