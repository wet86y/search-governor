#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


REQUIRED_TOP_LEVEL = {
    "id",
    "name",
    "exposure",
    "type",
    "entrypoint",
    "timeout_sec",
    "requires_env",
    "capabilities",
    "supports",
    "defaults",
    "limits",
    "output_contract",
}

REQUIRED_CAPABILITIES = {
    "result_kind",
    "search_result",
    "snippet",
    "text_chunk",
    "content_granularity",
    "inline_content_for_analysis",
    "external_fetch",
    "expandable_content",
    "native_fetch",
    "full_document",
}

REQUIRED_OUTPUT_FIELDS = {"title", "url", "snippet", "provider", "rank"}


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        fail("usage: scripts/validate_source.py <providers/<id>/source.json>")
    path = Path(argv[1])
    payload = json.loads(path.read_text(encoding="utf-8"))
    missing = sorted(REQUIRED_TOP_LEVEL - set(payload))
    if missing:
        fail(f"{path}: missing top-level fields: {missing}")
    if "enabled" in payload:
        fail(f"{path}: top-level enabled is not allowed; use a registry.json entry")
    caps = payload.get("capabilities")
    if not isinstance(caps, dict):
        fail(f"{path}: capabilities must be object")
    missing_caps = sorted(REQUIRED_CAPABILITIES - set(caps))
    if missing_caps:
        fail(f"{path}: missing capabilities fields: {missing_caps}")
    supports = payload.get("supports")
    if not isinstance(supports, dict):
        fail(f"{path}: supports must be object")
    output_contract = payload.get("output_contract")
    if not isinstance(output_contract, dict):
        fail(f"{path}: output_contract must be object")
    required_fields = set(output_contract.get("required_fields") or [])
    missing_output = sorted(REQUIRED_OUTPUT_FIELDS - required_fields)
    if missing_output:
        fail(f"{path}: output_contract.required_fields missing: {missing_output}")
    print(f"OK: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
