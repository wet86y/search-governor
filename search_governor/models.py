from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Candidate:
    id: str
    title: str
    url: str
    snippet: str
    provider: str
    rank: int
    domain: str = ""
    normalized_url: str = ""
    published_at: str | None = None
    language: str | None = None
    raw_score: float | None = None
    content_kind: str = "search_snippet"
    source_hits: list[dict[str, Any]] = field(default_factory=list)
    rule_score: float = 0.0
    rerank_score: float | None = None
    final_score: float = 0.0
    rank_reason: list[str] = field(default_factory=list)
    fetch_status: str = "not_fetched"
    fetched_title: str | None = None
    fetched_content: str | None = None
    fetch_error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
