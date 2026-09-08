# 本文件实现 L0 的强连词多意图守卫。
# 定义 StrongConjunctionGuard，用于按最小句长、连词位置和强连词清单判定是否直接进入 L3。
from __future__ import annotations

import re


class StrongConjunctionGuard:
    """按已确认阈值识别具有明显串行动作的输入。"""

    minimum_length = 10
    minimum_conjunction_offset = 4
    strong_conjunctions = (
        "然后",
        "接着",
        "顺便",
        "顺带",
        "以及",
        "并且",
        "另外",
        "同时",
        "完了再",
        "之后再",
        "再帮我",
        "再给我",
        "外加",
    )

    def __init__(self) -> None:
        """预编译强连词模式，避免每次请求重复构造正则。"""
        self._pattern = re.compile("|".join(map(re.escape, self.strong_conjunctions)))

    def is_obvious_multi_intent(self, text: str | None) -> bool:
        """仅在句长和连词位置同时满足阈值时返回真，避免口语误判。"""
        if text is None or len(text) < self.minimum_length:
            return False
        matched = self._pattern.search(text)
        return matched is not None and matched.start() >= self.minimum_conjunction_offset
