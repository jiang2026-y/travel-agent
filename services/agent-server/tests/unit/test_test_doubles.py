# 本文件验证 Agent Server 内存测试替身。
# 定义检查点、模型、记忆、RAG 和 Skill 替身的异步测试函数。
import pytest

from travel_agent_agent.infrastructure.test_doubles import (
    FakeMemory,
    FakeModel,
    FakeRag,
    FakeSkill,
    InMemoryCheckpoint,
)


@pytest.mark.asyncio
async def test_agent_memory_test_doubles_do_not_call_external_services() -> None:
    """替身返回固定结果，并保留检查点副本以支持恢复测试。"""
    checkpoint = InMemoryCheckpoint()
    checkpoint.save("thread_001", {"status": "running"})
    loaded = checkpoint.load("thread_001")
    assert loaded == {"status": "running"}
    assert loaded is not None
    loaded["status"] = "changed"
    assert checkpoint.load("thread_001") == {"status": "running"}
    assert await FakeModel("result").generate("hidden") == "result"
    assert await FakeMemory(("memory",)).search("user", "hidden") == ["memory"]
    assert await FakeRag(("document",)).retrieve("hidden") == ["document"]
    assert await FakeSkill({"ok": True}).invoke("search", {}) == {"ok": True}
