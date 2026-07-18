from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import Candidate
from .paths import data_dir


def cache_dir() -> Path:
    path = data_dir() / "fetch_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_key_for_url(url: str) -> str:
    return hashlib.sha1((url or "").encode("utf-8")).hexdigest()


def cache_key_for_candidate(c: Candidate) -> str:
    return cache_key_for_url(c.normalized_url or c.url)


def cache_path(key: str) -> Path:
    return cache_dir() / f"{key}.json"


def load_cache(key: str) -> dict[str, Any] | None:
    path = cache_path(key)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def save_candidate_cache(c: Candidate) -> dict[str, Any]:
    key = cache_key_for_candidate(c)
    payload = {
        "cache_key": key,
        "id": c.id,
        "url": c.url,
        "normalized_url": c.normalized_url,
        "provider": c.provider,
        "title": c.title,
        "snippet": c.snippet,
        "published_at": c.published_at,
        "fetch_status": c.fetch_status,
        "fetch_error": c.fetch_error,
        "fetched_title": c.fetched_title,
        "fetched_content": c.fetched_content,
        "extra": c.extra,
    }
    cache_path(key).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
