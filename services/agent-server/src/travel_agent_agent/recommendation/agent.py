# 文件职责：使用裸 qwen3.6-flash 模型生成异步问题推荐。
# 定义 RecommendationError、QuestionRecommendationAgent 及推荐结果解析函数。
from __future__ import annotations

import json
from typing import Any

from langchain_core.language_models import BaseChatModel
from travel_agent_sensitive_masker import SensitiveMasker

from travel_agent_agent.core.json_utils import parse_model_json
from travel_agent_agent.core.prompt_loader import load_prompt
from travel_agent_agent.recommendation.signals import continuation_signals


class RecommendationError(RuntimeError):
    """表示推荐模型调用或结果校验失败。"""


class QuestionRecommendationAgent:
    """封装不带工具和 ReAct 循环的单次问题推荐模型调用。"""

    max_items = 4
    max_text_length = 2000

    def __init__(self, model: BaseChatModel) -> None:
        """保存裸聊天模型和统一脱敏器。"""
        self.model = model
        self.masker = SensitiveMasker()

    async def recommend(
        self,
        user_question: str,
        assistant_reply: str,
        context_summary: str = "",
    ) -> list[str]:
        """根据用户问题、助手回复和会话摘要生成最多四条中文推荐。"""
        if not user_question.strip() or not assistant_reply.strip():
            return []
        prompt = self._build_prompt(user_question, assistant_reply, context_summary)
        try:
            result = await self.model.ainvoke(
                [
                    {
                        "role": "system",
                        "content": load_prompt(
                            "prompts/question-recommendation-agent-system.md"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ]
            )
            content = getattr(result, "content", result)
            return parse_recommendation_questions(content)
        except RecommendationError:
            raise
        except Exception as error:
            raise RecommendationError("recommendation_model_failed") from error

    def _build_prompt(
        self, user_question: str, assistant_reply: str, context_summary: str
    ) -> str:
        """构造只包含脱敏对话和固定关键词的推荐输入。"""
        return (
            "用户问题：\n"
            f"{self.masker.mask_text(user_question)[:self.max_text_length]}\n\n"
            "助手回复：\n"
            f"{self.masker.mask_text(assistant_reply)[:5000]}\n\n"
            "当前会话脱敏摘要：\n"
            f"{self.masker.mask_text(context_summary)[:1000]}\n\n"
            "可被后端直接执行的快速操作关键词（点击后会跳过主智能体直接续跑当前子智能体）：\n"
            f"{continuation_signals.as_grouped_prompt()}\n\n"
            "请根据助手回复的当前阶段判断是否使用上述关键词："
            "如果助手在抛出澄清/选择题，优先把助手列出的具体选项作为推荐项，不要硬塞‘确定/确认’；"
            "如果助手在等待用户整体确认/修改/继续，再考虑从上述关键词中挑选。\n"
            "请根据以上对话生成接下来的推荐问题。"
        )


def parse_recommendation_questions(content: Any) -> list[str]:
    """解析模型 JSON，过滤空值并限制推荐数量和长度。"""
    if not isinstance(content, str):
        raise RecommendationError("recommendation_content_invalid")
    try:
        payload = parse_model_json(content)
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise RecommendationError("recommendation_json_invalid") from error
    questions = payload.get("questions") if isinstance(payload, dict) else None
    if not isinstance(questions, list):
        raise RecommendationError("recommendation_questions_invalid")
    result: list[str] = []
    for question in questions:
        if not isinstance(question, str):
            continue
        normalized = question.strip()
        if normalized and len(normalized) <= 500 and normalized not in result:
            result.append(normalized)
        if len(result) >= QuestionRecommendationAgent.max_items:
            break
    return result
