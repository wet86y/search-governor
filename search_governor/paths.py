from __future__ import annotations
import os
from pathlib import Path


def app_home() -> Path:
    env = os.environ.get("SG_APP_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def runtime_home() -> Path:
    env = os.environ.get("SG_RUNTIME_HOME") or os.environ.get("SG_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return app_home()


def home() -> Path:
    """Backward-compatible alias for the persistent runtime root."""
    return runtime_home()


def app_config_dir() -> Path:
    return app_home() / "config"


def config_dir() -> Path:
    return runtime_home() / "config"


def sources_dir() -> Path:
    """The single operator-owned runtime namespace for registered sources."""
    env = os.environ.get("SG_SOURCES_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return runtime_home() / "managed_sources"


def data_dir() -> Path:
    return runtime_home() / "data"
