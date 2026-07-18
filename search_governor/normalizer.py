from __future__ import annotations
import hashlib
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from .models import Candidate

TRACKING_PREFIXES = ("utm_",)
TRACKING_KEYS = {"fbclid", "gclid", "yclid", "mc_cid", "mc_eid", "igshid", "spm"}


def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def normalize_url(url: str) -> str:
    p = urlparse((url or "").strip())
    scheme = p.scheme.lower() or "https"
    netloc = p.netloc.lower().removeprefix("www.")
    path = p.path or "/"
    if path != "/":
        path = path.rstrip("/")
    kept = []
    for k, v in parse_qsl(p.query, keep_blank_values=True):
        lk = k.lower()
        if lk in TRACKING_KEYS or any(lk.startswith(prefix) for prefix in TRACKING_PREFIXES):
            continue
        kept.append((k, v))
    query = urlencode(sorted(kept), doseq=True)
    return urlunparse((scheme, netloc, path, "", query, ""))


def make_id(provider: str, url: str, rank: int) -> str:
    h = hashlib.sha1(f"{provider}|{url}|{rank}".encode("utf-8")).hexdigest()[:10]
    return f"c_{h}"


def raw_to_candidate(raw: dict, default_provider: str) -> Candidate | None:
    title = str(raw.get("title") or "").strip()
    url = str(raw.get("url") or "").strip()
    snippet = str(raw.get("snippet") or raw.get("summary") or raw.get("description") or "").strip()
    provider = str(raw.get("provider") or default_provider).strip()
    if not title or not url:
        return None
    try:
        rank = int(raw.get("rank") or 999)
    except Exception:
        rank = 999
    norm = normalize_url(url)
    passthrough_extra = raw.get("extra") if isinstance(raw.get("extra"), dict) else {}
    extra = {k: v for k, v in raw.items() if k not in {"title", "url", "snippet", "summary", "description", "provider", "rank", "domain", "published_at", "language", "raw_score", "content_kind", "extra"}}
    extra.update(passthrough_extra)
    cand = Candidate(
        id=make_id(provider, norm, rank),
        title=title,
        url=url,
        snippet=snippet,
        provider=provider,
        rank=rank,
        domain=raw.get("domain") or domain_of(url),
        normalized_url=norm,
        published_at=raw.get("published_at"),
        language=raw.get("language"),
        raw_score=raw.get("raw_score"),
        content_kind=raw.get("content_kind") or "search_snippet",
        extra=extra,
    )
    cand.source_hits.append({"provider": provider, "rank": rank, "url": url})
    return cand
