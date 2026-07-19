from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


EXTERNAL_RE = re.compile(
    r"\s*<<<EXTERNAL_UNTRUSTED_CONTENT id=\"[^\"]+\">>>\s*"
    r"(?:Source:[^\n]*\n---\n)?"
    r"(.*?)"
    r"\s*<<<END_EXTERNAL_UNTRUSTED_CONTENT id=\"[^\"]+\">>>\s*",
    re.DOTALL,
)


def read_request() -> dict[str, Any]:
    return json.load(sys.stdin)


def clean_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = EXTERNAL_RE.sub(lambda m: m.group(1), text)
    return re.sub(r"\s+", " ", text).strip()


def emit_jsonl(items: list[dict[str, Any]]) -> None:
    for item in items:
        print(json.dumps(item, ensure_ascii=False))


def emit_report(report: dict[str, Any]) -> None:
    print("SG_REPORT_JSON=" + json.dumps(report, ensure_ascii=False), file=sys.stderr)


def run_json(cmd: list[str], *, cwd: str | Path | None = None, env: dict[str, str] | None = None, timeout: int = 30) -> Any:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(f"adapter timeout after {timeout}s: {cmd[0]}", file=sys.stderr)
        sys.exit(20)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        print(detail or f"{cmd[0]} exited {proc.returncode}", file=sys.stderr)
        sys.exit(proc.returncode or 20)
    try:
        return json.loads(proc.stdout)
    except Exception as exc:
        print(f"failed to parse JSON from {cmd[0]}: {exc}", file=sys.stderr)
        sys.exit(21)


def provider_count(req: dict[str, Any], default: int = 10, maximum: int = 20) -> int:
    try:
        count = int(req.get("per_provider_count") or default)
    except Exception:
        count = default
    return max(1, min(count, maximum))


def first_result_container(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict) and isinstance(payload.get("outputs"), list):
        for output in payload["outputs"]:
            result = output.get("result") if isinstance(output, dict) else None
            if isinstance(result, dict):
                return result
    return payload if isinstance(payload, dict) else {}
