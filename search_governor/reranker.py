from __future__ import annotations
import json
import os
import urllib.request
import urllib.error
from typing import Any
from .models import Candidate


def candidate_doc(c: Candidate, max_chars: int = 1200) -> str:
    text = (
        f"标题：{c.title}\n"
        f"URL：{c.url}\n"
        f"来源：{c.provider} / {c.domain}\n"
        f"时间：{c.published_at or ''}\n"
        f"摘要：{c.snippet or ''}\n"
        "判断提示：请判断该材料是否有助于回答排序任务卡中的必答问题。\n"
    )
    return text[:max_chars]


def content_source_label(c: Candidate) -> str:
    granularity = c.extra.get("content_granularity") if isinstance(c.extra, dict) else None
    if c.fetch_status == "inline_content":
        return f"供应商返回{granularity or '摘要/片段'}，不是完整原文抓取"
    if c.fetch_status == "snippet_fallback":
        return "搜索摘要兜底，不是完整原文抓取"
    if c.fetch_status == "ok":
        return "外部或原生正文抓取结果，可能受字符预算截断"
    return c.fetch_status or "未知"


def body_candidate_doc(c: Candidate, max_chars: int = 4000) -> str:
    text = (
        f"标题：{c.title}\n"
        f"URL：{c.url}\n"
        f"来源：{c.provider} / {c.domain}\n"
        f"时间：{c.published_at or ''}\n"
        f"摘要：{c.snippet or ''}\n"
        f"内容来源：{content_source_label(c)}\n"
        f"内容片段：\n{c.fetched_content or ''}\n"
        "判断提示：请重点判断内容片段是否能支撑排序任务卡中的必答问题，是否包含事实、版本、日期、风险、限制或操作信息；如果内容来源不是完整原文抓取，不要把它当作全文证据。\n"
    )
    return text[:max_chars]


class RerankError(RuntimeError):
    pass


