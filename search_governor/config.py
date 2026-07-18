from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any
from .paths import config_dir, home


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(name: str) -> dict[str, Any]:
    public_path = config_dir() / f"{name}.json"
    value = load_json(public_path)
    local_path = config_dir() / f"{name}.local.json"
    if os.environ.get("SEARCH_GOVERNOR_DISABLE_LOCAL") != "1" and local_path.exists():
        value = deep_merge(value, load_json(local_path))
    return value


def load_dotenv() -> None:
    if os.environ.get("SEARCH_GOVERNOR_DISABLE_LOCAL") == "1":
        return
    env_path = config_dir() / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def load_all_configs() -> dict[str, Any]:
    load_dotenv()
    cfg = {
        "home": str(home()),
        "provider_presets": load_config("provider_presets"),
        "reranker": load_config("reranker"),
        "deep_analyzer": load_config("deep_analyzer"),
        "scoring": load_config("scoring"),
        "fetcher": load_config("fetcher"),
        "content_cleaner": load_config("content_cleaner"),
        "retention": load_config("retention"),
    }
    return cfg
