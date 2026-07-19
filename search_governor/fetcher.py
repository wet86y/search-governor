from __future__ import annotations
import json
import re
import shlex
import subprocess
import sys
import urllib.request
import urllib.error
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse, quote, urlsplit, urlunsplit
from .models import Candidate
from .paths import app_home


BROWSER_FALLBACK_ERROR_KINDS = {"blocked", "rate_limited", "empty"}


def resolve_browser_fallback_script(script_value: str) -> Path:
    script = Path(script_value).expanduser() if script_value else Path()
    if script_value and not script.is_absolute():
        script = app_home() / script
    return script


class TextHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0
        self.title_parts: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg", "nav", "header", "footer", "aside", "form", "button", "select", "option", "iframe", "canvas"}:
            self.skip_depth += 1
            return
        if tag == "title":
            self.in_title = True
        if tag in {"p", "br", "div", "section", "article", "main", "li", "h1", "h2", "h3", "h4", "pre", "code", "blockquote"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg", "nav", "header", "footer", "aside", "form", "button", "select", "option", "iframe", "canvas"}:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag == "title":
            self.in_title = False
        if tag in {"p", "li", "h1", "h2", "h3", "h4", "pre", "blockquote"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.skip_depth:
            return
        text = data.strip()
        if not text:
            return
        if self.in_title:
            self.title_parts.append(text)
        self.parts.append(text + " ")

    def text(self) -> str:
        out = "".join(self.parts)
        out = re.sub(r"[ \t]+", " ", out)
        out = re.sub(r"\n\s*\n+", "\n\n", out)
        return out.strip()

    def title(self) -> str:
        return " ".join(self.title_parts).strip()


def _preferred_github_fetch(url: str) -> tuple[str, str] | None:
    p = urlparse(url)
    if p.netloc.lower() != "github.com":
        return None
    parts = [x for x in p.path.split("/") if x]
    if len(parts) >= 5 and parts[2] == "blob":
        owner, repo = parts[0], parts[1]
        rest = parts[3:]
        if rest and rest[0] == "-":
            rest[0] = "main"
        ref_and_path = "/".join(rest)
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref_and_path}", "github_raw_blob"
    if len(parts) >= 4 and parts[2] == "issues" and parts[3].isdigit():
        owner, repo, number = parts[0], parts[1], parts[3]
        return f"https://api.github.com/repos/{owner}/{repo}/issues/{number}", "github_issue_api"
    return None


def _encode_url(url: str) -> str:
    """Percent-encode non-ASCII characters in URL path and query."""
    p = urlsplit(url)
    encoded_path = quote(p.path, safe="/%")
    encoded_query = quote(p.query, safe="=&?/%") if p.query else ""
    return urlunsplit((p.scheme, p.netloc, encoded_path, encoded_query, p.fragment))


def _read_url_text(url: str, cfg: dict, headers: dict[str, str] | None = None) -> tuple[str, str]:
    req_headers = {"User-Agent": cfg.get("user_agent", "SearchGovernor/0.1")}
    if headers:
        req_headers.update(headers)
    encoded_url = _encode_url(url)
    req = urllib.request.Request(encoded_url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=int(cfg.get("timeout_sec", 20))) as resp:
        raw = resp.read(max(300000, int(cfg.get("max_chars_per_doc", 5000)) * 20))
        content_type = resp.headers.get("Content-Type", "")
    return raw.decode("utf-8", errors="replace"), content_type


def _github_issue_text(payload_text: str) -> tuple[str, str | None]:
    payload = json.loads(payload_text)
    title = str(payload.get("title") or "").strip()
    body = str(payload.get("body") or "").strip()
    user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    meta = []
    if payload.get("state"):
        meta.append(f"state: {payload.get('state')}")
    if payload.get("created_at"):
        meta.append(f"created_at: {payload.get('created_at')}")
    if user.get("login"):
        meta.append(f"author: {user.get('login')}")
    parts = [title, "\n".join(meta), body]
    return "\n\n".join(x for x in parts if x).strip(), title or None


def fetch_one(c: Candidate, cfg: dict) -> Candidate:
    if materialize_native_content(c, cfg):
        return c
    if materialize_inline_content(c, cfg):
        return c
    if not external_fetch_allowed(c, cfg):
        c.fetch_status = "external_fetch_disabled"
        c.fetch_error = "provider does not allow external body fetch"
        return c
    allowed = set(cfg.get("allowed_schemes", ["http", "https"]))
    p = urlparse(c.url)
    if p.scheme not in allowed:
        c.fetch_status = "failed"
        c.fetch_error = f"scheme not allowed: {p.scheme}"
        return c

    attempts: list[tuple[str, str]] = []
    github_preferred = _preferred_github_fetch(c.url)
    if github_preferred:
        attempts.append(github_preferred)
    attempts.append((c.url, "default"))

    text = ""
    content_type = ""
    title = None
    last_error = None
    last_error_kind = None
    for fetch_url, fetch_kind in attempts:
        try:
            headers = {"Accept": "application/vnd.github+json"} if fetch_kind == "github_issue_api" else None
            text, content_type = _read_url_text(fetch_url, cfg, headers=headers)
            if fetch_kind == "github_issue_api":
                parsed_text, title = _github_issue_text(text)
            elif "html" in content_type.lower() or "<html" in text[:500].lower():
                parser = TextHTMLParser()
                try:
                    parser.feed(text)
                    parsed_text = parser.text()
                    title = parser.title()
                except Exception:
                    parsed_text = re.sub(r"<[^>]+>", " ", text)
                    title = None
            else:
                parsed_text = text
                title = None
            if parsed_text.strip():
                if fetch_kind != "default":
                    c.extra["preferred_fetch"] = {"kind": fetch_kind, "url": fetch_url}
                break
            last_error = "parsed empty content"
            last_error_kind = "empty"
        except urllib.error.HTTPError as e:
            last_error = f"HTTP {e.code}: {e.reason}"
            if e.code in (401, 403):
                last_error_kind = "blocked"
            elif e.code in (404, 410):
                last_error_kind = "not_found"
            elif e.code == 429:
                last_error_kind = "rate_limited"
            else:
                last_error_kind = "http_error"
            continue
        except urllib.error.URLError as e:
            last_error = str(e.reason)[:500]
            last_error_kind = "unreachable"
            continue
        except ConnectionError as e:
            last_error = str(e)[:500]
            last_error_kind = "unreachable"
            continue
        except Exception as e:
            last_error = str(e)[:500]
            last_error_kind = "error"
            continue
    else:
        if browser_fallback_allowed(last_error_kind, cfg):
            browser_result = fetch_with_browser_fallback(c, cfg, last_error or "direct fetch failed", last_error_kind)
            if browser_result.fetch_status in {"ok", "auth_required", "failed"}:
                return browser_result

        fallback = (c.snippet or "").strip()
        if fallback:
            c.fetch_status = "snippet_fallback"
            c.fetch_error = last_error or "empty fetched body; using snippet"
            c.fetched_title = c.title
            c.fetched_content = fallback
            if last_error_kind:
                c.extra["fetch_error_kind"] = last_error_kind
            return c
        c.fetch_status = "failed"
        c.fetch_error = last_error or "empty fetched body"
        if last_error_kind:
            c.extra["fetch_error_kind"] = last_error_kind
        return c

    max_chars = int(cfg.get("max_chars_per_doc", 5000))
    parsed_text = parsed_text[:max_chars]
    c.fetch_status = "ok"
    c.fetched_title = title or c.title
    c.fetched_content = parsed_text
    return c


def browser_fallback_allowed(error_kind: str | None, cfg: dict) -> bool:
    if not cfg.get("browser_fallback_enabled", True):
        return False
    kinds = cfg.get("browser_fallback_error_kinds", sorted(BROWSER_FALLBACK_ERROR_KINDS))
    if not isinstance(kinds, list):
        kinds = sorted(BROWSER_FALLBACK_ERROR_KINDS)
    return bool(error_kind and error_kind in {str(kind) for kind in kinds})


def fetch_with_browser_fallback(c: Candidate, cfg: dict, direct_error: str, error_kind: str | None) -> Candidate:
    script_value = str(cfg.get("browser_fallback_script") or "").strip()
    script = resolve_browser_fallback_script(script_value)
    c.extra["direct_fetch_error"] = direct_error
    if error_kind:
        c.extra["fetch_error_kind"] = error_kind
    if not script_value or not script.exists():
        c.extra["browser_fallback"] = {"ok": False, "status": "missing_script", "script": script_value}
        return c

    max_chars = int(cfg.get("max_chars_per_doc", 5000))
    timeout = int(cfg.get("browser_fallback_timeout_sec", 25))
    command_timeout = int(cfg.get("browser_fallback_command_timeout_sec", 30))
    cmd = [
        sys.executable,
        str(script),
        c.url,
        "--timeout",
        str(timeout),
        "--command-timeout",
        str(command_timeout),
        "--max-text-chars",
        str(int(cfg.get("browser_fallback_max_text_chars", max(max_chars * 3, 12000)))),
        "--max-clean-chars",
        str(max_chars),
    ]
    profile = str(cfg.get("browser_fallback_profile") or "").strip()
    if profile:
        cmd.extend(["--profile", profile])
    if cfg.get("browser_fallback_keep_tab", False):
        cmd.append("--keep-tab")
    if cfg.get("browser_fallback_headless", False):
        cmd.append("--headless")

    try:
        proc = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=timeout + command_timeout + 15,
            check=False,
        )
    except subprocess.TimeoutExpired:
        c.extra["browser_fallback"] = {"ok": False, "status": "timeout"}
        return c

    try:
        payload = json.loads(proc.stdout)
    except Exception:
        c.extra["browser_fallback"] = {
            "ok": False,
            "status": "non_json",
            "error": (proc.stderr or proc.stdout or "browser_fetch returned non-JSON")[:500],
        }
        return c

    c.extra["browser_fallback"] = {
        k: payload.get(k)
        for k in ("ok", "status", "message", "final_url", "raw_text_len", "cleaned_text_len", "timing")
        if k in payload
    }
    status = str(payload.get("status") or "")
    if proc.returncode == 0 and payload.get("ok"):
        content = str(payload.get("cleaned_text") or "").strip()
        if content:
            c.fetch_status = "ok"
            c.fetch_error = None
            c.fetched_title = payload.get("title") or c.title
            c.fetched_content = content[:max_chars]
            if payload.get("final_url"):
                c.extra["resolved_url"] = payload.get("final_url")
            c.extra["browser_fallback"]["used"] = True
            return c

    if status == "verification_needed" or proc.returncode == 30:
        c.fetch_status = "auth_required"
        c.fetch_error = str(payload.get("message") or "browser fetch requires manual authentication or verification")[:500]
        c.fetched_title = payload.get("title") or c.title
        return c

    if proc.returncode not in (0, 10):
        c.fetch_status = "failed"
        c.fetch_error = str(payload.get("error") or payload.get("message") or f"browser fallback exited {proc.returncode}")[:500]
        return c

    return c


