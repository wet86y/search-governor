#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
cd "$ROOT"

tracked="$(git ls-files)"
for forbidden in 'config/.env' 'config/provider_presets.local.json' 'config/scoring.local.json' 'config/reranker.local.json' 'config/deep_analyzer.local.json' 'config/fetcher.local.json' 'connectors'; do
  if grep -Fqx "$forbidden" <<<"$tracked"; then
    echo "Forbidden local path is tracked: $forbidden" >&2
    exit 1
  fi
done
for forbidden_prefix in 'providers.local/' 'integrations.local/' 'build.local/' 'legacy.local/' 'data/' 'connectors/'; do
  if grep -Fq "$forbidden_prefix" <<<"$tracked"; then
    echo "Forbidden local path prefix is tracked: $forbidden_prefix" >&2
    exit 1
  fi
done

if git grep -n -I -E '(gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|Bearer[[:space:]]+[A-Za-z0-9._~+/-]{20,})' -- . >/tmp/search-governor-secret-scan.txt; then
  echo "Potential credential material found in tracked files:" >&2
  sed -n '1,40p' /tmp/search-governor-secret-scan.txt >&2
  exit 1
fi

echo "Public Git tree check passed"
