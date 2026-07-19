#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path


def run(*args: str, cwd: Path, capture: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=capture)


def git_text(source: Path, *args: str) -> str:
    return run("git", *args, cwd=source).stdout.decode("utf-8").strip()


def project_version(source: Path, ref: str) -> str:
    text = git_text(source, "show", f"{ref}:pyproject.toml")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise RuntimeError("Could not read project version from pyproject.toml")
    return match.group(1)


def extract_archive(source: Path, ref: str, destination: Path) -> None:
    archive = run("git", "archive", "--format=tar", ref, cwd=source).stdout
    destination.mkdir(parents=True, exist_ok=False)
    root = destination.resolve()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as payload:
        for member in payload.getmembers():
            target = (destination / member.name).resolve()
            if target != root and root not in target.parents:
                raise RuntimeError(f"Archive member escapes release root: {member.name}")
        payload.extractall(destination, filter="data")


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def install_wrapper(install_root: Path, bin_dir: Path) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper = bin_dir / "sg"
    content = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'exec "{install_root}/current/bin/sg" "$@"\n'
    )
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=bin_dir, delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.chmod(0o755)
    os.replace(temporary, wrapper)
    return wrapper


def switch_current(install_root: Path, release_dir: Path) -> str | None:
    current = install_root / "current"
    previous = os.readlink(current) if current.is_symlink() else None
    if current.exists() and not current.is_symlink():
        raise RuntimeError(f"Current path exists and is not a symlink: {current}")
    temporary = install_root / f".current-{os.getpid()}"
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(Path("releases") / release_dir.name)
    os.replace(temporary, current)
    return previous


def deploy(source: Path, install_root: Path, ref: str, bin_dir: Path, skip_venv: bool = False) -> dict:
    source = source.expanduser().resolve()
    install_root = install_root.expanduser().resolve()
    commit = git_text(source, "rev-parse", f"{ref}^{{commit}}")
    version = project_version(source, ref)
    release_id = f"{version}-{commit[:12]}"
    releases = install_root / "releases"
    runtime = install_root / "runtime"
    release_dir = releases / release_id
    releases.mkdir(parents=True, exist_ok=True)
    for path in (
        runtime / "config",
        runtime / "managed_sources",
        runtime / "connectors",
        runtime / "data" / "runs",
        runtime / "data" / "logs",
        runtime / "data" / "tmp",
        runtime / "integrations" / "openclaw" / "local",
        install_root / "backups",
    ):
        path.mkdir(parents=True, exist_ok=True)

    if not release_dir.exists():
        extract_archive(source, ref, release_dir)
        try:
            if not skip_venv:
                subprocess.run(["python3", "-m", "venv", str(release_dir / ".venv")], check=True)
                subprocess.run(
                    [str(release_dir / ".venv" / "bin" / "python"), "-m", "pip", "install", "--no-deps", "."],
                    cwd=release_dir,
                    check=True,
                )
            (release_dir / "DEPLOYED_COMMIT").write_text(commit + "\n", encoding="utf-8")
        except Exception:
            subprocess.run(["find", str(release_dir), "-depth", "-delete"], check=False)
            raise
    elif (release_dir / "DEPLOYED_COMMIT").read_text(encoding="utf-8").strip() != commit:
        raise RuntimeError(f"Existing release has an unexpected commit marker: {release_dir}")

    env_file = runtime / "config" / ".env"
    if not env_file.exists():
        env_file.write_text((release_dir / "config" / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
        env_file.chmod(0o600)

    previous = switch_current(install_root, release_dir)
    wrapper = install_wrapper(install_root, bin_dir.expanduser().resolve())
    state = {
        "version": 1,
        "release_id": release_id,
        "commit": commit,
        "ref": ref,
        "source": str(source),
        "current": str(release_dir),
        "previous_current": previous,
        "runtime": str(runtime),
        "wrapper": str(wrapper),
        "installed_at": datetime.now().isoformat(timespec="seconds"),
    }
    atomic_json(install_root / "install-state.json", state)
    return state


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Deploy a committed Search Governor release into a stable current/releases layout.")
    parser.add_argument("--source-root", type=Path, default=root)
    parser.add_argument("--install-root", type=Path, default=Path.home() / ".local" / "share" / "search-governor")
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--bin-dir", type=Path, default=Path.home() / ".local" / "bin")
    parser.add_argument("--skip-venv", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    print(json.dumps(deploy(args.source_root, args.install_root, args.ref, args.bin_dir, args.skip_venv), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
