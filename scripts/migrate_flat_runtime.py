#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path


def move_checked(source: Path, target: Path, moved: list[tuple[Path, Path]], dry_run: bool) -> None:
    if not source.exists() and not source.is_symlink():
        return
    if target.exists() or target.is_symlink():
        raise RuntimeError(f"Migration target already exists: {target}")
    if dry_run:
        print(f"MOVE {source} -> {target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, target)
    moved.append((source, target))


def migrate(install_root: Path, dry_run: bool = False) -> dict:
    install_root = install_root.expanduser().resolve()
    if not (install_root / ".git").is_dir():
        raise RuntimeError(f"Legacy flat Git runtime not found: {install_root}")
    runtime = install_root / "runtime"
    if runtime.exists() and any(runtime.iterdir()):
        raise RuntimeError(f"Runtime destination is not empty: {runtime}")

    moved: list[tuple[Path, Path]] = []
    planned_existing: list[tuple[Path, Path]] = []
    plan = [
        (install_root / "managed_sources", runtime / "managed_sources"),
        (install_root / "connectors", runtime / "connectors"),
        (install_root / "data", runtime / "data"),
        (install_root / "integrations" / "openclaw" / "local", runtime / "integrations" / "openclaw" / "local"),
    ]
    for local_config in sorted((install_root / "config").glob("*.local.json")):
        plan.append((local_config, runtime / "config" / local_config.name))
    plan.append((install_root / "config" / ".env", runtime / "config" / ".env"))

    try:
        for source, target in plan:
            if source.exists() or source.is_symlink():
                planned_existing.append((source, target))
            move_checked(source, target, moved, dry_run)
    except Exception:
        for source, target in reversed(moved):
            if target.exists() or target.is_symlink():
                source.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, source)
        raise

    result = {
        "version": 1,
        "legacy_root": str(install_root),
        "runtime_root": str(runtime),
        "migrated_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": dry_run,
        "moves": [{"source": str(source), "target": str(target)} for source, target in planned_existing],
    }
    if not dry_run:
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "migration-state.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Move persistent assets out of a legacy flat Search Governor Git runtime.")
    parser.add_argument("--install-root", type=Path, default=Path.home() / ".local" / "share" / "search-governor")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = migrate(args.install_root, args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
