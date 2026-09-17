# 本文件定义 MasterAgent 的内置人机交互工具和交互载荷模型。
# 定义 UserQuestion、normalize_ui_fields 与 UserInteractionTools，禁止在工具层执行外部写操作。
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.types import interrupt

SUPPORTED_UI_TYPES = frozenset(
    {"text", "select", "multi_select", "confirm", "form", "date", "number"}
)


@dataclass(frozen=True, slots=True)
class UserQuestion:
    """保存本次主动提问的前端渲染信息，不含用户答案。"""

    ui_type: str
    question: str
    options: tuple[str, ...] = ()
    fields: tuple[dict[str, Any], ...] = ()
    default_value: str | None = None
    allow_other: bool = False

    def as_interrupt_payload(self) -> dict[str, Any]:
        """转换为 LangGraph 显式中断载荷，供 API 层落库为待交互。"""
        return {
            "kind": "clarification",
            "ui_type": self.ui_type,
            "question": self.question,
            "options": list(self.options),
            "fields": [dict(item) for item in self.fields],
            "default_value": self.default_value,
            "allow_other": self.allow_other,
        }


class UserInteractionTools:
    """提供结构化主动提问入口，由上层转换为 LangGraph interrupt。"""

    async def ask_user(
        self,
        question: str,
        ui_type: str = "text",
        options: tuple[str, ...] = (),
        fields: tuple[dict[str, Any], ...] = (),
        default_value: str | None = None,
        allow_other: bool = False,
    ) -> str:
        """通过 LangGraph 显式中断等待用户答案，并只返回用户提供的文本。"""
        if not question.strip():
            raise ValueError("question_required")
        normalized_ui_type = ui_type if ui_type in SUPPORTED_UI_TYPES else "text"
        payload = UserQuestion(
            ui_type=normalized_ui_type,
            question=question.strip(),
            options=normalize_ui_options(options),
            fields=normalize_ui_fields(fields),
            default_value=default_value if isinstance(default_value, str) else None,
            allow_other=bool(allow_other),
        )
        answer = interrupt(payload.as_interrupt_payload())
        if isinstance(answer, dict) and isinstance(answer.get("message"), str):
            return f"用户补充信息：{str(answer['message']).strip()}"
        if isinstance(answer, str):
            return f"用户补充信息：{answer.strip()}"
        raise ValueError("clarification_response_invalid")


def normalize_ui_options(options: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    """清洗单选与多选选项，去掉空值并保持模型给定顺序。"""
    if not options:
        return ()
    normalized = [item.strip() for item in options if isinstance(item, str) and item.strip()]
    return tuple(dict.fromkeys(normalized))


def normalize_ui_fields(
    fields: tuple[dict[str, Any], ...] | list[dict[str, Any]] | None,
) -> tuple[dict[str, Any], ...]:
    """清洗表单字段定义，只保留带 name 的对象并剔除嵌套非字典值。"""
    if not fields:
        return ()
    normalized: list[dict[str, Any]] = []
    for item in fields:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        normalized.append({str(key): value for key, value in item.items()})
    return tuple(normalized)
