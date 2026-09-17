# 文件职责：导出问题推荐旁路及快速操作词表的公共接口。
# 本文件不定义业务逻辑，仅提供推荐模块的包入口。

"""问题推荐旁路模块。"""

from travel_agent_agent.recommendation.signals import (
    ContinuationSignals,
    continuation_signals,
)

__all__ = ["ContinuationSignals", "continuation_signals"]
