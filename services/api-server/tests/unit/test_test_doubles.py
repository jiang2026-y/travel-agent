# 本文件验证 API Server 内存测试替身。
# 定义用户、会话和 Run 存储替身的隔离测试函数。
from travel_agent_api.infrastructure.test_doubles import (
    InMemoryRunStore,
    InMemorySessionStore,
    InMemoryUserStore,
    PreconfiguredUser,
)


def test_api_memory_test_doubles_isolate_users_sessions_and_runs() -> None:
    """内存替身应保存测试数据且不要求数据库连接。"""
    users = InMemoryUserStore()
    sessions = InMemorySessionStore()
    runs = InMemoryRunStore()
    user = PreconfiguredUser("user_001", "travel.user", "user")

    users.add(user)
    sessions.bind("session_001", user.user_id)
    runs.save_status("run_001", "running")

    assert users.get("user_001") == user
    assert sessions.get_user_id("session_001") == "user_001"
    assert runs.get_status("run_001") == "running"
    sessions.invalidate("session_001")
    assert sessions.get_user_id("session_001") is None
