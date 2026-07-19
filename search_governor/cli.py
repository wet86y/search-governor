from __future__ import annotations
import argparse
import json
import os
import shlex
import shutil
import sys
from pathlib import Path
from . import __version__
from .config import load_all_configs
from .sources import load_sources
from .gc import cleanup
from .pipeline import search, PipelineError
from .paths import app_home, data_dir, home, runtime_home


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sg", description="Provider-neutral aggregated search governance engine.")
    p.add_argument("--version", action="version", version=f"sg {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("search", help="Aggregate sources, dedupe, rerank, optionally fetch or expand Top N, and return evidence.")
    sp.add_argument("query", help="Search query")
    sp.add_argument("--mode", choices=["fast", "full", "deep"], default="fast", help="Search routing mode. fast returns summaries quickly, full expands recall and content, deep expands content and runs a final text-model analysis.")
    sp.add_argument("--return-count", type=int, default=None, help="Number of final Top sources to return. Defaults to mode policy: fast=5, full/deep=8.")
    sp.add_argument("--per-provider-count", type=int, default=None, help="Optional per-source cap within the mode total provider budget.")
    sp.add_argument("--provider-counts", default=None, help="Comma-separated per-provider counts, e.g. source_a:10,source_b:5.")
    sp.add_argument("--provider-total-budget", type=int, default=None, help="Debug-only override for the mode total provider budget.")
    sp.add_argument("--providers", default=None, help="Comma-separated manually registered provider IDs. Overrides the configured preset.")
    sp.add_argument("--provider-preset", "--preset", dest="provider_preset", default=None, help="Manually configured provider mix preset.")
    sp.add_argument("--search-depth", default="basic", choices=["basic", "advanced"], help="Unified depth hint. Adapters may downgrade unsupported values.")
    sp.add_argument("--freshness", default=None, help="Freshness hint: day/week/month/year/7d/30d/etc. Adapter maps support best-effort.")
    sp.add_argument("--date-after", default=None, help="YYYY-MM-DD lower date bound if supported.")
    sp.add_argument("--date-before", default=None, help="YYYY-MM-DD upper date bound if supported.")
    sp.add_argument("--topic", default="general", help="general/news/finance/etc. Adapter maps support best-effort.")
    sp.add_argument("--include-domains", default=None, help="Comma-separated domain allow/prefer list.")
    sp.add_argument("--exclude-domains", default=None, help="Comma-separated domain deny list.")
    sp.add_argument("--locale", default="zh-CN", help="Locale hint, default zh-CN.")
    sp.add_argument("--language", default=None, help="ISO language hint, e.g. zh/en.")
    sp.add_argument("--country", default=None, help="ISO country hint, e.g. CN/US.")
    sp.add_argument("--include-provider-answer", action="store_true", help="Allow adapters to include provider-generated answer summaries when URL-grounded.")
    sp.add_argument("--timeout-sec", type=int, default=30, help="Default provider timeout hint.")
    sp.add_argument("--format", choices=["markdown", "json"], default="markdown")
    sp.add_argument("--fetch-mode", choices=["sync", "defer", "off"], default="defer", help="Body fetch/expansion mode. Only applies to fast mode; full/deep always expand synchronously. sync: wait for external/native content before returning. defer (default for fast): return snippets immediately and fetch externally fetchable Top N in background. off: disable body fetch/expansion.")
    sp.add_argument("--no-fetch", action="store_true", help="Disable Top-N body fetch/expansion; mainly for debugging.")
    sp.add_argument("--allow-rule-fallback", action="store_true", help="If reranker fails, continue with rule-only ranking.")
    sp.add_argument("--allow-analysis-fallback", action="store_true", help="Allow deep mode to emit a deterministic evidence outline when no analysis model is configured.")
    sp.add_argument("--allow-disabled-sources", action="store_true", help="Allow running disabled sources during adapter development.")
    sp.add_argument("--allow-large-return-count", action="store_true", help="Allow return_count > 8.")
    sp.add_argument("--allow-large-per-provider-count", action="store_true", help="Allow per_provider_count > 20.")
    sp.add_argument("--allow-provider-total-budget-override", action="store_true", help="Allow --provider-total-budget or over-budget --provider-counts for controlled debugging.")
    sp.add_argument("--brief-file", default=None, help="Markdown brief file for deep mode.")
    sp.add_argument("--point-question", default=None, help="Deep brief: point question.")
    sp.add_argument("--goal", default=None, help="Deep brief: goal.")
    sp.add_argument("--necessary-context", default=None, help="Deep brief: necessary local/project/user context.")
    sp.add_argument("--must-answer", default=None, help="Deep brief: must-answer checklist.")
    sp.add_argument("--boundaries", default=None, help="Deep brief: search boundaries.")
    sp.add_argument("--output-use", default=None, help="Deep brief: how the final evidence article will be used.")

    sub.add_parser("providers", help="List manually registered providers.")
    sub.add_parser("health", help="Check local environment, config, and source registrations.")
    sub.add_parser("clean", help="Run retention cleanup now.")
    rp = sub.add_parser("read", help="Read a fetched document from cache by cache key or URL.")
    group = rp.add_mutually_exclusive_group(required=True)
    group.add_argument("--cache-key")
    group.add_argument("--url")
    rp.add_argument("--format", choices=["markdown", "json"], default="markdown")
    return p


def cmd_providers() -> int:
    for sid, spec in load_sources().items():
        print(f"{sid}\tenabled={spec.enabled}\ttype={spec.config.get('type')}\tentrypoint={spec.config.get('entrypoint')}")
    return 0


def _default_preset_sources(cfg: dict) -> set[str]:
    presets_cfg = cfg.get("provider_presets", {})
    default_name = presets_cfg.get("default_preset", "total")
    preset = (presets_cfg.get("presets") or {}).get(default_name) or {}
    weights = preset.get("weights") if isinstance(preset.get("weights"), dict) else {}
    return set(weights.keys())


def _mode_default_preset_names(cfg: dict) -> dict[str, str]:
    presets_cfg = cfg.get("provider_presets", {})
    mode_defaults = presets_cfg.get("mode_default_presets", {})
    if not isinstance(mode_defaults, dict):
        return {}
    return {str(k): str(v) for k, v in mode_defaults.items() if str(k).strip() and str(v).strip()}


def _check_capabilities(caps: dict) -> list[str]:
    required = [
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
    ]
    return [k for k in required if k not in caps]


def _resolve_dependency_file(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else home() / path


def _check_runtime_dependencies(spec) -> tuple[list[str], list[str]]:
    deps = spec.config.get("runtime_dependencies") if isinstance(spec.config.get("runtime_dependencies"), dict) else {}
    missing_commands = [cmd for cmd in deps.get("commands", []) if not shutil.which(str(cmd))]
    missing_files = [path for path in deps.get("files", []) if not _resolve_dependency_file(str(path)).exists()]
    return missing_commands, missing_files


def cmd_health() -> int:
    ok = True
    print(f"SG_APP_HOME={app_home()}")
    print(f"SG_RUNTIME_HOME={runtime_home()}")
    print(f"data_dir={data_dir()}")
    default_sources: set[str] = set()
    try:
        cfg = load_all_configs()
        print("config=ok")
        default_sources = _default_preset_sources(cfg)
        print(f"default_preset_sources={','.join(sorted(default_sources))}")
        mode_default_presets = _mode_default_preset_names(cfg)
        if mode_default_presets:
            print(
                "mode_default_presets="
                + ",".join(f"{mode}:{preset}" for mode, preset in sorted(mode_default_presets.items()))
            )
        reranker_enabled = bool(cfg["reranker"].get("enabled", False))
        key_env = cfg["reranker"].get("api_key_env", "SEARCH_GOVERNOR_RERANK_API_KEY")
        print(f"reranker_enabled={reranker_enabled}")
        print(f"reranker_model={cfg['reranker'].get('model') or 'not-configured'}")
        print(f"{key_env}={'set' if os.environ.get(key_env) else 'missing'}")
        if reranker_enabled and not os.environ.get(key_env):
            ok = False
    except Exception as e:
        print(f"config=failed: {e}")
        ok = False
    try:
        sources = load_sources()
        for sid, spec in sources.items():
            entrypoint = spec.config.get("entrypoint", "")
            entry_parts = shlex.split(str(entrypoint)) if entrypoint else []
            adapter_path = spec.path / entry_parts[-1] if entry_parts else spec.path
            adapter_ok = adapter_path.exists()
            caps = spec.config.get("capabilities") if isinstance(spec.config.get("capabilities"), dict) else {}
            missing_caps = _check_capabilities(caps)
            required_env = [str(x) for x in spec.config.get("requires_env", [])]
            missing_env = [name for name in required_env if not os.environ.get(name)]
            missing_commands, missing_files = _check_runtime_dependencies(spec)
            in_default = sid in default_sources
            warning_only = bool(spec.enabled and not in_default and (missing_env or missing_commands or missing_files))
            adapter_status = "ok" if adapter_ok else "missing"
            caps_status = "ok" if not missing_caps else "missing:" + ",".join(missing_caps)
            env_status = "ok" if not missing_env else "missing:" + ",".join(missing_env)
            dep_missing = []
            dep_missing.extend(f"cmd:{cmd}" for cmd in missing_commands)
            dep_missing.extend(f"file:{path}" for path in missing_files)
            deps_status = "ok" if not dep_missing else "missing:" + ",".join(dep_missing)
            print(
                f"source={sid}\tenabled={spec.enabled}\tdefault_preset={in_default}"
                f"\tadapter={adapter_status}\tcapabilities={caps_status}"
                f"\tenv={env_status}\tdeps={deps_status}"
                + ("\twarning_only=True" if warning_only else "")
            )
            if spec.enabled and not adapter_ok:
                ok = False
            if spec.enabled and missing_caps:
                ok = False
            if spec.enabled and in_default and (missing_env or missing_commands or missing_files):
                ok = False
            if in_default and not spec.enabled:
                ok = False
    except Exception as e:
        print(f"sources=failed: {e}")
        ok = False
    return 0 if ok else 2


def cmd_clean() -> int:
    cfg = load_all_configs()
    report = cleanup(cfg["retention"])
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_read(args) -> int:
    from .fetch_cache import cache_key_for_url, load_cache
    from .normalizer import normalize_url

    key = args.cache_key or cache_key_for_url(normalize_url(args.url))
    payload = load_cache(key)
    if not payload:
        if args.format == "json":
            print("null")
        print(f"ERROR: cache miss: {key}", file=sys.stderr)
        return 3
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"# {payload.get('fetched_title') or payload.get('title') or key}\n")
        print(f"- cache_key: {key}")
        print(f"- provider: {payload.get('provider')}")
        print(f"- url: {payload.get('url')}")
        print(f"- fetch_status: {payload.get('fetch_status')}")
        if payload.get("fetch_error"):
            print(f"- fetch_error: {payload.get('fetch_error')}")
        print("")
        print((payload.get("fetched_content") or payload.get("snippet") or "").strip())
    return 0


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "providers":
            return cmd_providers()
        if args.cmd == "health":
            return cmd_health()
        if args.cmd == "clean":
            return cmd_clean()
        if args.cmd == "read":
            return cmd_read(args)
        if args.cmd == "search":
            result = search(args)
            if args.format == "json":
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(result["markdown"])
            return 0
    except PipelineError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
