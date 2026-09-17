# 文件职责：把长期记忆原文解析为结构化差旅偏好，供偏好设置页面回显。
# 定义 PreferenceParseAgent 与 sanitize_preferences：使用裸 qwen3.6-flash 单次调用，
# 只接受目录内的 key 与选项值，解析失败时降级为空结构而不抛出。
from __future__ import annotations

import json

from langchain_core.language_models import BaseChatModel
from travel_agent_sensitive_masker import SensitiveMasker

from travel_agent_agent.core.json_utils import parse_model_json

_SYSTEM_PROMPT = (
    "你是差旅偏好抽取器。用户会给出长期记忆中的自然语言偏好描述，"
    "以及允许使用的偏好项目录（key 与可选值）。\n"
    "只输出严格 JSON，格式为 {\"preferences\": {\"<key>\": [\"<取值>\"]}}。\n"
    "规则：\n"
    "1. 只能使用目录中给出的 key；只能使用该 key 的 options 中出现的取值，禁止臆造。\n"
    "2. 记忆中没有提到的项一律不要出现在结果里。\n"
    "3. 同一 key 可返回多个取值（数组）；无法确定时省略该项。\n"
    "4. 不输出任何解释、Markdown 或多余文本。"
)


class PreferenceParseAgent:
    """封装单次偏好解析调用，不参与 ReAct 循环也不调用工具。"""

    max_memory_chars = 4000

    def __init__(self, model: BaseChatModel) -> None:
        """保存裸聊天模型与统一脱敏器。"""
        self.model = model
        self.masker = SensitiveMasker()

    async def parse(
        self, memory_text: str, catalog: dict[str, list[str]]
    ) -> dict[str, list[str]]:
        """把记忆原文解析为目录内的结构化偏好；失败或无匹配时返回空字典。"""
        text = (memory_text or "").strip()
        if not text or not catalog:
            return {}
        payload = {
            "memory_text": self.masker.mask_text(text)[: self.max_memory_chars],
            "catalog": [{"key": key, "options": options} for key, options in catalog.items()],
        }
        try:
            response = await self.model.ainvoke(
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ]
            )
        except Exception:
            return {}
        content = getattr(response, "content", "")
        if not isinstance(content, str) or not content.strip():
            return {}
        try:
            parsed = parse_model_json(content)
        except Exception:
            return {}
        return sanitize_preferences(parsed, catalog)


def sanitize_preferences(
    parsed: object, catalog: dict[str, list[str]]
) -> dict[str, list[str]]:
    """只保留目录内合法的 key 与取值，丢弃任何模型臆造内容。"""
    if not isinstance(parsed, dict):
        return {}
    raw = parsed.get("preferences")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, list[str]] = {}
    for key, values in raw.items():
        allowed = catalog.get(str(key))
        if allowed is None:
            continue
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            continue
        kept = [str(item) for item in values if str(item) in allowed]
        if kept:
            result[str(key)] = kept
    return result
