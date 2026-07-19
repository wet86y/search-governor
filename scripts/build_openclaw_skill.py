#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path


TOKEN = "{{SG_BIN}}"
DESCRIPTION_TOKEN = "{{LOCAL_DESCRIPTION_SUFFIX}}"
DESCRIPTION_DIRECTIVE = re.compile(r"^<!--\s*description-suffix:\s*(.*?)\s*-->\s*", re.MULTILINE)


def build_skill(root: Path, template: Path, local_extension: Path | None, output_dir: Path) -> Path:
    root = root.resolve()
    sg_bin = (root / "bin" / "sg").resolve()
    if not sg_bin.is_file():
        raise SystemExit(f"Search Governor CLI not found: {sg_bin}")
    text = template.read_text(encoding="utf-8")
    if TOKEN not in text:
        raise SystemExit(f"Skill template does not contain {TOKEN}: {template}")
    if DESCRIPTION_TOKEN not in text:
        raise SystemExit(f"Skill template does not contain {DESCRIPTION_TOKEN}: {template}")
    text = text.replace(TOKEN, str(sg_bin))
    description_suffix = ""
    if local_extension is not None and local_extension.exists():
        extension = local_extension.read_text(encoding="utf-8").strip()
        match = DESCRIPTION_DIRECTIVE.search(extension)
        if match:
            description_suffix = match.group(1).strip()
            if any(char in description_suffix for char in ('"', "\n", "\r")):
                raise SystemExit("Local Skill description suffix must be a single YAML-safe line without quotes")
            extension = DESCRIPTION_DIRECTIVE.sub("", extension, count=1).strip()
        if extension:
            text = text.rstrip() + "\n\n" + extension + "\n"
    text = text.replace(DESCRIPTION_TOKEN, description_suffix)
    if TOKEN in text:
        raise SystemExit(f"Unresolved template token remains in generated Skill: {TOKEN}")
    if DESCRIPTION_TOKEN in text:
        raise SystemExit(f"Unresolved template token remains in generated Skill: {DESCRIPTION_TOKEN}")
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
    local_extension = args.local_extension or root / "integrations" / "openclaw" / "local" / "skill-routes.md"
    output_dir = args.output_dir or root / "data" / "staging" / "openclaw-search-governor"
    output = build_skill(root, template, local_extension, output_dir)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