def is_sensitive_fetch(c: Candidate, cfg: dict) -> bool:
    return bool(provider_capabilities(c, cfg).get("sensitive_fetch"))


def provider_capabilities(c: Candidate, cfg: dict) -> dict:
    all_caps = cfg.get("provider_capabilities") if isinstance(cfg.get("provider_capabilities"), dict) else {}
    caps = all_caps.get(c.provider, {})
    return caps if isinstance(caps, dict) else {}


def provider_source_path(c: Candidate, cfg: dict) -> str | None:
    paths = cfg.get("provider_source_paths") if isinstance(cfg.get("provider_source_paths"), dict) else {}
    value = paths.get(c.provider)
    return str(value) if value else None


def external_fetch_allowed(c: Candidate, cfg: dict) -> bool:
    caps = provider_capabilities(c, cfg)
    if not caps:
        c.extra["capability_error"] = "missing provider capabilities"
        return False
    if "external_fetch" not in caps:
        c.extra["capability_error"] = "missing capabilities.external_fetch"
        return False
    return bool(caps.get("external_fetch"))


def materialize_inline_content(c: Candidate, cfg: dict) -> bool:
    caps = provider_capabilities(c, cfg)
    if not caps.get("inline_content_for_analysis"):
        return False
    text = (c.fetched_content or c.snippet or "").strip()
    if not text:
        c.fetch_status = "inline_content_missing"
        c.fetch_error = "provider declared inline content but candidate has no snippet"
        return True
    c.fetch_status = "inline_content"
    c.fetched_title = c.fetched_title or c.title
    c.fetched_content = text
    c.extra["inline_content_for_analysis"] = True
    if caps.get("result_kind"):
        c.extra["provider_result_kind"] = caps.get("result_kind")
    if caps.get("content_granularity"):
        c.extra["content_granularity"] = caps.get("content_granularity")
    return True


