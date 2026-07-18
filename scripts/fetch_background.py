#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


SG_HOME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SG_HOME))
os.environ.setdefault("SG_HOME", str(SG_HOME))

from search_governor.config import load_all_configs
from search_governor.content_cleaner import clean_top_content
from search_governor.fetch_cache import cache_key_for_candidate, load_cache, save_candidate_cache
from search_governor.fetcher import fetch_top
from search_governor.models import Candidate
from search_governor.reporter import write_jsonl
from search_governor.sources import load_sources


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def candidate_from_dict(row: dict) -> Candidate:
    fields = Candidate.__dataclass_fields__
    return Candidate(**{k: row[k] for k in fields if k in row})


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch reranked Search Governor results into cache.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--return-count", type=int, required=True)
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    log_path = run_dir / "deferred_fetch.log"
    try:
        cfg = load_all_configs()
        fetcher_cfg = dict(cfg["fetcher"])
        sources = load_sources()
        fetcher_cfg["provider_capabilities"] = {
            sid: (spec.config.get("capabilities", {}) if isinstance(spec.config.get("capabilities", {}), dict) else {})
            for sid, spec in sources.items()
        }
        fetcher_cfg["provider_source_paths"] = {sid: str(spec.path) for sid, spec in sources.items()}
        rows = read_jsonl(run_dir / "reranked.jsonl")[: args.return_count]
        results = []
        for row in rows:
            c = candidate_from_dict(row)
            key = cache_key_for_candidate(c)
            cached = load_cache(key)
            if cached and cached.get("fetch_status") == "ok":
                results.append(cached)
                continue
            fetched = fetch_top([c], fetcher_cfg, enabled=True)
            cleaned, _cleanup = clean_top_content(fetched, cfg["content_cleaner"])
            payload = save_candidate_cache(cleaned[0])
            results.append(payload)
        write_jsonl(run_dir / "deferred_fetch_status.jsonl", results)
        log_path.write_text(json.dumps({"ok": True, "count": len(results)}, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    except Exception as exc:
        log_path.write_text(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), encoding="utf-8")
        return 20


if __name__ == "__main__":
    raise SystemExit(main())
