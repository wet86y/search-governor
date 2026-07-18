#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
SOURCE_JSON="${1:-$ROOT/providers/example/source.json}"
python3 "$ROOT/scripts/validate_source.py" "$SOURCE_JSON"
