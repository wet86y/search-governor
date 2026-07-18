#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
export SG_HOME="$ROOT"
export SEARCH_GOVERNOR_DISABLE_LOCAL=1
"$ROOT/bin/sg" --version
"$ROOT/bin/sg" providers
"$ROOT/bin/sg" search "Search Governor contract smoke" \
  --providers mock \
  --allow-disabled-sources \
  --allow-rule-fallback \
  --return-count 2 \
  --no-fetch \
  --format json >/tmp/search-governor-smoke.json
python3 -c 'import json; p=json.load(open("/tmp/search-governor-smoke.json", encoding="utf-8")); assert p["pipeline"]["returned"] >= 1'
echo "smoke ok"
