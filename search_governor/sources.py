from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from .paths import sources_dir


@dataclass
class SourceSpec:
    id: str
    path: Path
    config: dict[str, Any]
    enabled: bool


class SourceRegistryError(RuntimeError):
    pass


def _load_registry(root: Path) -> list[SourceSpec]:
    registry_path = root / "sources.json"
    if not registry_path.exists():
        return []
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    specs: list[SourceSpec] = []
    seen: set[str] = set()
    for item in registry.get("sources", []):
        sid = str(item.get("id") or "").strip()
        if not sid:
            raise SourceRegistryError(f"Provider without id in {registry_path}")
        if sid == "search-governor":
            raise SourceRegistryError("Provider id 'search-governor' is reserved to prevent recursive aggregation")
        if sid in seen:
            raise SourceRegistryError(f"Duplicate provider id in {registry_path}: {sid}")
        seen.add(sid)
        relative_path = str(item.get("path") or "").strip()
        if not relative_path:
            raise SourceRegistryError(f"Provider {sid} has no manifest path")
        manifest_path = (root / relative_path).resolve()
        root_resolved = root.resolve()
        if manifest_path != root_resolved and root_resolved not in manifest_path.parents:
            raise SourceRegistryError(f"Provider {sid} escapes registry root: {relative_path}")
        cfg = json.loads(manifest_path.read_text(encoding="utf-8"))
        if cfg.get("id") != sid:
            raise SourceRegistryError(f"Provider id mismatch: registry={sid}, manifest={cfg.get('id')}")
        specs.append(SourceSpec(id=sid, path=manifest_path.parent, config=cfg, enabled=bool(item.get("enabled", False))))
    return specs


def load_sources() -> dict[str, SourceSpec]:
    out: dict[str, SourceSpec] = {}
    for spec in _load_registry(sources_dir()):
        out[spec.id] = spec
    return out
