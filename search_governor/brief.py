from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class DeepBrief:
    point_question: str = ""
    goal: str = ""
    necessary_context: str = ""
    boundaries: str = ""
    output_use: str = ""
    must_answer: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def any(self) -> bool:
        return any((self.point_question, self.goal, self.necessary_context, self.boundaries, self.output_use, self.must_answer))

    def validate_for_deep(self) -> list[str]:
        missing: list[str] = []
        if not self.point_question.strip():
            missing.append("point_question")
        if not self.goal.strip():
            missing.append("goal")
        if not self.boundaries.strip():
            missing.append("boundaries")
        if not self.output_use.strip():
            missing.append("output_use")
        return missing


_SECTION_MAP = {
    "点问题": "point_question",
    "目标": "goal",
    "必要上下文": "necessary_context",
    "必答清单": "must_answer",
    "搜索边界": "boundaries",
    "输出用途": "output_use",
}


def _extract_sections(markdown: str) -> dict[str, str]:
    out: dict[str, str] = {}
    pattern = re.compile(r"^##\s+(.+?)\s*$", re.M)
    matches = list(pattern.finditer(markdown or ""))
    for i, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        key = _SECTION_MAP.get(title)
        if key:
            out[key] = markdown[start:end].strip()
    return out


def parse_brief_file(path: str | None) -> DeepBrief:
    if not path:
        return DeepBrief()
    brief_path = Path(path)
    if not brief_path.exists():
        raise FileNotFoundError(f"brief file not found: {path}")
    data = _extract_sections(brief_path.read_text(encoding="utf-8"))
    return DeepBrief(**{k: str(v or "").strip() for k, v in data.items()})


def brief_from_args(args) -> DeepBrief:
    brief = parse_brief_file(getattr(args, "brief_file", None))
    for arg_name, field_name in [
        ("point_question", "point_question"),
        ("goal", "goal"),
        ("necessary_context", "necessary_context"),
        ("must_answer", "must_answer"),
        ("boundaries", "boundaries"),
        ("output_use", "output_use"),
    ]:
        val = getattr(args, arg_name, None)
        if val:
            setattr(brief, field_name, val.strip())
    return brief


def _trim_text(text: str, max_chars: int) -> str:
    value = (text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip() + "..."


def _default_must_answer(brief: DeepBrief, query: str) -> str:
    point = brief.point_question.strip() or query
    return "\n".join(
        [
            f"1. 直接回答点问题：{_trim_text(point, 180)}",
            "2. 给出支撑结论的关键信源和证据。",
            "3. 列出风险、限制、冲突和仍缺失的信息。",
        ]
    )


def build_ranking_context(query: str, brief: DeepBrief | None, mode: str) -> str:
    if mode != "deep" or brief is None or not brief.any():
        return query

    must_answer = brief.must_answer.strip() or _default_must_answer(brief, query)
    parts = [
        "【排序任务卡】",
        f"本次检索目标：{_trim_text(brief.point_question or query, 240)}",
        f"使用目标：{_trim_text(brief.goal, 240)}",
        "",
        "优先选择能回答以下问题的材料：",
        _trim_text(must_answer, 600),
    ]

    if brief.necessary_context.strip():
        parts.extend(["", "必要上下文：", _trim_text(brief.necessary_context, 300)])
    if brief.boundaries.strip():
        parts.extend(["", "来源与边界要求：", _trim_text(brief.boundaries, 300)])
    if brief.output_use.strip():
        parts.extend(["", f"最终输出用途：{_trim_text(brief.output_use, 240)}"])

    parts.extend(
        [
            "",
            "排序要求：",
            "1. 优先选择能直接回答必答清单的材料。",
            "2. 优先选择包含具体事实、日期、版本、参数、案例、限制、风险或可执行结论的材料。",
            "3. 优先选择符合搜索边界和来源偏好的材料。",
            "4. 降低泛泛介绍、营销内容、重复转载、与上下文不匹配的材料。",
            "5. 如果材料只与关键词相关但不能支撑必答清单，应降权。",
        ]
    )
    return _trim_text("\n".join(parts), 1200)