def _audit_and_trim_documents(query: str, documents: list[str], instruction: str, cfg: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    max_chars = int(cfg.get("max_input_chars", 28000))
    min_doc_chars = int(cfg.get("min_doc_chars_after_audit", 300))
    truncation_marker = "\n[TRUNCATED_BY_INPUT_AUDIT]"
    original_doc_chars = [len(doc or "") for doc in documents]
    fixed_chars = len(query or "") + len(instruction or "") + 512
    original_total = fixed_chars + sum(original_doc_chars)
    audit = {
        "enabled": True,
        "model_context_hint": cfg.get("model_context_hint", "32k"),
        "max_input_chars": max_chars,
        "original_chars": original_total,
        "truncated": False,
        "truncated_documents": 0,
    }
    if original_total <= max_chars or not documents:
        return documents, audit

    available = max(0, max_chars - fixed_chars)
    if available and min_doc_chars * len(documents) <= available:
        per_doc = max(min_doc_chars, available // len(documents))
    else:
        per_doc = available // len(documents) if documents else 0
    trimmed: list[str] = []
    truncated_documents = 0
    for doc in documents:
        text = doc or ""
        if per_doc and len(text) > per_doc:
            slice_chars = max(0, per_doc - len(truncation_marker))
            trimmed.append(text[:slice_chars].rstrip() + truncation_marker)
            truncated_documents += 1
        else:
            trimmed.append(text[:per_doc] if per_doc else "")
            if not per_doc and text:
                truncated_documents += 1
    audit.update(
        {
            "truncated": True,
            "truncated_documents": truncated_documents,
            "final_chars": fixed_chars + sum(len(doc) for doc in trimmed),
            "error": f"rerank input exceeded configured audit budget: {original_total}>{max_chars}; documents were truncated before model call",
        }
    )
    return trimmed, audit


def http_rerank_documents(query: str, documents: list[str], cfg: dict[str, Any], top_n: int) -> dict[str, Any]:
    key_env = cfg.get("api_key_env", "SEARCH_GOVERNOR_RERANK_API_KEY")
    api_key = os.environ.get(key_env, "").strip()
    if not api_key:
        raise RerankError(f"Missing API key env: {key_env}")
    api_base = str(cfg.get("api_base") or "").strip()
    model = str(cfg.get("model") or "").strip()
    if not api_base or not model:
        raise RerankError("Reranker api_base and model must be configured")
    instruction = cfg.get("instruction") or ""
    documents, input_audit = _audit_and_trim_documents(query, documents, instruction, cfg)
    payload = {
        "model": model,
        "query": query,
        "documents": documents,
        "top_n": max(1, min(top_n, len(documents))),
        "return_documents": bool(cfg.get("return_documents", False)),
    }
    if instruction:
        payload["instruction"] = instruction
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        api_base,
        data=data,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    timeout = int(cfg.get("timeout_sec", 45))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            response = json.loads(body)
            if isinstance(response, dict):
                response["_input_audit"] = input_audit
            return response
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace")[:1000]
        raise RerankError(f"Rerank HTTP {e.code}: {msg}") from e
    except Exception as e:
        raise RerankError(f"Rerank request failed: {e}") from e


def http_rerank(query: str, candidates: list[Candidate], cfg: dict[str, Any], top_n: int) -> dict[str, Any]:
    docs = [candidate_doc(c, int(cfg.get("max_doc_chars", 1200))) for c in candidates]
    return http_rerank_documents(query, docs, cfg, top_n)


def apply_rerank(query: str, candidates: list[Candidate], cfg: dict[str, Any], return_count: int, allow_rule_fallback: bool = False) -> tuple[list[Candidate], dict[str, Any]]:
    if not candidates:
        return [], {"enabled": bool(cfg.get("enabled", True)), "ok": False, "error": "no candidates"}
    max_candidates = int(cfg.get("max_candidates", 40))
    candidates = sorted(candidates, key=lambda c: c.rule_score, reverse=True)[:max_candidates]
    if not cfg.get("enabled", True):
        return sorted(candidates, key=lambda c: c.rule_score, reverse=True), {"enabled": False, "ok": True, "mode": "rule_only"}
    try:
        response = http_rerank(query, candidates, cfg, top_n=len(candidates))
        results = response.get("results", [])
        by_index = {int(r["index"]): float(r.get("relevance_score", 0.0)) for r in results if "index" in r}
        max_score = max(by_index.values()) if by_index else 1.0
        min_score = min(by_index.values()) if by_index else 0.0
        span = max(max_score - min_score, 1e-9)
        weights = cfg.get("score_weights", {"rule": 0.3, "rerank": 0.7})
        rw = float(weights.get("rule", 0.3))
        mw = float(weights.get("rerank", 0.7))
        for idx, c in enumerate(candidates):
            if idx in by_index:
                norm = (by_index[idx] - min_score) / span if span > 1e-9 else by_index[idx]
                c.rerank_score = by_index[idx]
                c.final_score = rw * c.rule_score + mw * norm
                c.rank_reason.append(f"rerank_score:{by_index[idx]:.6f}")
            else:
                c.rerank_score = None
                c.final_score = rw * c.rule_score
                c.rank_reason.append("rerank_missing_index")
        ranked = sorted(candidates, key=lambda c: c.final_score, reverse=True)
        return ranked, {"enabled": True, "ok": True, "provider": cfg.get("provider", "compatible_http"), "model": cfg.get("model"), "response_id": response.get("id"), "meta": response.get("meta") or response.get("tokens"), "input_audit": response.get("_input_audit")}
    except Exception as e:
        if allow_rule_fallback:
            ranked = sorted(candidates, key=lambda c: c.rule_score, reverse=True)
            for c in ranked:
                c.rank_reason.append("rerank_failed_rule_fallback")
            return ranked, {"enabled": True, "ok": False, "fallback": "rule", "error": str(e)}
        raise


def apply_body_rerank(query: str, candidates: list[Candidate], cfg: dict[str, Any], allow_rule_fallback: bool = False) -> tuple[list[Candidate], dict[str, Any]]:
    if not candidates:
        return [], {"enabled": bool(cfg.get("enabled", True)), "ok": False, "error": "no candidates"}
    if not cfg.get("enabled", True):
        return sorted(candidates, key=lambda c: c.final_score, reverse=True), {"enabled": False, "ok": True, "mode": "summary_only"}
    try:
        max_chars = int(cfg.get("body_max_doc_chars", cfg.get("max_doc_chars", 4000)))
        docs = [body_candidate_doc(c, max_chars) for c in candidates]
        response = http_rerank_documents(query, docs, cfg, top_n=len(candidates))
        results = response.get("results", [])
        by_index = {int(r["index"]): float(r.get("relevance_score", 0.0)) for r in results if "index" in r}
        max_score = max(by_index.values()) if by_index else 1.0
        min_score = min(by_index.values()) if by_index else 0.0
        span = max(max_score - min_score, 1e-9)
        weights = cfg.get("body_score_weights", {"summary": 0.25, "body": 0.75})
        sw = float(weights.get("summary", 0.25))
        bw = float(weights.get("body", 0.75))
        for idx, c in enumerate(candidates):
            c.extra["summary_final_score"] = c.final_score
            if c.rerank_score is not None:
                c.extra["summary_rerank_score"] = c.rerank_score
            if idx in by_index:
                norm = (by_index[idx] - min_score) / span if span > 1e-9 else by_index[idx]
                c.extra["body_rerank_score"] = by_index[idx]
                c.final_score = sw * c.final_score + bw * norm
                c.rank_reason.append(f"body_rerank_score:{by_index[idx]:.6f}")
            else:
                c.final_score = sw * c.final_score
                c.rank_reason.append("body_rerank_missing_index")
        ranked = sorted(candidates, key=lambda c: c.final_score, reverse=True)
        return ranked, {"enabled": True, "ok": True, "provider": cfg.get("provider", "compatible_http"), "model": cfg.get("model"), "response_id": response.get("id"), "meta": response.get("meta") or response.get("tokens"), "input_audit": response.get("_input_audit")}
    except Exception as e:
        if allow_rule_fallback:
            ranked = sorted(candidates, key=lambda c: c.final_score, reverse=True)
            for c in ranked:
                c.rank_reason.append("body_rerank_failed_summary_fallback")
            return ranked, {"enabled": True, "ok": False, "fallback": "summary", "error": str(e)}
        raise
