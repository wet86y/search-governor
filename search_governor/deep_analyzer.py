from __future__ import annotations

import concurrent.futures
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .models import Candidate


class DeepAnalyzerError(RuntimeError):
    pass


def _safe_excerpt(text: str | None, body_chars: int, full_cap: int = 12000) -> str:
    raw = text or ""
    if body_chars <= 0:
        return raw[:full_cap]
    return raw[:body_chars]


def _query_terms(*texts: str) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for text in texts:
        for token in re.findall(r"[A-Za-z0-9_+\-.]{2,}|[\u4e00-\u9fff]{2,}", text or ""):
            key = token.casefold()
            if key not in seen:
                seen.add(key)
                terms.append(key)
    return terms[:24]


def _split_blocks(text: str) -> list[str]:
    blocks = [b.strip() for b in re.split(r"\n\s*\n+", text or "") if b.strip()]
    if len(blocks) <= 1:
        blocks = [b.strip() for b in (text or "").splitlines() if b.strip()]
    return blocks


def _domain_path(url: str) -> tuple[str, str]:
    try:
        parsed = urllib.parse.urlparse(url or "")
    except Exception:
        return "", ""
    return (parsed.netloc or "").lower(), (parsed.path or "").lower()


def _source_hints(c: Candidate) -> dict[str, Any]:
    domain = (c.domain or _domain_path(c.url)[0]).lower()
    _url_domain, path = _domain_path(c.url)
    title = (c.title or "").casefold()
    matched: list[str] = []
    hints = {
        "official_like": False,
        "release_like": False,
        "repo_like": False,
        "forum_like": False,
        "news_like": False,
        "blog_like": False,
        "aggregator_like": False,
        "social_like": False,
        "generic": True,
        "confidence": 0.0,
        "matched_rules": matched,
        "budget_hint": "default",
        "noise_prior": "unknown",
    }

    def mark(key: str, confidence: float, rule: str) -> None:
        hints[key] = True
        hints["generic"] = False
        hints["confidence"] = max(float(hints["confidence"]), confidence)
        matched.append(rule)

    if domain.startswith("docs.") or domain in {"docs.python.org", "developer.mozilla.org"}:
        mark("official_like", 0.9, "domain:docs")
    if any(part in path for part in ("/docs", "/reference", "/cli", "/plugins", "/guide", "/manual")):
        mark("official_like", 0.75, "path:docs-like")
    if "github.com" in domain and ("/releases" in path or "release" in title or "changelog" in title):
        mark("release_like", 0.9, "github:release")
    elif any(term in path or term in title for term in ("release-notes", "changelog", "releases")):
        mark("release_like", 0.75, "path:title-release")
    if "github.com" in domain and not hints["release_like"]:
        mark("repo_like", 0.75, "domain:github")
    if any(site in domain for site in ("reddit.com", "stackoverflow.com", "facebook.com")) or "discussions" in path:
        mark("forum_like", 0.75, "domain:path-forum")
    if any(site in domain for site in ("news.qq.com", "36kr.com", "techcrunch.com", "theverge.com")):
        mark("news_like", 0.7, "domain:news")
    if any(site in domain for site in ("csdn.net", "cnblogs.com", "medium.com", "segmentfault.com")) or any(part in path for part in ("/blog", "/article", "/posts")):
        mark("blog_like", 0.65, "domain:path-blog")
    if any(site in domain for site in ("so.html5.qq.com", "baike.sogou.com")) or "search_news" in path:
        mark("aggregator_like", 0.8, "domain:path-aggregator")
    if any(site in domain for site in ("x.com", "twitter.com", "linkedin.com", "youtube.com")):
        mark("social_like", 0.7, "domain:social")

    if hints["official_like"] or hints["release_like"]:
        hints["budget_hint"] = "expand"
        hints["noise_prior"] = "low"
    elif hints["aggregator_like"]:
        hints["budget_hint"] = "shrink"
        hints["noise_prior"] = "high"
    elif not hints["generic"]:
        hints["noise_prior"] = "medium"
    return hints


_NOISE_TERMS = (
    "相关阅读",
    "相关推荐",
    "热门推荐",
    "登录",
    "注册",
    "广告",
    "分享",
    "评论",
    "免责声明",
    "copyright",
    "all rights reserved",
    "subscribe",
)


def _block_noise_score(block: str) -> float:
    lower = block.casefold()
    score = 0.0
    if len(block) < 40:
        score += 0.25
    if sum(lower.count(term.casefold()) for term in _NOISE_TERMS):
        score += 0.35
    linkish = lower.count("http://") + lower.count("https://") + lower.count("www.")
    if linkish >= 3:
        score += 0.25
    return min(score, 1.0)


def _query_match_score(block: str, terms: list[str]) -> int:
    lower = block.casefold()
    return sum(lower.count(term) for term in terms)


def _dense_score(block: str) -> float:
    if not block:
        return 0.0
    punctuation = sum(block.count(ch) for ch in "，。；,.!?！？")
    return min(1.0, (len(block) / 500.0) + min(0.3, punctuation / 30.0))


def _append_budgeted(parts: list[str], text: str, budget: int) -> bool:
    current = len("\n\n".join(parts))
    if current >= budget:
        return False
    remaining = budget - current - (2 if parts else 0)
    if remaining <= 40:
        return False
    clipped = text[:remaining].rstrip()
    if clipped:
        parts.append(clipped)
        return True
    return False


