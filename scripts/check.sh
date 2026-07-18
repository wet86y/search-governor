#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
cd "$ROOT"
export SEARCH_GOVERNOR_DISABLE_LOCAL=1

python3 -m compileall -q search_governor providers scripts tests
python3 -m unittest discover -s tests -p 'test_*.py' -v
node --check integrations/openclaw/index.js
node tests/openclaw_plugin_test.js
./scripts/smoke_test.sh
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  ./scripts/check-public-tree.sh
fi
