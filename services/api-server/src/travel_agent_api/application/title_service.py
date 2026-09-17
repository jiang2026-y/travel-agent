# 文件职责：在主 Run 完成后以后台任务生成并落库会话标题。
# 定义 ConversationTitleDispatcher 与 schedule_conversation_title，隔离标题失败与主请求。
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from fastapi import BackgroundTasks

from travel_agent_api.core.correlation import CorrelationContext
from travel_agent_api.infrastructure.agent_client import AgentClient, InternalUserContext
from travel_agent_api.persistence.services import PostgresConversationService

_LOGGER = logging.getLogger("travel_agent_api.title")


@dataclass(frozen=True, slots=True)
class ConversationTitleDispatcher:
    """封装 API 到 Agent 的标题生成后台任务调度与标题落库。"""

    agent_client: AgentClient

    def schedule(
        self,
        background_tasks: BackgroundTasks,
        *,
        user: InternalUserContext,
        correlation: CorrelationContext,
        conversation_service: PostgresConversationService,
        conversation_id: str,
        thread_id: str,
        user_question: str,
        intent_result_json: str,
    ) -> None:
        """将标题生成加入后台任务，不在主答案请求中等待模型。"""
        background_tasks.add_task(
            self.dispatch,
            user,
            correlation,
            conversation_service,
            conversation_id,
            thread_id,
            user_question,
            intent_result_json,
        )

    async def dispatch(
        self,
        user: InternalUserContext,
        correlation: CorrelationContext,
        conversation_service: PostgresConversationService,
        conversation_id: str,
        thread_id: str,
        user_question: str,
        intent_result_json: str,
    ) -> None:
        """执行标题生成与落库；任何异常只记录 warning。"""
        try:
            title = await self.agent_client.generate_conversation_title(
                user,
                correlation,
                conversation_id,
                thread_id,
                user_question,
                intent_result_json,
            )
            if not title:
                return
            await conversation_service.update_title(user.user_id, conversation_id, title)
        except Exception as error:
            _LOGGER.warning(
                "conversation_title_failed trace_id=%s request_id=%s run_id=%s error_type=%s",
                correlation.trace_id,
                correlation.request_id,
                correlation.run_id,
                type(error).__name__,
            )


def schedule_conversation_title(
    background_tasks: BackgroundTasks,
    dispatcher: ConversationTitleDispatcher | None,
    **kwargs: Any,
) -> None:
    """提供主流程可直接调用的标题调度函数；未启用时静默跳过。"""
    if dispatcher is None:
        return
    dispatcher.schedule(background_tasks, **kwargs)
