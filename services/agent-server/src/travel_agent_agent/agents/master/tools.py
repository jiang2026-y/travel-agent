# 本文件定义 MasterAgent 的两个内置只读工具和工具调用结果模型。
# 定义 InfoQueryTools、UserInteractionTools，禁止在工具层执行外部写操作。
from __future__ import annotations

from dataclasses import dataclass

from langgraph.types import interrupt


@dataclass(frozen=True, slots=True)
class UserQuestion:
    """保存前端可渲染的主动提问信息。"""

    ui_type: str
    question: str
    options: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()


class InfoQueryTools:
    """提供通用只读信息查询入口，首期不连接任何写能力。"""

    async def query(self, question: str) -> str:
        """返回待接入 Skill 的安全只读占位结果。"""
        if not question.strip():
            return "查询内容不能为空。"
        return "通用信息查询能力已登记，等待只读 Skill 配置。"


class UserInteractionTools:
    """提供结构化主动提问入口，由上层转换为 LangGraph interrupt。"""

    async def ask_user(
        self,
        question: str,
        ui_type: str = "text",
        options: tuple[str, ...] = (),
        fields: tuple[str, ...] = (),
    ) -> UserQuestion:
        """通过 LangGraph 显式中断等待用户答案，不替用户做选择。"""
        if not question.strip():
            raise ValueError("question_required")
        answer = interrupt(
            {
                "kind": "clarification",
                "ui_type": ui_type,
                "question": question.strip(),
                "options": list(options),
                "fields": list(fields),
            }
        )
        if isinstance(answer, dict) and isinstance(answer.get("message"), str):
            return UserQuestion(ui_type, answer["message"].strip(), options, fields)
        if isinstance(answer, str):
            return UserQuestion(ui_type, answer.strip(), options, fields)
        raise ValueError("clarification_response_invalid")
