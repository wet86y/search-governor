#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path


def validate_skill(root: Path) -> None:
    skill = root / "SKILL.md"
    if not skill.is_file():
        raise ValueError(f"Generated Skill is missing SKILL.md: {root}")
    text = skill.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "name: \"openclaw-search-governor\"" not in text:
        raise ValueError(f"Unexpected generated Skill metadata: {skill}")
    if "{{SG_BIN}}" in text:
        raise ValueError(f"Generated Skill contains an unresolved CLI token: {skill}")


def deploy(source: Path, target: Path, archive_root: Path) -> dict[str, str | None]:
    source = source.resolve()
    target = target.resolve(strict=False)
    archive_root = archive_root.resolve(strict=False)
    validate_skill(source)
    if source == target or target in source.parents or source in target.parents:
        raise ValueError("Source and target Skill directories must not overlap")
    target.parent.mkdir(parents=True, exist_ok=True)
    archive_root.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.staging-{uuid.uuid4().hex[:8]}"
    archive: Path | None = None
    try:
        shutil.copytree(source, staging)
        validate_skill(staging)
        if target.exists() or target.is_symlink():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            archive = archive_root / stamp
            suffix = 0
            while archive.exists():
                suffix += 1
                archive = archive_root / f"{stamp}-{suffix}"
            os.replace(target, archive)
        os.replace(staging, target)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        if archive is not None and archive.exists() and not target.exists():
            os.replace(archive, target)
        raise
    return {"target": str(target), "archive": str(archive) if archive else None}


def main() -> int:
    parser = argparse.ArgumentParser(description="Atomically deploy a generated local OpenClaw Skill.")
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--archive-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(deploy(args.source, args.target, args.archive_root), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
