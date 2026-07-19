#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


TOKEN = "{{SG_BIN}}"


def build_skill(root: Path, template: Path, local_extension: Path | None, output_dir: Path) -> Path:
    root = root.resolve()
    sg_bin = (root / "bin" / "sg").resolve()
    if not sg_bin.is_file():
        raise SystemExit(f"Search Governor CLI not found: {sg_bin}")
    text = template.read_text(encoding="utf-8")
    if TOKEN not in text:
        raise SystemExit(f"Skill template does not contain {TOKEN}: {template}")
    text = text.replace(TOKEN, str(sg_bin))
    if local_extension is not None and local_extension.exists():
        extension = local_extension.read_text(encoding="utf-8").strip()
        if extension:
            text = text.rstrip() + "\n\n" + extension + "\n"
    if TOKEN in text:
        raise SystemExit(f"Unresolved template token remains in generated Skill: {TOKEN}")
    if str(root / ".openclaw" / "workspace") in text:
        raise SystemExit("Generated Skill unexpectedly references an OpenClaw workspace runtime")
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "SKILL.md"
    output.write_text(text, encoding="utf-8")
    return output


def main() -> int:
    default_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Build the deployable OpenClaw Search Governor Skill.")
    parser.add_argument("--root", type=Path, default=default_root)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--local-extension", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    template = args.template or root / "integrations" / "openclaw" / "skill-template" / "SKILL.md"
    local_extension = args.local_extension or root / "integrations.local" / "openclaw-skill.local.md"
    output_dir = args.output_dir or root / "build.local" / "openclaw-search-governor"
    output = build_skill(root, template, local_extension, output_dir)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
