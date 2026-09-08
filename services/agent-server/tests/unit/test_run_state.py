# 本文件验证 Agent Run 状态机。
# 定义可恢复迁移、终态拒绝与不可变版本递增测试函数。
import pytest

from travel_agent_agent.orchestration.state import RunState
from travel_agent_agent.orchestration.status import RunStatus, can_transition


def test_run_state_allows_clarification_resume_and_completion() -> None:
    """运行中的 Run 可以澄清、恢复并完成，且每次迁移增加版本。"""
    state = RunState("run_001", "thread_001", RunStatus.RUNNING)

    clarifying = state.transition(RunStatus.CLARIFYING)
    resumed = clarifying.transition(RunStatus.RUNNING)
    completed = resumed.transition(RunStatus.COMPLETED)

    assert state.version == 1
    assert completed.status is RunStatus.COMPLETED
    assert completed.version == 4


def test_terminal_run_cannot_resume_or_transition() -> None:
    """终态 Run 不得被恢复，防止中断续跑重放已完成动作。"""
    assert not can_transition(RunStatus.COMPLETED, RunStatus.RUNNING)

    with pytest.raises(ValueError, match="invalid_run_transition"):
        RunState("run_001", "thread_001", RunStatus.COMPLETED).transition(RunStatus.RUNNING)