def materialize_native_content(c: Candidate, cfg: dict) -> bool:
    caps = provider_capabilities(c, cfg)
    native = caps.get("native_fetch")
    if not isinstance(native, dict):
        return False
    entrypoint = native.get("entrypoint")
    source_path = provider_source_path(c, cfg)
    if not entrypoint or not source_path:
        c.extra["native_fetch_error"] = "native_fetch entrypoint or source path missing"
        return False

    payload = {
        "candidate": c.to_dict(),
        "capabilities": caps,
        "fetcher": {
            "max_chars_per_doc": int(native.get("max_chars_per_doc") or cfg.get("max_chars_per_doc", 5000)),
        },
    }
    try:
        proc = subprocess.run(
            shlex.split(str(entrypoint)),
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            cwd=source_path,
            timeout=int(native.get("timeout_sec") or cfg.get("timeout_sec", 20)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        c.extra["native_fetch_error"] = "native fetch timeout"
        return False

    if proc.returncode != 0:
        c.extra["native_fetch_error"] = (proc.stderr or proc.stdout or f"native fetch exited {proc.returncode}")[:500]
        return False
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        c.extra["native_fetch_error"] = "native fetch returned non-JSON"
        return False
    content = str(result.get("fetched_content") or "").strip()
    if not content:
        c.extra["native_fetch_error"] = str(result.get("fetch_error") or "native fetch returned empty content")[:500]
        return False
    c.fetch_status = "ok"
    c.fetched_title = result.get("fetched_title") or c.title
    c.fetched_content = content
    c.extra["native_fetch"] = {k: result.get(k) for k in ("provider", "content_chars", "window_start_line", "window_lines") if k in result}
    return True


def apply_inline_content(candidates: list[Candidate], cfg: dict) -> list[Candidate]:
    for c in candidates:
        materialize_inline_content(c, cfg)
    return candidates


def fetch_top(candidates: list[Candidate], cfg: dict, enabled: bool = True) -> list[Candidate]:
    if not enabled or not cfg.get("enabled", True):
        for c in candidates:
            c.fetch_status = "disabled"
        return candidates

    results: list[Candidate | None] = [None] * len(candidates)
    fetchable: list[tuple[int, Candidate]] = []
    for i, c in enumerate(candidates):
        if materialize_native_content(c, cfg):
            results[i] = c
        elif materialize_inline_content(c, cfg):
            results[i] = c
        elif external_fetch_allowed(c, cfg):
            fetchable.append((i, c))
        else:
            c.fetch_status = "external_fetch_disabled"
            c.fetch_error = "provider does not allow external body fetch"
            results[i] = c
    normal_items = [(i, c) for i, c in fetchable if not is_sensitive_fetch(c, cfg)]
    sensitive_items = [(i, c) for i, c in fetchable if is_sensitive_fetch(c, cfg)]

    def run_group(items: list[tuple[int, Candidate]], max_workers: int) -> None:
        if not items:
            return
        workers = max(1, min(max_workers, len(items)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_index = {pool.submit(fetch_one, c, cfg): i for i, c in items}
            for future in as_completed(future_to_index):
                i = future_to_index[future]
                try:
                    results[i] = future.result()
                except Exception as exc:
                    c = candidates[i]
                    c.fetch_status = "failed"
                    c.fetch_error = str(exc)[:500]
                    results[i] = c

    normal_workers = int(cfg.get("normal_concurrency", cfg.get("concurrency", 4)))
    sensitive_workers = int(cfg.get("sensitive_concurrency", 2))
    with ThreadPoolExecutor(max_workers=2) as groups:
        futures = [
            groups.submit(run_group, normal_items, normal_workers),
            groups.submit(run_group, sensitive_items, sensitive_workers),
        ]
        for future in as_completed(futures):
            future.result()

    return [r if r is not None else c for r, c in zip(results, candidates)]
