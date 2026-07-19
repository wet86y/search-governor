#!/usr/bin/env python3
"""Browser-based content fetcher for Search Governor.

Opens a URL in the OpenClaw browser, extracts page text, runs it through
the Search Governor content cleaner, and returns cleaned JSON.

Usage:
    python3 browser_fetch.py <url> [--query <query>] [--max-text-chars <n>]

Exit codes:
    0  — success, cleaned content in stdout JSON
    10 — page loaded but content is empty
    20 — browser/infrastructure error
    30 — verification/challenge page detected (needs user action)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

SG_APP_HOME = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SG_APP_HOME))
os.environ.setdefault("SG_APP_HOME", str(SG_APP_HOME))

from search_governor.config import load_all_configs
from search_governor.content_cleaner import clean_text


# --- Verification/challenge detection ---

VERIFICATION_PATTERNS = [
    # Baidu
    "百度安全验证",
    "baidu security verification",
    "请输入验证码",
    "安全验证",
    # Generic
    "请完成验证",
    "验证码",
    "captcha",
    "请完成安全验证",
    "checking your browser",
    "cf-browser-verification",
    "cloudflare",
    "just a moment",
    "请进行人机验证",
    "slider verification",
    "滑动验证",
]

AUTH_REQUIRED_PATTERNS = [
    "authentication required",
    "authorization required",
    "login required",
    "sign in to continue",
    "please sign in",
    "please log in",
    "需要登录",
    "登录后查看",
    "请先登录",
    "请登录后",
    "未授权访问",
    "unauthorized",
    "forbidden",
]

NAVIGATION_ERROR_PATTERNS = [
    "chrome-error://chromewebdata",
    "err_connection_refused",
    "err_connection_reset",
    "err_name_not_resolved",
    "err_timed_out",
    "err_internet_disconnected",
]


def resolve_openclaw_bin() -> str:
    configured = os.environ.get("OPENCLAW_BIN", "").strip()
    candidates = [configured, str(Path.home() / ".npm-global" / "bin" / "openclaw"), shutil.which("openclaw") or ""]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("OpenClaw executable not found; set OPENCLAW_BIN")


def browser_request(
    method: str,
    path: str,
    *,
    profile: str,
    timeout: int,
    body: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
) -> dict[str, Any]:
    helper = Path(__file__).with_name("browser_gateway_rpc.mjs")
    request_query = {"profile": profile}
    if query:
        request_query.update(query)
    request = {
        "method": method,
        "path": path,
        "query": {key: str(value).lower() if isinstance(value, bool) else str(value) for key, value in request_query.items()},
        "body": body,
        "timeoutMs": timeout * 1000,
    }
    cmd = ["node", str(helper), resolve_openclaw_bin()]
    proc = subprocess.run(
        cmd,
        input=json.dumps(request, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(detail or "OpenClaw browser gateway request failed")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"browser command returned non-JSON: {proc.stdout[:500]}") from exc


def evaluate_page(profile: str, timeout: int, max_text_chars: int, target_id: str | None = None) -> dict[str, Any]:
    fn = (
        "() => ({"
        "url: location.href,"
        "title: document.title,"
        "text: document.body ? document.body.innerText.slice(0, "
        f"{int(max_text_chars)}"
        ") : ''"
        "})"
    )
    body: dict[str, Any] = {"kind": "evaluate", "fn": fn, "timeoutMs": timeout * 1000}
    if target_id:
        body["targetId"] = target_id
    payload = browser_request("POST", "/act", profile=profile, timeout=timeout, body=body)
    result = payload.get("result")
    return result if isinstance(result, dict) else {}


def classify_page(url: str, text: str, title: str) -> tuple[str, str]:
    """Classify the loaded page.

    Returns (status, message) where status is one of:
        ok                  — page loaded with content
        verification_needed — captcha/challenge/auth page detected
        navigation_error    — browser could not load the page
        empty_content       — page loaded but no text
    """
    combined = f"{title} {text[:500]}".lower()
    url_lower = (url or "").lower()

    # Check for navigation errors
    for pattern in NAVIGATION_ERROR_PATTERNS:
        if pattern in combined or pattern in url_lower:
            return "navigation_error", f"Browser navigation error: {pattern}"

    # Check for verification/challenge pages
    for pattern in VERIFICATION_PATTERNS:
        if pattern.lower() in combined:
            return "verification_needed", f"Verification/challenge page detected: {pattern}"

    # Check for forced login/auth pages. Keep patterns specific to avoid
    # treating ordinary site navigation links as authentication failures.
    for pattern in AUTH_REQUIRED_PATTERNS:
        if pattern.lower() in combined:
            return "verification_needed", f"Authentication required page detected: {pattern}"

    # Check if content is meaningful
    text_stripped = (text or "").strip()
    if not text_stripped:
        return "empty_content", "Page loaded but body text is empty"

    # If text is very short and matches anti-bot patterns
    if len(text_stripped) < 100:
        for pattern in VERIFICATION_PATTERNS:
            if pattern.lower() in combined:
                return "verification_needed", f"Verification/challenge page detected: {pattern}"

    return "ok", "Page loaded successfully"


def fetch_with_browser(
    url: str,
    *,
    profile: str = "openclaw",
    timeout: float = 25.0,
    poll_interval: float = 2.0,
    command_timeout: int = 30,
    max_text_chars: int = 12000,
    keep_tab: bool = False,
    headless: bool = False,
) -> dict[str, Any]:
    """Fetch a URL via the OpenClaw browser and return page content."""

    # Start browser
    started_at = time.perf_counter()
    browser_request("POST", "/start", profile=profile, timeout=command_timeout, query={"headless": headless} if headless else None)
    browser_start_ms = int((time.perf_counter() - started_at) * 1000)

    # Open URL
    opened_at = time.perf_counter()
    opened = browser_request("POST", "/tabs/open", profile=profile, timeout=command_timeout, body={"url": url})
    target_id = opened.get("targetId")
    tab_ref = opened.get("suggestedTargetId") or opened.get("tabId") or target_id

    # Poll for page load
    last_page: dict[str, Any] = {}
    status = "empty_content"
    status_msg = "Timeout waiting for page content"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        page = evaluate_page(profile, command_timeout, max_text_chars, str(target_id) if target_id else None)
        last_page = page
        status, status_msg = classify_page(
            str(page.get("url") or ""),
            str(page.get("text") or ""),
            str(page.get("title") or ""),
        )
        # Break on any definitive status (not still loading)
        if status != "empty_content" or (page.get("text") and len(page.get("text", "")) > 200):
            break

    elapsed_ms = int((time.perf_counter() - opened_at) * 1000)
    final_url = str(last_page.get("url") or opened.get("url") or "")
    text = str(last_page.get("text") or "")
    title = str(last_page.get("title") or opened.get("title") or "")

    result = {
        "ok": status == "ok",
        "status": status,
        "message": status_msg,
        "input_url": url,
        "final_url": final_url,
        "title": title,
        "text": text,
        "text_len": len(text),
        "tab": tab_ref,
        "target_id": target_id,
        "timing": {
            "browser_start_ms": browser_start_ms,
            "navigation_ms": elapsed_ms,
        },
    }

    # Close tab unless requested to keep
    if tab_ref and not keep_tab:
        try:
            browser_request("DELETE", f"/tabs/{quote(str(tab_ref), safe='')}", profile=profile, timeout=command_timeout)
        except Exception as exc:
            result["close_error"] = str(exc)

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch a URL via OpenClaw browser and clean the content.")
    parser.add_argument("url", help="URL to fetch")
    parser.add_argument("--query", default="", help="Search query (for context, currently unused)")
    parser.add_argument("--profile", default="openclaw", help="OpenClaw browser profile")
    parser.add_argument("--timeout", type=float, default=25.0, help="Navigation wait seconds")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="Polling interval seconds")
    parser.add_argument("--command-timeout", type=int, default=30, help="Per browser command timeout")
    parser.add_argument("--max-text-chars", type=int, default=12000, help="Max body text chars from browser")
    parser.add_argument("--max-clean-chars", type=int, default=5000, help="Max chars after cleaning")
    parser.add_argument("--keep-tab", action="store_true", help="Leave browser tab open")
    parser.add_argument("--headless", action="store_true", help="Start browser in headless mode")
    parser.add_argument("--no-clean", action="store_true", help="Skip content cleaning")
    args = parser.parse_args()

    # Fetch via browser
    try:
        fetch_result = fetch_with_browser(
            args.url,
            profile=args.profile,
            timeout=args.timeout,
            poll_interval=args.poll_interval,
            command_timeout=args.command_timeout,
            max_text_chars=args.max_text_chars,
            keep_tab=args.keep_tab,
            headless=args.headless,
        )
    except Exception as exc:
        result = {
            "ok": False,
            "status": "browser_error",
            "input_url": args.url,
            "error": str(exc),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 20

    # Handle non-ok statuses
    if not fetch_result.get("ok"):
        print(json.dumps(fetch_result, ensure_ascii=False, indent=2))
        if fetch_result.get("status") == "verification_needed":
            return 30
        return 10

    # Clean content
    raw_text = fetch_result.get("text") or ""
    cleanup_report = None
    cleaned_text = raw_text

    if not args.no_clean and raw_text:
        try:
            cfg = load_all_configs()
            cleaner_cfg = cfg.get("content_cleaner", {})
            cleaner_cfg["max_chars_per_doc"] = args.max_clean_chars
            cleaned_text, cleanup_report = clean_text(raw_text, cleaner_cfg)
        except Exception as exc:
            fetch_result["clean_error"] = str(exc)

    result = {
        "ok": True,
        "status": "ok",
        "input_url": args.url,
        "final_url": fetch_result.get("final_url"),
        "title": fetch_result.get("title"),
        "raw_text_len": len(raw_text),
        "cleaned_text": cleaned_text,
        "cleaned_text_len": len(cleaned_text),
        "content_cleanup": cleanup_report,
        "timing": fetch_result.get("timing"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
