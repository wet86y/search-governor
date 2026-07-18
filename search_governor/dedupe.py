from __future__ import annotations
import re
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any
from .models import Candidate


def merge_candidates(a: Candidate, b: Candidate) -> Candidate:
    # Keep the more informative visible fields, merge source hits.
    if len(b.snippet or "") > len(a.snippet or ""):
        a.snippet = b.snippet
    if b.published_at and not a.published_at:
        a.published_at = b.published_at
    a.source_hits.extend(b.source_hits or [{"provider": b.provider, "rank": b.rank, "url": b.url}])
    a.rank_reason.append(f"deduped_with:{b.provider}#{b.rank}")
    return a


def title_sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def parse_published_date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit() and len(text) == 10:
        try:
            return datetime.fromtimestamp(int(text)).date()
        except Exception:
            return None
    for pattern in (
        r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})",
        r"(\d{4})(\d{2})(\d{2})",
    ):
        match = re.search(pattern, text)
        if match:
            try:
                y, m, d = (int(x) for x in match.groups())
                return date(y, m, d)
            except Exception:
                return None
    try:
        return datetime.fromisoformat(text.removesuffix("Z")).date()
    except Exception:
        return None


def parse_bound(value: str | None) -> date | None:
    parsed = parse_published_date(value)
    if value and not parsed:
        raise ValueError(f"invalid date bound: {value}")
    return parsed


def freshness_after(value: str | None) -> date | None:
    if not value:
        return None
    today = date.today()
    mapping = {
        "day": 1,
        "oneDay": 1,
        "week": 7,
        "oneWeek": 7,
        "7d": 7,
        "month": 30,
        "oneMonth": 30,
        "30d": 30,
        "year": 365,
        "oneYear": 365,
        "365d": 365,
    }
    if value in mapping:
        return today - timedelta(days=mapping[value])
    match = re.fullmatch(r"(\d+)d", value)
    if match:
        return today - timedelta(days=int(match.group(1)))
    return None


def should_keep_unknown_date_for_provider(provider: str, params: dict[str, Any]) -> bool:
    caps_by_provider = params.get("provider_capabilities") if isinstance(params.get("provider_capabilities"), dict) else {}
    caps = caps_by_provider.get(provider, {}) if isinstance(caps_by_provider.get(provider, {}), dict) else {}
    return bool(caps.get("retain_unknown_dates")) or caps.get("result_kind") == "academic"


def normalize_and_filter_dates(candidates: list[Candidate], params: dict[str, Any]) -> tuple[list[Candidate], dict[str, Any]]:
    try:
        date_after = parse_bound(params.get("date_after"))
        date_before = parse_bound(params.get("date_before"))
    except ValueError as exc:
        return [], {"enabled": True, "error": str(exc), "input": len(candidates), "after": 0}
    freshness_bound = freshness_after(params.get("freshness"))
    if freshness_bound and (not date_after or freshness_bound > date_after):
        date_after = freshness_bound

    enabled = bool(date_after or date_before)
    normalized = 0
    dropped_before = 0
    dropped_after = 0
    dropped_unknown = 0
    kept_unknown_special = 0
    kept: list[Candidate] = []
    for c in candidates:
        parsed = parse_published_date(c.published_at)
        if parsed:
            normalized += 1
            c.published_at = parsed.isoformat()
        if not enabled:
            kept.append(c)
            continue
        if not parsed:
            if should_keep_unknown_date_for_provider(c.provider, params):
                c.extra["time_filter"] = "kept_unknown_date_for_special_provider"
                kept_unknown_special += 1
                kept.append(c)
                continue
            dropped_unknown += 1
            continue
        if date_after and parsed < date_after:
            dropped_before += 1
            continue
        if date_before and parsed > date_before:
            dropped_after += 1
            continue
        kept.append(c)

    return kept, {
        "enabled": enabled,
        "input": len(candidates),
        "after": len(kept),
        "date_after": date_after.isoformat() if date_after else None,
        "date_before": date_before.isoformat() if date_before else None,
        "normalized": normalized,
        "dropped_before": dropped_before,
        "dropped_after": dropped_after,
        "dropped_unknown": dropped_unknown,
        "kept_unknown_special": kept_unknown_special,
        "unknown_policy": "keep_unknown_for_declared_local_or_academic_sources",
    }


def dedupe(candidates: list[Candidate]) -> tuple[list[Candidate], dict]:
    by_url: dict[str, Candidate] = {}
    exact_removed = 0
    for c in candidates:
        key = c.normalized_url or c.url
        if key in by_url:
            by_url[key] = merge_candidates(by_url[key], c)
            exact_removed += 1
        else:
            by_url[key] = c
    items = list(by_url.values())

    # Weak dedupe: same domain + highly similar title.
    kept: list[Candidate] = []
    weak_removed = 0
    for c in items:
        merged = False
        for k in kept:
            if c.domain == k.domain and title_sim(c.title, k.title) >= 0.92:
                merge_candidates(k, c)
                weak_removed += 1
                merged = True
                break
        if not merged:
            kept.append(c)
    return kept, {"input": len(candidates), "after": len(kept), "exact_removed": exact_removed, "weak_removed": weak_removed}
