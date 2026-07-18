#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
cd "$ROOT"
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
mkdir -p "$HOME/.local/bin"
ln -sf "$ROOT/bin/sg" "$HOME/.local/bin/sg"
mkdir -p "$ROOT/data/runs" "$ROOT/data/logs" "$ROOT/data/tmp"
if [[ ! -f "$ROOT/config/.env" ]]; then
  cp "$ROOT/config/.env.example" "$ROOT/config/.env"
  echo "Created config/.env. Add optional model keys and local provider credentials as needed."
fi
echo "Installed. Test with: sg --version && sg health"
