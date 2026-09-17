# 文件职责：在主答案完成后以后台任务调度 Agent 的问题推荐旁路。
# 定义 RecommendationDispatcher 和 schedule_recommendations，隔离推荐失败与主请求结果。
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from fastapi import BackgroundTasks

from travel_agent_api.core.correlation import CorrelationContext
from travel_agent_api.infrastructure.agent_client import AgentClient, InternalUserContext

_LOGGER = logging.getLogger("travel_agent_api.recommendation")


@dataclass(frozen=True, slots=True)
class RecommendationDispatcher:
    """封装 API 到 Agent 的推荐后台任务调度。"""

    agent_client: AgentClient

    def schedule(
        self,
        background_tasks: BackgroundTasks,
        *,
        user: InternalUserContext,
        correlation: CorrelationContext,
        conversation_id: str,
        thread_id: str,
        answer_version: str,
        run_status: str,
        user_question: str,
        assistant_reply: str,
        context_summary: str = "",
        has_pending_interaction: bool = False,
    ) -> None:
        """将推荐请求加入后台任务，不在主答案请求中等待模型。"""
        background_tasks.add_task(
            self.dispatch,
            user,
            correlation,
            conversation_id,
            thread_id,
            answer_version,
            run_status,
            user_question,
            assistant_reply,
            context_summary,
            has_pending_interaction,
        )

    async def dispatch(
        self,
        user: InternalUserContext,
        correlation: CorrelationContext,
        conversation_id: str,
        thread_id: str,
        answer_version: str,
        run_status: str,
        user_question: str,
        assistant_reply: str,
        context_summary: str = "",
        has_pending_interaction: bool = False,
    ) -> None:
        """立即执行旁路推荐任务，异常只记录 warning。"""
        await self._run(
            user,
            correlation,
            conversation_id,
            thread_id,
            answer_version,
            run_status,
            user_question,
            assistant_reply,
            context_summary,
            has_pending_interaction,
        )

    async def _run(
        self,
        user: InternalUserContext,
        correlation: CorrelationContext,
        conversation_id: str,
        thread_id: str,
        answer_version: str,
        run_status: str,
        user_question: str,
        assistant_reply: str,
        context_summary: str,
        has_pending_interaction: bool,
    ) -> None:
        """执行推荐内部调用；所有异常只记录 warning。"""
        try:
            await self.agent_client.generate_recommendations(
                user,
                correlation,
                conversation_id,
                thread_id,
                answer_version,
                run_status,
                user_question,
                assistant_reply,
                context_summary,
                has_pending_interaction,
            )
        except Exception as error:
            _LOGGER.warning(
                "recommendation_background_failed trace_id=%s request_id=%s "
                "run_id=%s error_type=%s",
                correlation.trace_id,
                correlation.request_id,
                correlation.run_id,
                type(error).__name__,
            )


def schedule_recommendations(
    background_tasks: BackgroundTasks,
    dispatcher: RecommendationDispatcher,
    **kwargs: Any,
) -> None:
    """提供主答案完成处可直接调用的推荐调度函数。"""
    dispatcher.schedule(background_tasks, **kwargs)
