from __future__ import annotations
import re
from datetime import date, datetime
from .models import Candidate


def tokenize_query(q: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9_./:+#-]{2,}|[\u4e00-\u9fff]{2,}", q or "")
    return [w.lower() for w in words if len(w.strip()) >= 2]


def parse_date(s: str | None):
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(s[:19], fmt).date()
        except Exception:
            pass
    return None


def score_candidates(candidates: list[Candidate], query: str, scoring_cfg: dict, include_domains: list[str] | None = None, exclude_domains: list[str] | None = None, prefer_domains: list[str] | None = None) -> None:
    terms = tokenize_query(query)
    provider_weight = scoring_cfg.get("provider_weight", {})
    domain_bonus = scoring_cfg.get("domain_bonus", {})
    penalties = scoring_cfg.get("penalties", {})
    include_domains = include_domains or []
    exclude_domains = exclude_domains or []
    prefer_domains = prefer_domains or []
    for c in candidates:
        text_title = (c.title or "").lower()
        text_snip = (c.snippet or "").lower()
        score = 0.0
        reasons: list[str] = []
        for t in terms:
            if t in text_title:
                score += 0.06
                reasons.append(f"title_match:{t}")
            if t in text_snip:
                score += 0.035
                reasons.append(f"snippet_match:{t}")
        pw = float(provider_weight.get(c.provider, 0.6))
        score += pw * 0.15
        reasons.append(f"provider_weight:{c.provider}={pw}")
        # rank bonus: higher for smaller original rank
        if c.rank > 0:
            score += max(0.0, (20 - min(c.rank, 20)) / 20) * 0.08
        d = c.domain or ""
        for dom, bonus in domain_bonus.items():
            if dom in d or (dom.endswith(".") and dom in (c.url or "")):
                score += float(bonus)
                reasons.append(f"domain_bonus:{dom}")
        for dom in prefer_domains:
            if dom in d or dom in (c.url or ""):
                score += 0.10
                reasons.append(f"prefer_domain:{dom}")
        if include_domains and not any(dom in d or dom in (c.url or "") for dom in include_domains):
            score -= 0.20
            reasons.append("not_in_include_domains")
        if exclude_domains and any(dom in d or dom in (c.url or "") for dom in exclude_domains):
            score += float(penalties.get("excluded_domain", -1.0))
            reasons.append("excluded_domain")
        if not c.snippet:
            score += float(penalties.get("missing_snippet", -0.10))
            reasons.append("missing_snippet")
        elif len(c.snippet) < 50:
            score += float(penalties.get("very_short_snippet", -0.05))
            reasons.append("very_short_snippet")
        if parse_date(c.published_at):
            score += 0.03
            reasons.append("has_date")
        c.rule_score = max(0.0, min(1.0, score))
        c.final_score = c.rule_score
        c.rank_reason.extend(reasons[:12])
