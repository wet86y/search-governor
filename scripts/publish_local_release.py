#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Any

try:
    from .build_openclaw_skill import build_skill
    from .deploy_local_release import deploy as deploy_release
    from .deploy_openclaw_skill import deploy as deploy_skill
except ImportError:
    from build_openclaw_skill import build_skill
    from deploy_local_release import deploy as deploy_release
    from deploy_openclaw_skill import deploy as deploy_skill


PLUGIN_ID = "openclaw-search-governor-websearch"
DEFAULT_GATEWAY_SERVICE = "openclaw-gateway.service"


class LocalPublishError(RuntimeError):
    pass


def run_checked(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, check=True)


def git_text(source: Path, *args: str) -> str:
    return run_checked(["git", *args], cwd=source).stdout.strip()


def require_clean_committed_head(source: Path) -> str:
    if not (source / ".git").exists():
        raise LocalPublishError(f"Local publishing requires a Git checkout: {source}")
    dirty = git_text(source, "status", "--porcelain", "--untracked-files=all")
    if dirty:
        raise LocalPublishError("Working tree must be clean; commit or remove all non-ignored changes before publishing")
    return git_text(source, "rev-parse", "HEAD^{commit}")


def release_versions(source: Path) -> dict[str, str]:
    project = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
    init_text = (source / "search_governor" / "__init__.py").read_text(encoding="utf-8")
    init_match = re.search(r'^__version__\s*=\s*"([^"]+)"', init_text, re.MULTILINE)
    if init_match is None:
        raise LocalPublishError("Could not read search_governor.__version__")
    package = json.loads((source / "integrations" / "openclaw" / "package.json").read_text(encoding="utf-8"))
    plugin = json.loads((source / "integrations" / "openclaw" / "openclaw.plugin.json").read_text(encoding="utf-8"))
    return {
        "project": str(project["project"]["version"]),
        "python_package": init_match.group(1),
        "openclaw_package": str(package["version"]),
        "openclaw_plugin": str(plugin["version"]),
    }


def require_consistent_version(source: Path) -> str:
    versions = release_versions(source)
    if len(set(versions.values())) != 1:
        raise LocalPublishError(f"Release versions are inconsistent: {versions}")
    return next(iter(versions.values()))


def require_global_python(source: Path, python: Path) -> str:
    if not python.is_file():
        raise LocalPublishError(f"Global Python executable is missing: {python}")
    probe = run_checked(
        [str(python), "-c", "import json,sys; print(json.dumps({'version': list(sys.version_info[:3]), 'prefix': sys.prefix, 'base_prefix': sys.base_prefix}))"]
    )
    payload = json.loads(probe.stdout)
    if payload["version"][:2] != [3, 12]:
        raise LocalPublishError(f"Search Governor local releases require Python 3.12: {payload}")

    project = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project.get("project", {}).get("dependencies") or []
    if dependencies:
        raise LocalPublishError(
            "Runtime dependencies are declared; define an explicit release dependency strategy before publishing: "
            + ", ".join(map(str, dependencies))
        )
    return ".".join(map(str, payload["version"]))


def build_and_activate_skill(
    current: Path,
    runtime: Path,
    sg_bin: Path,
    openclaw_home: Path,
) -> dict[str, Any]:
    local_extension = runtime / "integrations" / "openclaw" / "local" / "skill-routes.md"
    output = runtime / "data" / "staging" / "openclaw-search-governor"
    built = build_skill(
        current,
        current / "integrations" / "openclaw" / "skill-template" / "SKILL.md",
        local_extension,
        output,
        sg_bin,
    )
    target = openclaw_home / "workspace" / "skills" / "openclaw-search-governor"
    deployed = deploy_skill(output, target, runtime.parent / "backups" / "openclaw-skills")
    return {
        "built": str(built),
        "target": deployed["target"],
        "archive": deployed["archive"],
        "local_extension_applied": local_extension.is_file(),
    }


