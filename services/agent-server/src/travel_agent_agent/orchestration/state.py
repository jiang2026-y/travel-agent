# 本文件定义 Agent 编排使用的最小可恢复 Run 状态对象。
# 定义 RunState，用于保存 Run、线程、状态和版本；定义 transition，用于创建状态迁移后的不可变对象。
from __future__ import annotations

from dataclasses import dataclass

from travel_agent_agent.orchestration.status import RunStatus, require_transition


@dataclass(frozen=True, slots=True)
class RunState:
    """保存不依赖数据库 Schema 的 Run 恢复状态。"""

    run_id: str
    thread_id: str
    status: RunStatus
    version: int = 1

    def transition(self, target: RunStatus) -> RunState:
        """校验迁移并返回版本递增的新状态，不原地修改检查点快照。"""
        require_transition(self.status, target)
        return RunState(
            run_id=self.run_id,
            thread_id=self.thread_id,
            status=target,
            version=self.version + 1,
        )
