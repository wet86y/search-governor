#!/usr/bin/env python3
"""Mock source: reads SearchRequest JSON from stdin, writes Candidate JSONL."""
from __future__ import annotations
import json
import sys

req = json.load(sys.stdin)
query = req.get("query", "")
count = int(req.get("per_provider_count", 8))
items = [
    {
        "title": f"Mock official documentation result for {query}",
        "url": "https://example.com/docs/search-governor",
        "snippet": f"Official-style documentation page that directly discusses {query}, search aggregation, reranking, and fetch behavior.",
        "provider": "mock",
        "rank": 1,
        "published_at": "2026-06-01",
        "language": "en",
        "content_kind": "search_snippet"
    },
    {
        "title": f"Mock GitHub issue related to {query}",
        "url": "https://github.com/example/search-governor/issues/1?utm_source=mock",
        "snippet": f"Issue thread mentioning {query}, adapter failures, reranker fallback, and provider normalization.",
        "provider": "mock",
        "rank": 2,
        "published_at": "2026-06-02",
        "language": "en",
        "content_kind": "search_snippet"
    },
    {
        "title": f"Mock blog duplicate about {query}",
        "url": "https://example.com/docs/search-governor/?utm_campaign=dup#section",
        "snippet": f"Duplicate URL variant about {query}. This should be deduped by normalized URL logic.",
        "provider": "mock",
        "rank": 3,
        "published_at": "2026-06-03",
        "language": "en",
        "content_kind": "search_snippet"
    }
]
print('SG_REPORT_JSON={"applied_params":{"per_provider_count":{"applied":true,"method":"local_mock","value":%d}}}' % count, file=sys.stderr)
for item in items[:count]:
    print(json.dumps(item, ensure_ascii=False))
