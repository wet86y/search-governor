#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
cd "$ROOT"
python3 scripts/deploy_local_release.py --source-root "$ROOT"
echo "Installed immutable release and stable runtime. Test with: sg --version && sg health"
