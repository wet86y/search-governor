from __future__ import annotations
import re
from .models import Candidate


def _compile_patterns(patterns: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


_INLINE_NOISE_PATTERNS = [
    re.compile(r"\bSearch syntax tips Provide feedback Saved searches\b", re.IGNORECASE),
    re.compile(r"\bSign in Sign up\b", re.IGNORECASE),
    re.compile(r"\bLog in Sign up\b", re.IGNORECASE),
    re.compile(r"\bYou signed (?:in|out) (?:with|in) another tab or window\. Reload to refresh your session\.", re.IGNORECASE),
    re.compile(r"\bYou switched accounts on another tab or window\. Reload to refresh your session\.", re.IGNORECASE),
    re.compile(r"\bDismiss alert \{\{ message \}\}", re.IGNORECASE),
    re.compile(r"\bUh oh! There was an error while loading\. Please reload this page\s*\.", re.IGNORECASE),
]


def _normalize_line(line: str) -> str:
    line = re.sub(r"\s+", " ", line).strip()
    line = re.sub(r"^[#>*\-\u2022]+\s*", "", line).strip()
    for pattern in _INLINE_NOISE_PATTERNS:
        line = pattern.sub(" ", line)
    line = re.sub(r"\s+", " ", line).strip()
    return line


def _looks_like_short_nav(line: str, min_line_chars: int) -> bool:
    if len(line) >= min_line_chars:
        return False
    if re.search(r"[。！？.!?，,；;：:]", line):
        return False
    if re.search(r"\d", line):
        return False
    words = re.findall(r"[A-Za-z0-9_\-]+|[\u4e00-\u9fff]", line)
    return len(words) <= 4


def _looks_like_menu_line(line: str) -> bool:
    if len(line) > 220:
        return False
    if re.search(r"[。！？!?；;.,，、：:]", line):
        return False
    if line.count(" ") < 5 and "展开" not in line:
        return False
    tokens = [x for x in re.split(r"\s+", line) if x]
    min_tokens = 5 if "展开" in line else 8
    if len(tokens) < min_tokens:
        return False
    short_tokens = sum(1 for token in tokens if len(token) <= 8)
    return short_tokens / max(len(tokens), 1) > 0.75


def _looks_like_site_chrome(line: str) -> bool:
    lower = line.casefold()
    if line.count("✓") >= 5:
        return True
    github_terms = (
        "navigation menu toggle navigation",
        "appearance settings",
        "github copilot write better code",
        "security and quality",
        "footer navigation",
    )
    if sum(1 for term in github_terms if term in lower) >= 2:
        return True
    docs_terms = ("search... k", "copy page", "view as markdown", "github releases discord menu")
    if sum(1 for term in docs_terms if term in lower) >= 2:
        return True
    cjk_chrome_terms = ("登录社区云", "去全站搜索看看", "邀请您加入社区", "查看 \"\" 全部搜索结果", "关注阿里云公众号")
    if any(term in line for term in cjk_chrome_terms):
        return True
    promo_terms = ("sign-up now", "start free trial", "user dashboard", "new to x?", "sign up now to get your own")
    if any(term in lower for term in promo_terms):
        return True
    return False


def clean_text(text: str, cfg: dict) -> tuple[str, dict]:
    original_chars = len(text or "")
    original_lines = (text or "").splitlines()
    patterns = _compile_patterns(cfg.get("noise_patterns", []))
    stop_patterns = _compile_patterns(cfg.get("stop_after_patterns", []))
    min_line_chars = int(cfg.get("min_line_chars", 12))
    drop_duplicate_lines = bool(cfg.get("drop_duplicate_lines", True))
    max_chars = int(cfg.get("max_chars_per_doc", 2600))

    cleaned_lines: list[str] = []
    seen: set[str] = set()
    dropped_noise = 0
    dropped_short = 0
    dropped_duplicate = 0
    stopped_at_tail = False

    for raw_line in original_lines:
        line = _normalize_line(raw_line)
        if not line:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue
        if any(p.search(line) for p in stop_patterns):
            dropped_noise += 1
            stopped_at_tail = True
            break
        if any(p.search(line) for p in patterns):
            dropped_noise += 1
            continue
        if _looks_like_site_chrome(line):
            dropped_noise += 1
            continue
        if _looks_like_menu_line(line):
            dropped_noise += 1
            continue
        if _looks_like_short_nav(line, min_line_chars):
            dropped_short += 1
            continue
        key = line.casefold()
        if drop_duplicate_lines and key in seen:
            dropped_duplicate += 1
            continue
        seen.add(key)
        cleaned_lines.append(line)

    cleaned = "\n".join(cleaned_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip()

    report = {
        "original_chars": original_chars,
        "cleaned_chars": len(cleaned),
        "original_lines": len(original_lines),
        "cleaned_lines": len([x for x in cleaned.splitlines() if x.strip()]),
        "dropped_noise_lines": dropped_noise,
        "dropped_short_lines": dropped_short,
        "dropped_duplicate_lines": dropped_duplicate,
        "stopped_at_tail": stopped_at_tail,
        "truncated": len(cleaned) < original_chars and len(cleaned) >= max_chars,
    }
    return cleaned, report


def clean_top_content(candidates: list[Candidate], cfg: dict) -> tuple[list[Candidate], dict]:
    if not cfg.get("enabled", True):
        return candidates, {"enabled": False}

    reports = []
    for c in candidates:
        if not c.fetched_content:
            continue
        cleaned, report = clean_text(c.fetched_content, cfg)
        c.fetched_content = cleaned
        c.extra["content_cleanup"] = report
        reports.append(report)

    return candidates, {
        "enabled": True,
        "processed": len(reports),
        "original_chars": sum(r["original_chars"] for r in reports),
        "cleaned_chars": sum(r["cleaned_chars"] for r in reports),
        "dropped_noise_lines": sum(r["dropped_noise_lines"] for r in reports),
        "dropped_short_lines": sum(r["dropped_short_lines"] for r in reports),
        "dropped_duplicate_lines": sum(r["dropped_duplicate_lines"] for r in reports),
    }
