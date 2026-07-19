#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
export SG_APP_HOME="$ROOT"
export SG_RUNTIME_HOME="${SG_RUNTIME_HOME:-$ROOT}"
export SG_HOME="$SG_RUNTIME_HOME"
"$ROOT/bin/sg" health
