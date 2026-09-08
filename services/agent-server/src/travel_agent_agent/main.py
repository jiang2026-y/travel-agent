# 本文件装配 Agent Server 的最小安全 HTTP 应用。
# 定义 create_app，用于注册配置、关联审计与错误信封。
# 定义 health_check，用于报告服务状态；定义 app，作为 Uvicorn 启动入口。
from fastapi import FastAPI
from langgraph.checkpoint.redis import AsyncRedisSaver
from redis.asyncio import Redis

from travel_agent_agent.agents.intent_recognition import IntentRecognitionAgent
from travel_agent_agent.agents.master.agent import MasterAgent
from travel_agent_agent.agents.master.model import create_master_chat_model
from travel_agent_agent.api.errors import register_error_handlers
from travel_agent_agent.api.internal_commands import register_internal_command_routes
from travel_agent_agent.api.middleware import install_observability_middleware
from travel_agent_agent.core.settings import Settings
from travel_agent_agent.infrastructure.dashscope_client import create_dashscope_clients
from travel_agent_agent.infrastructure.itinerary_plan_store import (
    InMemoryItineraryPlanStore,
    ItineraryPlanStore,
)
from travel_agent_agent.intent.runtime import create_intent_runtime
from travel_agent_agent.orchestration.checkpoints import RedisRunCheckpointStore, RunCheckpointStore
from travel_agent_agent.orchestration.interactions import (
    InMemoryPendingInteractionStore,
    PendingInteractionStore,
    RedisPendingInteractionStore,
)


def create_app(
    settings: Settings | None = None,
    checkpoint_store: RunCheckpointStore | None = None,
    interaction_store: PendingInteractionStore | None = None,
    itinerary_plan_store: ItineraryPlanStore | InMemoryItineraryPlanStore | None = None,
) -> FastAPI:
    """创建带内部网关与真实只读配置、关联审计和安全错误信封的 Agent 应用。"""
    application_settings = settings or Settings.from_environment()
    application = FastAPI(title="Travel Agent Agent Server", version="0.1.0")
    application.state.settings = application_settings
    owns_checkpoint_store = checkpoint_store is None
    application.state.checkpoint_store = checkpoint_store or RedisRunCheckpointStore(
        client=Redis.from_url(application_settings.redis_url, decode_responses=True),
        ttl_seconds=application_settings.checkpoint_ttl_seconds,
    )
    if interaction_store is not None:
        application.state.interaction_store = interaction_store
    elif checkpoint_store is not None:
        application.state.interaction_store = InMemoryPendingInteractionStore({})
    else:
        application.state.interaction_store = RedisPendingInteractionStore(
            client=Redis.from_url(application_settings.redis_url, decode_responses=True),
            ttl_seconds=application_settings.hitl_token_ttl_seconds,
        )
    if itinerary_plan_store is not None:
        application.state.itinerary_plan_store = itinerary_plan_store
    elif checkpoint_store is not None:
        application.state.itinerary_plan_store = InMemoryItineraryPlanStore()
    else:
        try:
            application.state.itinerary_plan_store = ItineraryPlanStore.from_secret_file(
                Redis.from_url(application_settings.redis_url, decode_responses=True),
                application_settings.data_encryption_key_file,
            )
        except ValueError:
            # 未启用真实 Provider 的本地单元测试可使用内存替身；真实运行必须有 Secret。
            if application_settings.provider_call_enabled:
                raise
            application.state.itinerary_plan_store = InMemoryItineraryPlanStore()
    application.state.intent_agent = IntentRecognitionAgent()
    application.state.master_agent = None
    install_observability_middleware(application)
    register_error_handlers(application)
    register_internal_command_routes(application)

    @application.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        """返回 Agent Server 健康状态，不编译图或调用未配置的真实模型。"""
        return {
            "service": "agent-server",
            "status": "ok",
            "external_capabilities": (
                "dashscope_readonly_enabled"
                if application_settings.provider_call_enabled
                else "not_configured_policy_denied"
            ),
        }

    if application_settings.provider_call_enabled:

        @application.on_event("startup")
        async def initialize_real_intent_runtime() -> None:
            """启动时经 Tool Gateway 灌入 1024 维意图种子；失败则拒绝伪造 L2/L3 能力。"""
            embedding, l3, rewrite = create_dashscope_clients(application_settings)
            runtime = await create_intent_runtime(embedding, l3, rewrite)
            application.state.intent_agent = runtime.intent_agent
            langgraph_checkpointer = AsyncRedisSaver(redis_url=application_settings.redis_url)
            await langgraph_checkpointer.setup()
            application.state.langgraph_checkpointer = langgraph_checkpointer
            application.state.master_agent = MasterAgent.create_with_default_provider(
                create_master_chat_model(application_settings),
                checkpointer=langgraph_checkpointer,
                settings=application_settings,
            )

    if owns_checkpoint_store:

        @application.on_event("shutdown")
        async def close_checkpoint_store() -> None:
            """在服务停止时关闭自建 Redis 连接池，不关闭测试注入的替身。"""
            store = application.state.checkpoint_store
            await store.close()
            plan_store = application.state.itinerary_plan_store
            if isinstance(plan_store, ItineraryPlanStore):
                await plan_store.client.aclose()

    return application


app = create_app()
