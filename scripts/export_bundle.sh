#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
VERSION="${1:-0.1.0}"
OUT="${2:-$ROOT/dist/search-governor-v${VERSION}.zip}"

git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null
mkdir -p "$(dirname "$OUT")"
"$ROOT/scripts/check-public-tree.sh"
git -C "$ROOT" archive --format=zip --prefix="search-governor-${VERSION}/" --output="$OUT" HEAD
echo "Created tracked-files-only archive: $OUT"
