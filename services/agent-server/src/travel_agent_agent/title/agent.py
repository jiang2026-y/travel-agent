# 文件职责：使用裸 qwen3.6-flash 模型根据用户问题与意图结果生成会话标题。
# 定义 TitleGenerationError、ConversationTitleAgent 与 clean_title。
from __future__ import annotations

from typing import Any

from travel_agent_sensitive_masker import SensitiveMasker

from travel_agent_agent.core.prompt_loader import load_prompt

MAX_TITLE_LENGTH = 24
MAX_QUESTION_LENGTH = 2000


class TitleGenerationError(RuntimeError):
    """表示标题模型调用或结果清洗失败。"""


class ConversationTitleAgent:
    """封装不带工具和 ReAct 循环的单次会话标题生成调用。"""

    def __init__(self, model: Any) -> None:
        """保存裸聊天模型与统一脱敏器。"""
        self.model = model
        self.masker = SensitiveMasker()

    async def generate(self, user_question: str, intent_result_json: str = "") -> str | None:
        """生成不超过 24 字的中文标题；失败时返回 None 而不影响主流程。"""
        if not user_question.strip():
            return None
        prompt = (
            f"用户问题：{self.masker.mask_text(user_question)[:MAX_QUESTION_LENGTH]}\n"
            f"意图识别结果：{self.masker.mask_text(intent_result_json)[:MAX_QUESTION_LENGTH]}\n"
            "请生成标题："
        )
        try:
            response = await self.model.ainvoke(
                [
                    {
                        "role": "system",
                        "content": load_prompt("prompts/conversation-title-agent-system.md"),
                    },
                    {"role": "user", "content": prompt},
                ]
            )
        except Exception as error:
            raise TitleGenerationError("title_model_failed") from error
        content = getattr(response, "content", response)
        if not isinstance(content, str):
            raise TitleGenerationError("title_content_invalid")
        return clean_title(content)


def clean_title(text: str | None) -> str | None:
    """去掉引号、序号与 Markdown 包裹，并截断到允许的标题长度。"""
    if not isinstance(text, str):
        return None
    title = text.strip().strip("`").strip()
    for line in title.splitlines():
        candidate = line.strip().lstrip("#").strip().strip('"').strip("'").strip()
        if candidate:
            title = candidate
            break
    title = title.strip('"').strip("'").strip("`").strip()
    if not title:
        return None
    return title[:MAX_TITLE_LENGTH]
