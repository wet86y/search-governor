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
    """The single operator-owned runtime namespace for registered sources."""
    env = os.environ.get("SG_SOURCES_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return home() / "managed_sources"


def data_dir() -> Path:
    return home() / "data"
