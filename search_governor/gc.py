from __future__ import annotations
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from .paths import data_dir


def dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except FileNotFoundError:
                pass
    return total


def run_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return 0.0


def cleanup(retention: dict) -> dict:
    d = data_dir()
    runs = d / "runs"
    tmp = d / "tmp"
    logs = d / "logs"
    runs.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    report = {"removed": [], "size_before": dir_size(d)}
    # tmp always clear
    for p in tmp.iterdir():
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            try: p.unlink()
            except FileNotFoundError: pass
    ttl = int(retention.get("ttl_days", 3))
    cutoff = datetime.now() - timedelta(days=ttl)
    for r in list(runs.iterdir()):
        if not r.is_dir():
            continue
        m = datetime.fromtimestamp(run_mtime(r))
        if m < cutoff:
            shutil.rmtree(r, ignore_errors=True)
            report["removed"].append(str(r.name))
    max_bytes = int(retention.get("max_data_mb", 10)) * 1024 * 1024
    target_bytes = int(retention.get("cleanup_target_mb", 8)) * 1024 * 1024
    size = dir_size(d)
    if size > max_bytes:
        dirs = sorted([p for p in runs.iterdir() if p.is_dir()], key=run_mtime)
        for r in dirs:
            if dir_size(d) <= target_bytes:
                break
            shutil.rmtree(r, ignore_errors=True)
            report["removed"].append(str(r.name))
    report["size_after"] = dir_size(d)
    return report
