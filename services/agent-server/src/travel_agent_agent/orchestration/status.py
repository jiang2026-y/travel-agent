# 本文件定义可恢复 Run 的状态枚举和迁移规则。
# 定义 RunStatus、can_transition 与 require_transition。
# 这些定义用于表达运行生命周期并校验状态迁移。
from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    """表示旅行助手 Run 的可见生命周期状态。"""

    CREATED = "created"
    RUNNING = "running"
    CLARIFYING = "clarifying"
    PROPOSED = "proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.FAILED}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.CLARIFYING,
            RunStatus.PROPOSED,
            RunStatus.AWAITING_APPROVAL,
            RunStatus.COMPLETED,
            RunStatus.CANCELLED,
            RunStatus.FAILED,
        }
    ),
    RunStatus.CLARIFYING: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.FAILED}),
    RunStatus.PROPOSED: frozenset(
        {RunStatus.RUNNING, RunStatus.AWAITING_APPROVAL, RunStatus.COMPLETED, RunStatus.CANCELLED}
    ),
    RunStatus.AWAITING_APPROVAL: frozenset(
        {RunStatus.RUNNING, RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED}
    ),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}


def can_transition(current: RunStatus, target: RunStatus) -> bool:
    """判断一次状态迁移是否符合恢复与终态规则。"""
    return target in _ALLOWED_TRANSITIONS[current]


def require_transition(current: RunStatus, target: RunStatus) -> None:
    """拒绝非法状态迁移，避免恢复流程重复执行终态 Run。"""
    if not can_transition(current, target):
        raise ValueError(f"invalid_run_transition:{current}:{target}")
