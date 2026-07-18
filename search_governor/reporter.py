from __future__ import annotations
import json
from pathlib import Path
from .models import Candidate


def _clip(value, max_chars: int = 1200) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def write_jsonl(path: Path, items: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def content_source_summary(c: Candidate) -> dict:
    granularity = c.extra.get("content_granularity") if isinstance(c.extra, dict) else None
    if c.fetch_status == "inline_content":
        return {
            "kind": "provider_inline",
            "label": f"provider-supplied {granularity or 'snippet'}; not a full-document fetch",
            "heading": "Provider-supplied excerpt",
            "is_full_document": False,
        }
    if c.fetch_status == "snippet_fallback":
        return {
            "kind": "snippet_fallback",
            "label": "search snippet fallback; not a full-document fetch",
            "heading": "Snippet fallback",
            "is_full_document": False,
        }
    if c.fetch_status == "ok":
        return {
            "kind": "fetched_body",
            "label": "externally or natively fetched body",
            "heading": "Fetched content",
            "is_full_document": None,
        }
    if c.fetch_status == "auth_required":
        return {
            "kind": "auth_required",
            "label": "manual authentication or verification required before body fetch",
            "heading": "Content",
            "is_full_document": None,
        }
    return {
        "kind": c.fetch_status or "unknown",
        "label": c.fetch_status or "unknown",
        "heading": "Content",
        "is_full_document": None,
    }


def agent_pipeline_summary(pipeline: dict) -> dict:
    cleanup = pipeline.get("content_cleanup") if isinstance(pipeline.get("content_cleanup"), dict) else {}
    source_eval = pipeline.get("source_eval") if isinstance(pipeline.get("source_eval"), dict) else None
    body_rerank = pipeline.get("body_rerank") if isinstance(pipeline.get("body_rerank"), dict) else None
    deep_article = pipeline.get("deep_article") if isinstance(pipeline.get("deep_article"), dict) else None
    deferred = pipeline.get("deferred_fetch") if isinstance(pipeline.get("deferred_fetch"), dict) else {}
    return {
        "run_id": pipeline.get("run_id"),
        "collected": pipeline.get("collected"),
        "after_dedupe": pipeline.get("after_dedupe"),
        "returned": pipeline.get("returned"),
        "fetch_mode": pipeline.get("fetch_mode"),
        "fetch_enabled": pipeline.get("fetch_enabled"),
        "budget_policy": pipeline.get("budget_policy"),
        "fetched_ok": pipeline.get("fetched_ok"),
        "fetched_failed": pipeline.get("fetched_failed"),
        "fetch_auth_required": pipeline.get("fetch_auth_required"),
        "deferred_fetch_started": bool(deferred.get("started")),
        "content_cleanup": {
            "enabled": cleanup.get("enabled"),
            "processed": cleanup.get("processed"),
            "original_chars": cleanup.get("original_chars"),
            "cleaned_chars": cleanup.get("cleaned_chars"),
        },
        "body_rerank": {k: body_rerank.get(k) for k in ("enabled", "ok", "model")} if body_rerank else None,
        "source_eval": {k: source_eval.get(k) for k in ("enabled", "ok", "model")} if source_eval else None,
        "deep_article": deep_article,
        "reranker_ok": pipeline.get("reranker_ok"),
    }


def agent_candidate_result(c: Candidate) -> dict:
    source_eval = (c.extra.get("source_eval") or c.extra.get("deep_analysis")) if isinstance(c.extra, dict) else None
    cleanup = c.extra.get("content_cleanup") if isinstance(c.extra, dict) else None
    content_source = content_source_summary(c)
    result = {
        "rank": c.rank,
        "title": c.title,
        "url": c.url,
        "domain": c.domain,
        "provider": c.provider,
        "published_at": c.published_at,
        "language": c.language,
        "snippet": c.snippet,
        "fetch_status": c.fetch_status,
        "content_source": content_source,
        "fetched_title": c.fetched_title,
        "fetched_content": c.fetched_content,
    }
    if cleanup:
        result["content_cleanup"] = {
            "original_chars": cleanup.get("original_chars"),
            "cleaned_chars": cleanup.get("cleaned_chars"),
        }
    if source_eval:
        result["source_eval"] = {
            "source_type": source_eval.get("source_type"),
            "source_quality_score": source_eval.get("source_quality_score"),
            "evidence_value_score": source_eval.get("evidence_value_score"),
            "context_match_score": source_eval.get("context_match_score"),
            "question_contribution_score": source_eval.get("question_contribution_score"),
            "covered_questions": source_eval.get("covered_questions"),
            "recommended_action": source_eval.get("recommended_action"),
            "boundary_violation": source_eval.get("boundary_violation"),
            "risk_flags": source_eval.get("risk_flags"),
            "reason": source_eval.get("reason"),
        }
    return {k: v for k, v in result.items() if v not in (None, "", [])}


def evidence_markdown(query: str, params: dict, pipeline: dict, candidates: list[Candidate]) -> str:
    lines: list[str] = []
    lines.append("# Aggregated Search Result\n")
    lines.append("## Query\n")
    lines.append(query + "\n")
    lines.append("## Parameters\n")
    for k in ["mode", "provider_preset", "provider_preset_source", "sources", "per_provider_count", "provider_counts", "total_provider_count", "budget_policy", "return_count", "summary_count", "search_depth", "freshness", "date_after", "date_before", "topic", "locale", "language", "country"]:
        if k in params and params[k] not in (None, "", []):
            lines.append(f"- {k}: {params[k]}")
    lines.append("")
    brief = params.get("brief") if isinstance(params.get("brief"), dict) else None
    if brief:
        lines.append("## Deep Brief / 深度问题单\n")
        for label, key in [
            ("点问题", "point_question"),
            ("目标", "goal"),
            ("必答清单", "must_answer"),
            ("必要上下文", "necessary_context"),
            ("搜索边界", "boundaries"),
            ("输出用途", "output_use"),
        ]:
            if brief.get(key):
                lines.append(f"- {label}: {_clip(brief.get(key), 500)}")
        lines.append("")
    ranking_context = params.get("ranking_context")
    if ranking_context:
        lines.append("## Ranking Context / 排序任务卡\n")
        lines.append(_clip(ranking_context, 1200))
        lines.append("")
    lines.append("## Pipeline\n")
    public_pipeline = agent_pipeline_summary(pipeline)
    for k, v in public_pipeline.items():
        if v not in (None, "", [], {}):
            lines.append(f"- {k}: {v}")
    deep_article = pipeline.get("deep_article") if isinstance(pipeline, dict) else None
    if params.get("mode") == "deep" and deep_article:
        lines.append("")
        lines.append("Deep article:")
        lines.append(f"- data/runs/{pipeline.get('run_id')}/{deep_article.get('path', 'deep_article.md')}")
    lines.append("")
    lines.append("## Results\n")
    for i, c in enumerate(candidates, 1):
        lines.append(f"### {i}. {c.title}\n")
        lines.append(f"- URL: {c.url}")
        lines.append(f"- Domain: {c.domain}")
        lines.append(f"- Provider: {c.provider}")
        lines.append(f"- Fetch status: {c.fetch_status}")
        content_source = content_source_summary(c)
        lines.append(f"- Content source: {content_source['label']}")
        if c.fetch_error:
            lines.append(f"- Fetch error: {c.fetch_error}")
        cleanup = c.extra.get("content_cleanup") if isinstance(c.extra, dict) else None
        if cleanup:
            lines.append(f"- Content cleanup: {cleanup.get('original_chars', 0)} -> {cleanup.get('cleaned_chars', 0)} chars")
        source_eval = (c.extra.get("source_eval") or c.extra.get("deep_analysis")) if isinstance(c.extra, dict) else None
        if source_eval:
            lines.append(f"- Source type: {source_eval.get('source_type')}")
            lines.append(f"- Source quality score: {source_eval.get('source_quality_score')}")
            lines.append(f"- Evidence value score: {source_eval.get('evidence_value_score')}")
            lines.append(f"- Context match score: {source_eval.get('context_match_score')}")
            if source_eval.get("question_contribution_score") is not None:
                lines.append(f"- Question contribution score: {source_eval.get('question_contribution_score')}")
            if source_eval.get("covered_questions"):
                lines.append(f"- Covered questions: {source_eval.get('covered_questions')}")
            lines.append(f"- Recommended action: {source_eval.get('recommended_action')}")
            if source_eval.get("boundary_violation") is not None:
                lines.append(f"- Boundary violation: {source_eval.get('boundary_violation')}")
            if source_eval.get("risk_flags"):
                lines.append(f"- Risk flags: {source_eval.get('risk_flags')}")
            if source_eval.get("reason"):
                lines.append(f"- Source eval reason: {source_eval.get('reason')}")
        lines.append("\n#### Search snippet\n")
        lines.append((c.snippet or "").strip() + "\n")
        if c.fetched_content:
            lines.append(f"#### {content_source['heading']}\n")
            lines.append(c.fetched_content.strip() + "\n")
        lines.append("")
    return "\n".join(lines)
