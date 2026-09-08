# 本文件定义 Agent Server 仅供自动化测试使用的内存替身。
# 定义 InMemoryCheckpoint、FakeModel、FakeMemory、FakeRag 与 FakeSkill。
# 这些替身用于隔离检查点、模型、记忆、RAG 和工具。
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class InMemoryCheckpoint:
    """保存按线程隔离的测试检查点。"""

    states: dict[str, dict[str, object]] = field(default_factory=dict)

    def save(self, thread_id: str, state: dict[str, object]) -> None:
        """保存状态副本，避免测试调用方修改已保存值。"""
        self.states[thread_id] = dict(state)

    def load(self, thread_id: str) -> dict[str, object] | None:
        """读取状态副本；不存在时返回空值。"""
        state = self.states.get(thread_id)
        return dict(state) if state is not None else None


@dataclass(frozen=True, slots=True)
class FakeModel:
    """返回固定文本的模型替身，不发起任何网络调用。"""

    response: str = "fake-model-response"

    async def generate(self, prompt: str) -> str:
        """忽略提示词内容并返回固定结果，避免在测试日志保留原文。"""
        del prompt
        return self.response


@dataclass(frozen=True, slots=True)
class FakeMemory:
    """返回固定记忆片段的长期记忆替身。"""

    entries: tuple[str, ...] = ()

    async def search(self, user_id: str, query: str) -> list[str]:
        """忽略用户和查询原文并返回固定测试片段。"""
        del user_id, query
        return list(self.entries)


@dataclass(frozen=True, slots=True)
class FakeRag:
    """返回固定知识片段的 RAG 替身。"""

    documents: tuple[str, ...] = ()

    async def retrieve(self, query: str) -> list[str]:
        """忽略查询原文并返回固定知识片段。"""
        del query
        return list(self.documents)


@dataclass(frozen=True, slots=True)
class FakeSkill:
    """返回固定结果的 Skill 替身，不产生外部副作用。"""

    result: dict[str, object] = field(default_factory=dict)

    async def invoke(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        """忽略工具输入并返回结果副本。"""
        del name, arguments
        return dict(self.result)
