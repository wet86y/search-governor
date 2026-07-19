#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
VERSION="${1:-0.1.1}"
OUT="${2:-$ROOT/dist/search-governor-v${VERSION}.zip}"

git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null
mkdir -p "$(dirname "$OUT")"
"$ROOT/scripts/check-public-tree.sh"
git -C "$ROOT" archive --format=zip --prefix="search-governor-${VERSION}/" --output="$OUT" HEAD
entries="$(unzip -Z1 "$OUT")"
for forbidden_prefix in 'providers.local/' 'integrations.local/' 'build.local/' 'legacy.local/' 'data/' 'connectors/'; do
  if grep -Fq "/$forbidden_prefix" <<<"$entries"; then
    echo "Forbidden local path prefix is present in archive: $forbidden_prefix" >&2
    exit 1
  fi
done
if unzip -p "$OUT" | grep -aE '(gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|Bearer[[:space:]]+[A-Za-z0-9._~+/-]{20,})' >/tmp/search-governor-archive-secret-scan.txt; then
  echo "Potential credential material found in archive" >&2
  exit 1
fi
echo "Created tracked-files-only archive: $OUT"