def _select_body_passages(text: str | None, query: str, ranking_query: str, body_chars: int, full_cap: int) -> tuple[str, dict[str, Any]]:
    raw = (text or "").strip()
    empty_meta = {
        "strategy": "lightweight_deterministic",
        "body_chars": body_chars,
        "full_cap": full_cap,
        "total_blocks": 0,
        "query_hit_blocks": 0,
        "fallback_used": False,
        "selected": [],
        "total_chars": 0,
    }
    if not raw:
        return "", empty_meta
    if body_chars <= 0 or len(raw) <= body_chars:
        excerpt = raw[:full_cap]
        meta = {**empty_meta, "total_blocks": len(_split_blocks(raw)), "strategy": "full_or_uncapped", "total_chars": len(excerpt)}
        return excerpt, meta

    lead_budget = min(700, max(300, body_chars // 3))
    blocks = _split_blocks(raw)
    terms = _query_terms(query, ranking_query)

    parts: list[str] = []
    selected: list[dict[str, Any]] = []
    lead_parts: list[str] = []
    for idx, block in enumerate(blocks):
        if _block_noise_score(block) >= 0.6:
            continue
        if len("\n\n".join(lead_parts)) >= lead_budget:
            break
        if _append_budgeted(lead_parts, block, lead_budget):
            selected.append({"role": "lead", "block_index": idx, "chars": min(len(block), lead_budget), "query_score": _query_match_score(block, terms)})
    if lead_parts:
        parts.append("[lead_context]\n" + "\n\n".join(lead_parts))

    scored: list[tuple[float, int, int, str]] = []
    for idx, block in enumerate(blocks):
        q_score = _query_match_score(block, terms)
        if q_score <= 0:
            continue
        noise = _block_noise_score(block)
        score = q_score * 2.0 + _dense_score(block) - noise - min(idx, 20) * 0.01
        scored.append((score, q_score, -idx, block))

    seen_blocks = {" ".join(part.casefold().split())[:180] for part in lead_parts}
    query_parts: list[str] = []
    for _score, q_score, neg_idx, block in sorted(scored, reverse=True):
        key = " ".join(block.casefold().split())[:180]
        if key in seen_blocks:
            continue
        clipped = block[:500].rstrip()
        if _append_budgeted(query_parts, clipped, max(500, body_chars - len("\n\n".join(parts)) - 80)):
            seen_blocks.add(key)
            selected.append({"role": "query_hit", "block_index": -neg_idx, "chars": len(clipped), "query_score": q_score})
        if len(query_parts) >= 4:
            break
    if query_parts:
        parts.append("[query_matched_passages]\n" + "\n\n".join(query_parts))

    fallback_used = False
    if len("\n\n".join(parts)) < body_chars * 0.65:
        dense_parts: list[str] = []
        dense_blocks = sorted(
            ((1.0 - _block_noise_score(block) + _dense_score(block), -idx, block) for idx, block in enumerate(blocks)),
            reverse=True,
        )
        for _score, neg_idx, block in dense_blocks:
            key = " ".join(block.casefold().split())[:180]
            if key in seen_blocks:
                continue
            clipped = block[:500].rstrip()
            if _append_budgeted(dense_parts, clipped, max(300, body_chars - len("\n\n".join(parts)) - 80)):
                seen_blocks.add(key)
                fallback_used = True
                selected.append({"role": "dense_fallback", "block_index": -neg_idx, "chars": len(clipped), "query_score": _query_match_score(block, terms)})
            if len(dense_parts) >= 2:
                break
        if dense_parts:
            parts.append("[fallback_dense_passages]\n" + "\n\n".join(dense_parts))

    excerpt = "\n\n".join(parts)[:body_chars].rstrip()
    meta = {
        "strategy": "lightweight_deterministic",
        "body_chars": body_chars,
        "full_cap": full_cap,
        "total_blocks": len(blocks),
        "query_hit_blocks": len(scored),
        "fallback_used": fallback_used or not query_parts,
        "selected": selected,
        "total_chars": len(excerpt),
    }
    return excerpt, meta


def _compress_body_for_eval(text: str | None, query: str, ranking_query: str, body_chars: int, full_cap: int) -> str:
    excerpt, _meta = _select_body_passages(text, query, ranking_query, body_chars, full_cap)
    return excerpt


def _content_source_metadata(c: Candidate) -> dict[str, Any]:
    granularity = c.extra.get("content_granularity") if isinstance(c.extra, dict) else None
    if c.fetch_status == "inline_content":
        return {
            "kind": "provider_inline",
            "granularity": granularity or "snippet",
            "is_full_document": False,
            "note": "Provider-supplied excerpt/snippet for analysis, not a full-document fetch.",
        }
    if c.fetch_status == "snippet_fallback":
        return {
            "kind": "snippet_fallback",
            "granularity": "snippet",
            "is_full_document": False,
            "note": "Search snippet fallback, not a full-document fetch.",
        }
    if c.fetch_status == "ok":
        return {
            "kind": "fetched_body",
            "granularity": granularity,
            "is_full_document": None,
            "note": "Externally or natively fetched content; may still be truncated by configured character budgets.",
        }
    return {
        "kind": c.fetch_status or "unknown",
        "granularity": granularity,
        "is_full_document": None,
        "note": "Content availability is determined by fetch_status.",
    }


def _candidate_payload(c: Candidate, index: int, query: str, ranking_query: str, body_chars: int, full_cap: int) -> dict[str, Any]:
    body_excerpt, passage_selection = _select_body_passages(c.fetched_content, query, ranking_query, body_chars, full_cap)
    source_hints = _source_hints(c)
    content_source = _content_source_metadata(c)
    c.extra["source_hints"] = source_hints
    c.extra["passage_selection"] = passage_selection
    c.extra["content_source"] = content_source
    return {
        "rank": index,
        "title": c.title,
        "url": c.url,
        "provider": c.provider,
        "domain": c.domain,
        "published_at": c.published_at,
        "snippet": c.snippet,
        "content_source": content_source,
        "source_hints": source_hints,
        "passage_selection": passage_selection,
        "body_excerpt": body_excerpt,
        "scores": {
            "rule_score": c.rule_score,
            "summary_rerank_score": c.extra.get("summary_rerank_score"),
            "body_rerank_score": c.extra.get("body_rerank_score"),
            "final_score": c.final_score,
        },
    }


def _extract_json(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty response")
    try:
        return json.loads(text)
    except Exception:
        pass
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S | re.I)
    if fenced:
        return json.loads(fenced.group(1).strip())
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("no JSON object found")


def _truncate_body_excerpts(value: Any, max_body_chars: int) -> int:
    truncated = 0
    if isinstance(value, dict):
        if isinstance(value.get("body_excerpt"), str) and len(value["body_excerpt"]) > max_body_chars:
            value["body_excerpt"] = value["body_excerpt"][:max_body_chars].rstrip() + "\n[TRUNCATED_BY_INPUT_AUDIT]"
            truncated += 1
        for child in value.values():
            truncated += _truncate_body_excerpts(child, max_body_chars)
    elif isinstance(value, list):
        for child in value:
            truncated += _truncate_body_excerpts(child, max_body_chars)
    return truncated


def _audit_chat_payload(user_payload: dict[str, Any], cfg: dict[str, Any], *, max_input_chars_key: str = "max_input_chars") -> tuple[dict[str, Any], dict[str, Any]]:
    max_chars = int(cfg.get(max_input_chars_key, cfg.get("max_input_chars", 100000)))
    text = json.dumps(user_payload, ensure_ascii=False)
    audit = {
        "enabled": True,
        "model_context_hint": cfg.get("model_context_hint", "128k"),
        "max_input_chars": max_chars,
        "original_chars": len(text),
        "truncated": False,
        "truncated_body_excerpts": 0,
    }
    if len(text) <= max_chars:
        return user_payload, audit

    payload = json.loads(text)
    body_budget = max(400, int(cfg.get("audit_body_excerpt_chars", 1200)))
    truncated = _truncate_body_excerpts(payload, body_budget)
    text = json.dumps(payload, ensure_ascii=False)
    audit.update({"truncated": True, "truncated_body_excerpts": truncated, "final_chars": len(text)})
    if len(text) > max_chars:
        audit["error"] = f"chat input exceeds configured audit budget after truncation: {len(text)}>{max_chars}"
        raise DeepAnalyzerError(audit["error"])
    audit["error"] = f"chat input exceeded configured audit budget and body excerpts were truncated before model call: {audit['original_chars']}>{max_chars}"
    return payload, audit


def _chat_json(user_payload: dict[str, Any], cfg: dict[str, Any], *, max_tokens_key: str = "max_tokens", timeout_key: str = "timeout_sec", max_input_chars_key: str = "max_input_chars") -> tuple[Any, dict[str, Any]]:
    key_env = cfg.get("api_key_env", "SEARCH_GOVERNOR_ANALYSIS_API_KEY")
    api_key = os.environ.get(key_env, "").strip()
    if not api_key:
        raise DeepAnalyzerError(f"Missing API key env: {key_env}")
    api_base = str(cfg.get("api_base") or "").strip()
    model = str(cfg.get("model") or "").strip()
    if not api_base or not model:
        raise DeepAnalyzerError("Analyzer api_base and model must be configured")

    user_payload, input_audit = _audit_chat_payload(user_payload, cfg, max_input_chars_key=max_input_chars_key)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": cfg.get("system_prompt", "输出严格 JSON。")},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        "temperature": float(cfg.get("temperature", 0.2)),
        "max_tokens": int(cfg.get(max_tokens_key, cfg.get("max_tokens", 2200))),
    }
    if not cfg.get("enable_thinking", True):
        payload["enable_thinking"] = False
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        api_base,
        data=data,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    timeout = int(cfg.get(timeout_key, cfg.get("timeout_sec", 90)))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        msg = exc.read().decode("utf-8", errors="replace")[:1000]
        raise DeepAnalyzerError(f"Analyzer HTTP {exc.code}: {msg}") from exc
    except Exception as exc:
        raise DeepAnalyzerError(f"Analyzer request failed: {exc}") from exc

    raw["_input_audit"] = input_audit
    content = (((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    try:
        parsed = _extract_json(content)
    except Exception as exc:
        raise DeepAnalyzerError(f"DeepSeek JSON parse failed: {exc}; raw={content[:500]}") from exc
    return parsed, raw


def _eval_single(
    candidate: Candidate,
    index: int,
    query: str,
    ranking_query: str,
    brief,
    cfg: dict[str, Any],
    body_chars: int,
    full_cap: int,
) -> dict[str, Any] | None:
    """Evaluate one candidate. Returns its analysis item dict, or None on failure."""
    payload = {
        "query": query,
        "ranking_query": ranking_query,
        "brief": brief.to_dict() if brief and brief.any() else None,
        "task": "请对这条搜索结果做来源质量、证据价值、问题清单贡献、上下文匹配和边界违规判断。重点判断它是否能回答 brief.must_answer 中的必答问题。只依据给定的搜索结果和清洗正文，不要引入外部事实。",
        "output_schema": {
            "items": [
                {
                    "rank": 1,
                    "url": "结果 URL",
                    "source_type": "official_doc|github|blog|forum|news|vendor|social|unknown",
                    "source_quality_score": 0.0,
                    "evidence_value_score": 0.0,
                    "question_contribution_score": 0.0,
                    "covered_questions": ["问题编号或问题短名"],
                    "context_match_score": 0.0,
                    "boundary_violation": False,
                    "recommended_action": "read|keep|deprioritize|discard",
                    "risk_flags": [],
                    "reason": "一句话说明",
                }
            ],
        },
        "requirements": [
            "score 均为 0 到 1。",
            "question_contribution_score 表示该来源对必答清单的实际贡献，不能只按关键词相似度评分。",
            "covered_questions 只能从 brief.must_answer 或点问题中归纳，不要编造新问题；如果没有明确贡献，输出空数组。",
            "输出严格 JSON，不要 Markdown。",
        ],
        "results": [_candidate_payload(candidate, 1, query, ranking_query, body_chars, full_cap)],
    }
    try:
        parsed, raw = _chat_json(payload, cfg)
        if isinstance(parsed, list):
            items = parsed
        elif isinstance(parsed, dict):
            items = parsed.get("items", [parsed] if parsed.get("rank") else [])
        else:
            items = []
        return {
            "index": index,
            "item": items[0] if items else None,
            "usage": raw.get("usage", {}) if isinstance(raw, dict) else {},
            "input_audit": raw.get("_input_audit") if isinstance(raw, dict) else None,
        }
    except Exception as exc:
        print(f"source_eval: candidate #{index} failed: {exc}", file=sys.stderr)
        return None


def analyze_source_quality(
    ranking_query: str,
    query: str,
    brief,
    candidates: list[Candidate],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    if not cfg.get("enabled", True):
        return {"enabled": False, "ok": True, "analysis": {"items": []}}

    body_chars = int(cfg.get("source_eval_body_chars_per_result", cfg.get("body_chars_per_result", 8000)))
    full_cap = int(cfg.get("body_chars_full_cap", 12000))
    max_workers = max(1, len(candidates))

    items: list[dict] = []
    total_usage: dict[str, int] = {}
    input_audits: list[dict[str, Any]] = []
    pool_ok = True

    # Source eval: light structured classification, no thinking needed
    eval_cfg = {**cfg, "enable_thinking": False}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {
            pool.submit(_eval_single, c, i, query, ranking_query, brief, eval_cfg, body_chars, full_cap): i
            for i, c in enumerate(candidates, 1)
        }
        for fut in concurrent.futures.as_completed(futs):
            result = fut.result()
            if result is not None and result.get("item"):
                item = result["item"]
                item["rank"] = result["index"]
                items.append(item)
                if isinstance(result.get("input_audit"), dict):
                    input_audits.append(result["input_audit"])
                for k, v in result.get("usage", {}).items():
                    if isinstance(v, (int, float)):
                        total_usage[k] = total_usage.get(k, 0) + int(v)
            else:
                pool_ok = False

    items.sort(key=lambda x: x.get("rank", 9999))
    return {
        "enabled": True,
        "ok": pool_ok,
        "provider": cfg.get("provider", "openai_compatible_http"),
        "model": cfg.get("model"),
        "usage": total_usage,
        "input_audit": {
            "count": len(input_audits),
            "truncated": sum(1 for item in input_audits if item.get("truncated")),
            "max_original_chars": max((int(item.get("original_chars") or 0) for item in input_audits), default=0),
            "items": input_audits,
        },
        "analysis": {"items": items},
    }


def _to_float(value, default=0.0) -> float:
    try:
        x = float(value)
        if x < 0:
            return 0.0
        if x > 1:
            return 1.0
        return x
    except Exception:
        return default


def attach_analysis(candidates: list[Candidate], analysis: dict[str, Any]) -> None:
    parsed = analysis.get("analysis") if isinstance(analysis, dict) else None
    items = parsed.get("items", []) if isinstance(parsed, dict) else []
    by_url = {item.get("url"): item for item in items if isinstance(item, dict) and item.get("url")}
    by_rank = {item.get("rank"): item for item in items if isinstance(item, dict) and item.get("rank") is not None}

    for idx, candidate in enumerate(candidates, 1):
        item = by_url.get(candidate.url) or by_rank.get(idx)
        if item:
            candidate.extra["source_eval"] = item
            candidate.extra["deep_analysis"] = item


def apply_source_quality_blend(candidates: list[Candidate], cfg: dict[str, Any]) -> None:
    weights = cfg.get("score_weights", {})
    penalties = cfg.get("penalties", {})

    body_w = float(weights.get("body_rerank", 0.45))
    summary_w = float(weights.get("summary_rerank", 0.25))
    quality_w = float(weights.get("source_quality", 0.15))
    context_w = float(weights.get("context_match", 0.10))
    question_w = float(weights.get("question_contribution", 0.0))
    freshness_w = float(weights.get("freshness_or_source_prior", 0.05))

    boundary_penalty = float(penalties.get("boundary_violation", 0.30))
    context_mismatch_penalty = float(penalties.get("context_mismatch", 0.15))
    discard_penalty = float(penalties.get("discard_recommendation", 0.20))

    for c in candidates:
        source_eval = c.extra.get("source_eval") or {}

        body_score = _to_float(c.extra.get("body_rerank_score"), default=0.0)
        if body_score == 0.0:
            body_score = _to_float(c.final_score, default=0.0)

        summary_score = _to_float(c.extra.get("summary_final_score"), default=_to_float(c.final_score, 0.0))
        quality_score = _to_float(source_eval.get("source_quality_score"), default=0.5)
        context_score = _to_float(source_eval.get("context_match_score"), default=0.5)
        question_score = _to_float(source_eval.get("question_contribution_score"), default=context_score)

        source_prior = 0.0
        if c.domain in ("github.com", "docs.python.org", "readthedocs.io"):
            source_prior = 1.0
        elif any(x in (c.domain or "") for x in ["docs.", "developer.", "api."]):
            source_prior = 0.8
        else:
            source_prior = 0.5

        score = (
            body_w * body_score
            + summary_w * summary_score
            + quality_w * quality_score
            + context_w * context_score
            + question_w * question_score
            + freshness_w * source_prior
        )

        if source_eval.get("boundary_violation") is True:
            score -= boundary_penalty
            c.rank_reason.append("boundary_violation_penalty")
        if context_score < 0.3:
            score -= context_mismatch_penalty
            c.rank_reason.append("context_mismatch_penalty")
        if source_eval.get("recommended_action") == "discard":
            score -= discard_penalty
            c.rank_reason.append("discard_recommendation_penalty")

        c.extra["pre_source_eval_final_score"] = c.final_score
        c.final_score = max(0.0, score)
        c.rank_reason.append(f"source_quality_blend:{c.final_score:.4f}")


def _article_payload(query: str, ranking_query: str, brief, candidates: list[Candidate], cfg: dict[str, Any]) -> dict[str, Any]:
    body_chars = int(cfg.get("article_body_chars_per_result", cfg.get("body_chars_per_result", 8000)))
    full_cap = int(cfg.get("body_chars_full_cap", 12000))
    source_results = []
    for i, c in enumerate(candidates, 1):
        body_excerpt, passage_selection = _select_body_passages(c.fetched_content, query, ranking_query, body_chars, full_cap)
        source_hints = c.extra.get("source_hints") or _source_hints(c)
        content_source = c.extra.get("content_source") or _content_source_metadata(c)
        c.extra["source_hints"] = source_hints
        c.extra["content_source"] = content_source
        c.extra["article_passage_selection"] = passage_selection
        source_results.append(
            {
                "source_id": f"S{i}",
                "title": c.title,
                "url": c.url,
                "provider": c.provider,
                "domain": c.domain,
                "snippet": c.snippet,
                "content_source": content_source,
                "source_hints": source_hints,
                "passage_selection": passage_selection,
                "body_excerpt": body_excerpt,
                "scores": {
                    "final_score": c.final_score,
                    "summary_rerank_score": c.extra.get("summary_rerank_score"),
                    "body_rerank_score": c.extra.get("body_rerank_score"),
                },
                "source_eval": c.extra.get("source_eval"),
            }
        )
    return {
        "query": query,
        "ranking_query": ranking_query,
        "brief": brief.to_dict() if brief and brief.any() else None,
        "task": "你不是在写普通摘要。你正在为上层 agent 生成一份尽可能替代原文阅读的高密度研究材料。主 agent 默认应能只读本文回答用户问题；只有遇到明确风险条件时才需要回读原文。请围绕 brief 中的点问题、目标、必答清单、搜索边界和输出用途，分析 source_results 中最多 8 篇材料。",
        "output_schema": {
            "conclusion_summary": {"text": "围绕目标问题给出有数据支撑的综合判断（至少 3-5 句，含关键数字）", "confidence": "high|medium|low", "source_ids": ["S1"]},
            "question_coverage": [
                {
                    "question": "必答问题",
                    "answer": "当前可得结论，必须具体；证据不足时明确写证据不足",
                    "source_ids": ["S1"],
                    "key_details": ["关键事实、日期、版本、参数、风险或限制"],
                    "direct_excerpts": [{"source_id": "S1", "text": "来自清洗正文或摘要的短摘录"}],
                    "confidence": "high|medium|low",
                    "missing_or_uncertain": "仍缺什么或为什么不确定",
                    "requires_original_read": False,
                    "original_read_reason": "只有需要回读原文时填写原因"
                }
            ],
            "evidence_points": [
                {
                    "title": "证据点标题（概括核心内容）",
                    "text": "详细说明，包含具体数据、百分比、时间节点、来源机构等——目标是主 agent 看了不需要回原文。至少 3-5 句。",
                    "source_ids": ["S1"],
                    "confidence": "high|medium|low",
                    "key_data_points": ["具体数据点1：xx%", "具体数据点2：xx倍增长", "具体案例：xx企业"],
                    "related_questions": ["问题编号或问题短名"]
                }
            ],
            "evidence_draft": {
                "cross_source_findings": [
                    {"finding": "多个来源共同支撑的结论", "source_ids": ["S1", "S3"], "details": ["必须保留的细节"]}
                ],
                "conflicts_or_tensions": [
                    {"issue": "来源之间不一致或证据不足的问题", "source_ids": ["S2", "S5"], "interpretation": "应如何保守理解"}
                ],
                "directly_usable_facts": ["可直接给主 agent 使用的事实、数字、版本、日期、限制、案例；每条尽量带 source_id，如 S1: ..."]
            },
            "original_read_guidance": {
                "default_action": "use_deep_output|read_original",
                "requires_original_read": False,
                "source_ids_to_read": ["S1"],
                "reason": "为什么需要或不需要回读原文",
                "trigger_conditions": ["需要执行配置修改或代码修复", "需要精确命令/字段/API/版本承诺", "confidence=low", "来源冲突", "正式公开引用", "missing_information 非空且影响结论"]
            },
            "risks_and_limits": [{"risk": "风险或限制描述", "source_ids": ["S2"], "severity": "high|medium|low"}],
            "impact_on_goal": "这些证据对用户目标的具体影响（至少 3 句）",
            "recommended_agent_usage": "主 agent 应如何基于本文做下一步决策（如：直接引用数据、建议额外搜索方向等）",
            "missing_information": ["本文未覆盖但可能重要的信息点，建议搜索补充"],
            "source_table": [{"source_id": "S1", "title": "标题", "url": "URL", "use": "本文如何使用该来源", "risk": "该来源的已知风险"}],
        },
        "hard_requirements": [
            "只允许引用给定 source_results 中的 source_id。",
            "每个关键判断必须带 source_id。",
            "不允许编造来源或补充外部事实。",
            "必须尊重 source_results[].content_source：provider_inline/snippet_fallback 只能视为供应商返回摘要或搜索片段，不能表述为已读取完整原文。",
            "必须输出 question_coverage。若 brief.must_answer 非空，必须逐项覆盖其中每一项；若为空，至少覆盖点问题、风险/限制、缺口三项。",
            "question_coverage 中每一项都必须包含 source_ids；如果无证据，source_ids 为空并说明证据不足。",
            "尽量用 direct_excerpts 保留原文短摘录，但只能摘自给定 source_results 的摘要或正文片段。",
            "只有存在明确触发条件时才把 requires_original_read 设为 true；不要建议主 agent 习惯性回读原文，但必须保留可操作的 trigger_conditions。",
            "如果 missing_information 非空，original_read_guidance.trigger_conditions 必须说明哪些情况下需要回读哪些 source_id。",
            "如果问题涉及配置修改、代码修复、精确命令、字段、API 行为、版本承诺或公开引用，即使 default_action 是 use_deep_output，也必须给出回读触发条件和优先回读 source_ids。",
            "不要输出正式引用格式，只输出 S1/S2。",
            "evidence_points 必须合并重复信息；不要用多段文字反复陈述同一个 source 和同一组事实。",
            "directly_usable_facts 只能包含可直接复用的具体事实、数字、版本、日期、配置字段、错误文本或操作限制；没有这类事实时输出空数组，不要输出“不确定”。",
            "每条 evidence_points.text 至少 3 句。",
            "每条 evidence_points 必须包含 key_data_points 数组。",
            "输出严格 JSON，不要 Markdown。",
        ],
        "source_results": source_results,
    }


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _source_ids_text(value: Any) -> str:
    ids = [str(x) for x in _as_list(value) if str(x).strip()]
    return "[" + "][".join(ids) + "]" if ids else "[不确定]"


def _cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


def _meaningful_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    weak = {"不确定", "未知", "unknown", "n/a", "none", "无", "无。", "无明确", "无明确风险"}
    return "" if text.lower() in weak else text


def _dedupe_dict_items(items: list[dict[str, Any]], text_key: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        text = _meaningful_text(item.get(text_key))
        key = " ".join(text.split()).lower()[:220]
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(item)
    return out


def _normalize_evidence_draft(value: Any) -> dict[str, list[Any]]:
    draft: dict[str, list[Any]] = {
        "cross_source_findings": [],
        "conflicts_or_tensions": [],
        "directly_usable_facts": [],
    }
    if isinstance(value, dict):
        draft["cross_source_findings"] = [x for x in _as_list(value.get("cross_source_findings")) if isinstance(x, dict)]
        draft["conflicts_or_tensions"] = [x for x in _as_list(value.get("conflicts_or_tensions")) if isinstance(x, dict)]
        facts = [_meaningful_text(x) for x in _as_list(value.get("directly_usable_facts"))]
        draft["directly_usable_facts"] = [x for x in facts if x]
        return draft

    for item in _as_list(value):
        if isinstance(item, dict):
            if item.get("finding"):
                draft["cross_source_findings"].append(item)
                continue
            if item.get("issue"):
                draft["conflicts_or_tensions"].append(item)
                continue
            text = _meaningful_text(item.get("fact") or item.get("text") or item.get("summary") or item.get("title"))
            if text:
                draft["directly_usable_facts"].append(text)
            continue
        text = _meaningful_text(item)
        if text:
            draft["directly_usable_facts"].append(text)

    draft["directly_usable_facts"] = _unique_list(draft["directly_usable_facts"])
    return draft


def _render_article(parsed: dict[str, Any]) -> str:
    lines: list[str] = ["# 深度综合文章", ""]

    summary = parsed.get("conclusion_summary") if isinstance(parsed.get("conclusion_summary"), dict) else {}
    lines.extend(["## 1. 结论摘要", ""])
    lines.append(f"{summary.get('text') or '不确定'} {_source_ids_text(summary.get('source_ids'))}")
    if summary.get("confidence"):
        lines.append(f"\nConfidence: {summary.get('confidence')}")

    lines.extend(["", "## 2. 必答问题覆盖情况", ""])
    coverage = [x for x in _as_list(parsed.get("question_coverage")) if isinstance(x, dict)]
    if coverage:
        for i, item in enumerate(coverage, 1):
            question = item.get("question") or f"问题 {i}"
            lines.append(f"### 问题 {i}：{question}")
            lines.append("")
            lines.append(f"- 结论：{item.get('answer') or '证据不足'}")
            lines.append(f"- 证据：{_source_ids_text(item.get('source_ids'))}")
            if item.get("confidence"):
                lines.append(f"- 置信度：{item.get('confidence')}")
            details = item.get("key_details")
            if details:
                for detail in details if isinstance(details, list) else [details]:
                    lines.append(f"- 关键细节：{detail}")
            excerpts = [x for x in _as_list(item.get("direct_excerpts")) if isinstance(x, dict)]
            for excerpt in excerpts[:3]:
                lines.append(f"- 原文短摘：{excerpt.get('text') or ''} {_source_ids_text(excerpt.get('source_id'))}")
            if item.get("missing_or_uncertain"):
                lines.append(f"- 缺口：{item.get('missing_or_uncertain')}")
            if item.get("requires_original_read"):
                lines.append(f"- 需要回读原文：是。{item.get('original_read_reason') or ''}")
            else:
                lines.append("- 需要回读原文：否")
            lines.append("")
    else:
        lines.append("未输出问题覆盖情况。")

    lines.extend(["", "## 3. 主要证据", ""])
    evidence_items = [x for x in _as_list(parsed.get("evidence_points")) if isinstance(x, dict)]
    for i, item in enumerate(_dedupe_dict_items(evidence_items, "text"), 1):
        lines.append(f"### 证据 {i}：{item.get('title') or '未命名证据'}")
        lines.append("")
        lines.append(f"{item.get('text') or '不确定'} {_source_ids_text(item.get('source_ids'))}")
        if item.get("confidence"):
            lines.append(f"\nConfidence: {item.get('confidence')}")
        kd = item.get("key_data_points")
        if kd:
            for dp in kd if isinstance(kd, list) else [kd]:
                lines.append(f"- 数据要点：{dp}")
        lines.append("")

    lines.extend(["## 4. 证据底稿", ""])
    draft = _normalize_evidence_draft(parsed.get("evidence_draft"))
    cross = [x for x in _as_list(draft.get("cross_source_findings")) if isinstance(x, dict)]
    lines.append("### 跨来源共同结论")
    if cross:
        for item in cross:
            lines.append(f"- {item.get('finding') or '不确定'} {_source_ids_text(item.get('source_ids'))}")
            for detail in _as_list(item.get("details")):
                lines.append(f"  - {detail}")
    else:
        lines.append("- 未抽取到明确的跨来源共同结论。")
    lines.append("")
    tensions = [x for x in _as_list(draft.get("conflicts_or_tensions")) if isinstance(x, dict)]
    lines.append("### 冲突、矛盾和不确定性")
    if tensions:
        for item in tensions:
            lines.append(f"- {item.get('issue') or '不确定'} {_source_ids_text(item.get('source_ids'))}")
            if item.get("interpretation"):
                lines.append(f"  - 保守理解：{item.get('interpretation')}")
    else:
        lines.append("- 未发现明确冲突。")
    lines.append("")
    facts = [_meaningful_text(x) for x in _as_list(draft.get("directly_usable_facts"))]
    facts = [x for x in facts if x]
    lines.append("### 可直接使用的事实")
    if facts:
        for fact in facts:
            lines.append(f"- {fact}")
    else:
        lines.append("- 未抽取到足够具体的可直接使用事实；需要回读关键原文或补充搜索确认。")

    lines.extend(["", "## 5. 原文回读建议", ""])
    guidance = parsed.get("original_read_guidance") if isinstance(parsed.get("original_read_guidance"), dict) else {}
    if guidance:
        lines.append(f"- 默认动作：{guidance.get('default_action') or 'use_deep_output'}")
        needs_read = bool(guidance.get("requires_original_read"))
        lines.append(f"- 是否需要回读原文：{'是' if needs_read else '否'}")
        if guidance.get("source_ids_to_read"):
            label = "建议回读来源" if needs_read else "触发时优先回读来源"
            lines.append(f"- {label}：{_source_ids_text(guidance.get('source_ids_to_read'))}")
        if guidance.get("reason"):
            lines.append(f"- 原因：{guidance.get('reason')}")
        triggers = _as_list(guidance.get("trigger_conditions"))
        if triggers:
            lines.append("- 触发条件：")
            for trigger in triggers:
                lines.append(f"  - {trigger}")
    else:
        lines.append("- 默认动作：use_deep_output")
        lines.append("- 是否需要回读原文：否")

    lines.extend(["", "## 6. 风险与限制", ""])
    risks = [x for x in _as_list(parsed.get("risks_and_limits")) if isinstance(x, dict)]
    if risks:
        for item in risks:
            severity = item.get("severity")
            suffix = f" Severity: {severity}." if severity else ""
            lines.append(f"- {item.get('risk') or '不确定'} {_source_ids_text(item.get('source_ids'))}.{suffix}")
    else:
        lines.append("- 不确定 [不确定]")

    lines.extend(["", "## 7. 对用户目标的影响", ""])
    lines.append(str(parsed.get("impact_on_goal") or "不确定"))

    lines.extend(["", "## 8. 建议主 agent 如何使用", ""])
    lines.append(str(parsed.get("recommended_agent_usage") or "证据不足，需继续读原文或补充搜索。"))

    missing = _as_list(parsed.get("missing_information"))
    if missing:
        lines.extend(["", "Missing information:"])
        for item in missing:
            lines.append(f"- {item}")

    lines.extend(["", "## 9. 来源表", ""])
    lines.append("| ID | 标题 | URL | 用途 | 风险 |")
    lines.append("|---|---|---|---|---|")
    for item in _as_list(parsed.get("source_table")):
        if not isinstance(item, dict):
            continue
        lines.append(
            f"| {_cell(item.get('source_id'))} | {_cell(item.get('title'))} | {_cell(item.get('url'))} | {_cell(item.get('use'))} | {_cell(item.get('risk'))} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def _unique_list(values: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _primary_source_ids(parsed: dict[str, Any], limit: int = 3) -> list[str]:
    ids: list[Any] = []
    summary = parsed.get("conclusion_summary") if isinstance(parsed.get("conclusion_summary"), dict) else {}
    ids.extend(_as_list(summary.get("source_ids")))
    for item in _as_list(parsed.get("question_coverage")):
        if isinstance(item, dict):
            ids.extend(_as_list(item.get("source_ids")))
    return _unique_list(ids)[:limit]


def _postprocess_article(parsed: dict[str, Any]) -> dict[str, Any]:
    parsed["evidence_draft"] = _normalize_evidence_draft(parsed.get("evidence_draft"))

    guidance = parsed.get("original_read_guidance") if isinstance(parsed.get("original_read_guidance"), dict) else {}
    triggers = _as_list(guidance.get("trigger_conditions"))
    source_ids = _as_list(guidance.get("source_ids_to_read"))
    missing = [_meaningful_text(x) for x in _as_list(parsed.get("missing_information"))]
    missing = [x for x in missing if x]

    coverage = [x for x in _as_list(parsed.get("question_coverage")) if isinstance(x, dict)]
    combined_text = "\n".join(
        missing
        + [str(x.get("question") or "") for x in coverage]
        + [str(x.get("missing_or_uncertain") or "") for x in coverage]
    )
    precision_terms = (
        "配置",
        "修配置",
        "配置修复",
        "代码",
        "字段",
        "命令",
        "API",
        "版本",
        "日志",
        "错误",
        "握手",
        "机制",
        "校验",
        "精确",
        "准确引用",
        "公开引用",
        "正式引用",
    )
    item_read_terms = (
        "配置",
        "修配置",
        "配置修复",
        "代码",
        "字段",
        "命令",
        "API",
        "日志",
        "405",
        "触发路径",
        "报错文本",
        "错误文本",
        "握手",
        "校验",
        "精确",
        "准确引用",
        "公开引用",
        "正式引用",
    )
    precision_sensitive = any(term in combined_text for term in precision_terms)

    if precision_sensitive:
        precision_trigger = "涉及配置修改、代码修复、公开引用或精确字段/命令/API/日志的问题项需要回读关键原文"
        for item in coverage:
            item_text = "\n".join(
                [
                    str(item.get("question") or ""),
                    str(item.get("missing_or_uncertain") or ""),
                ]
            )
            if any(term in item_text for term in item_read_terms):
                item["requires_original_read"] = True
                item.setdefault("original_read_reason", precision_trigger)
                source_ids.extend(_as_list(item.get("source_ids")))
        parsed["question_coverage"] = coverage

    low_confidence = any(str(x.get("confidence", "")).lower() == "low" for x in coverage)
    explicit_read = any(bool(x.get("requires_original_read")) for x in coverage)

    if missing:
        triggers.append("missing_information 非空且会影响回答完整性时，回读关键来源或补充搜索")
    if precision_sensitive:
        triggers.append("需要执行配置修改、代码修复、公开引用或确认精确字段/命令/API/版本/日志/机制时，回读关键原文")
    if low_confidence or explicit_read:
        guidance["requires_original_read"] = True
        guidance["default_action"] = "read_original"
        triggers.append("存在低置信度或单项问题明确要求回读原文")
        guidance["reason"] = "存在低置信度或精确配置/引用/字段/命令/API/日志类问题；执行前应回读关键原文。"
    else:
        guidance.setdefault("requires_original_read", False)
        guidance.setdefault("default_action", "use_deep_output")

    if triggers and not source_ids:
        source_ids = _primary_source_ids(parsed)
    guidance["trigger_conditions"] = _unique_list(triggers)
    guidance["source_ids_to_read"] = _unique_list(source_ids)
    if guidance["trigger_conditions"] and not guidance.get("reason"):
        guidance["reason"] = "deep 输出可用于第一轮回答；触发条件满足时再回读关键原文。"
    parsed["original_read_guidance"] = guidance
    return parsed


def generate_deep_article(
    query: str,
    ranking_query: str,
    brief,
    candidates: list[Candidate],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    if not cfg.get("enabled", True):
        raise DeepAnalyzerError("deep analyzer is disabled")
    parsed, raw = _chat_json(
        _article_payload(query, ranking_query, brief, candidates, cfg),
        cfg,
        max_tokens_key="article_max_tokens",
        timeout_key="article_timeout_sec",
        max_input_chars_key="article_max_input_chars",
    )
    if not isinstance(parsed, dict):
        raise DeepAnalyzerError("deep article response is not a JSON object")
    parsed = _postprocess_article(parsed)
    return {
        "json": parsed,
        "markdown": _render_article(parsed),
        "usage": raw.get("usage"),
        "input_audit": raw.get("_input_audit"),
        "provider": cfg.get("provider", "openai_compatible_http"),
        "model": cfg.get("model"),
    }


def analyze_deep_results(query: str, candidates: list[Candidate], cfg: dict[str, Any]) -> dict[str, Any]:
    return analyze_source_quality(query, query, None, candidates, cfg)
