from __future__ import annotations
import os
from pathlib import Path


def home() -> Path:
    env = os.environ.get("SG_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def config_dir() -> Path:
    return home() / "config"


def sources_dir() -> Path:
    """Public provider definitions shipped by the repository."""
    return home() / "providers"


def local_sources_dir() -> Path:
    """Operator-owned providers that must never be published."""
    return home() / "providers.local"


def data_dir() -> Path:
    return home() / "data"