def verify_openclaw_registration(openclaw_home: Path, install_root: Path) -> None:
    config_path = openclaw_home / "openclaw.json"
    if not config_path.is_file():
        raise LocalPublishError(f"OpenClaw config is missing: {config_path}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    paths = (((config.get("plugins") or {}).get("load") or {}).get("paths") or [])
    expected = str(install_root / "current" / "integrations" / "openclaw")
    if expected not in paths:
        raise LocalPublishError(f"OpenClaw plugin is not registered through the stable current path: {expected}")


def restart_and_verify_gateway(service: str, timeout: int = 60) -> dict[str, Any]:
    run_checked(["systemctl", "--user", "restart", service])
    pid = run_checked(["systemctl", "--user", "show", service, "-p", "MainPID", "--value"]).stdout.strip()
    if not pid or pid == "0":
        raise LocalPublishError(f"OpenClaw Gateway did not start: {service}")

    deadline = time.monotonic() + timeout
    last_logs = ""
    while time.monotonic() < deadline:
        active = subprocess.run(["systemctl", "--user", "is-active", "--quiet", service], check=False).returncode == 0
        logs = subprocess.run(
            ["journalctl", "--user", "-u", service, f"_PID={pid}", "--no-pager", "-o", "cat"],
            text=True,
            capture_output=True,
            check=False,
        ).stdout
        last_logs = logs[-4000:]
        if active and "http server listening" in logs and PLUGIN_ID in logs:
            return {"service": service, "pid": int(pid), "plugin": PLUGIN_ID, "ready": True}
        time.sleep(1)
    raise LocalPublishError(f"Gateway did not report the Search Governor plugin before timeout: {last_logs[-1000:]}")


def verify_release(sg_bin: Path, expected_version: str) -> dict[str, Any]:
    version = run_checked([str(sg_bin), "--version"]).stdout.strip()
    if version != f"sg {expected_version}":
        raise LocalPublishError(f"Stable CLI version mismatch: {version}")
    run_checked([str(sg_bin), "health"])
    return {"version": version, "health": "ok"}


def publish(args: argparse.Namespace) -> dict[str, Any]:
    source = args.source_root.expanduser().resolve()
    install_root = args.install_root.expanduser().resolve()
    bin_dir = args.bin_dir.expanduser().resolve()
    sg_bin = bin_dir / "sg"
    openclaw_home = args.openclaw_home.expanduser().resolve()

    commit = require_clean_committed_head(source)
    version = require_consistent_version(source)
    python_version = require_global_python(source, args.python.expanduser().resolve())
    if not args.skip_checks:
        subprocess.run(["bash", "scripts/check.sh"], cwd=source, check=True)

    release = deploy_release(source, install_root, "HEAD", bin_dir)
    if release["commit"] != commit:
        raise LocalPublishError(f"Deployed commit mismatch: {release['commit']} != {commit}")
    skill = build_and_activate_skill(install_root / "current", install_root / "runtime", sg_bin, openclaw_home)
    verify_openclaw_registration(openclaw_home, install_root)
    gateway = None if args.skip_gateway_restart else restart_and_verify_gateway(args.gateway_service, args.gateway_timeout)
    verification = verify_release(sg_bin, version)
    return {
        "version": version,
        "commit": commit,
        "python": python_version,
        "release": release,
        "skill": skill,
        "gateway": gateway,
        "verification": verification,
        "github_actions": "not_performed",
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Publish Search Governor locally from a clean, committed HEAD without GitHub operations.")
    parser.add_argument("--source-root", type=Path, default=root)
    parser.add_argument("--install-root", type=Path, default=Path.home() / ".local" / "share" / "search-governor")
    parser.add_argument("--bin-dir", type=Path, default=Path.home() / ".local" / "bin")
    parser.add_argument("--openclaw-home", type=Path, default=Path.home() / ".openclaw")
    parser.add_argument("--python", type=Path, default=Path("/usr/bin/python3"))
    parser.add_argument("--gateway-service", default=DEFAULT_GATEWAY_SERVICE)
    parser.add_argument("--gateway-timeout", type=int, default=60)
    parser.add_argument("--skip-checks", action="store_true", help="Skip repository checks; intended only for isolated tests.")
    parser.add_argument("--skip-gateway-restart", action="store_true", help="Deploy without restarting OpenClaw Gateway.")
    args = parser.parse_args()
    try:
        result = publish(args)
    except (LocalPublishError, subprocess.CalledProcessError, ValueError, OSError) as exc:
        print(f"Local release failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
