# 本文件装配 API Server 的最小安全 HTTP 应用。
# 定义 create_app，用于注册安全配置、CORS、关联审计与错误信封。
# 定义 health_check，用于报告服务状态；定义 app，作为 Uvicorn 启动入口。
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from travel_agent_api.api.errors import register_error_handlers
from travel_agent_api.api.middleware import install_observability_middleware
from travel_agent_api.api.routes import admin, auth, conversations, internal_travel
from travel_agent_api.application.audit_service import InMemoryAuditService
from travel_agent_api.application.auth_service import AuthService
from travel_agent_api.application.travel_order_service import TravelOrderPersistenceService
from travel_agent_api.application.user_profile_service import UserProfileService
from travel_agent_api.core.encryption import DataEncryptionService
from travel_agent_api.core.settings import Settings
from travel_agent_api.infrastructure.agent_client import AgentClient
from travel_agent_api.persistence.database import (
    create_async_engine_from_settings,
    create_session_factory,
)
from travel_agent_api.persistence.services import (
    DatabaseUserDirectory,
    PostgresAuditService,
    PostgresConversationService,
)


def create_app(settings: Settings | None = None) -> FastAPI:
    """创建带 Cookie/CSRF/CORS 配置、关联审计和安全错误信封的 API 应用。"""
    application_settings = settings or Settings.from_environment()
    application = FastAPI(title="Travel Agent API Server", version="0.1.0")
    application.state.settings = application_settings
    application.state.agent_client = AgentClient(
        application_settings.agent_server_base_url,
        application_settings.api_agent_token_file,
    )
    if application_settings.persistence_enabled:
        database_engine = create_async_engine_from_settings(application_settings)
        session_factory = create_session_factory(database_engine)
        application.state.database_engine = database_engine
        encryption = DataEncryptionService.from_secret_file(
            application_settings.data_encryption_key_file
        )
        application.state.auth_service = AuthService.create_persistent_service(
            users=DatabaseUserDirectory(session_factory),
            session_ttl_seconds=application_settings.session_ttl_seconds,
            login_failure_limit=application_settings.login_failure_limit,
            login_failure_window_seconds=application_settings.login_failure_window_seconds,
        )
        application.state.audit_service = PostgresAuditService(session_factory)
        application.state.conversation_service = PostgresConversationService(
            session_factory, encryption
        )
        application.state.travel_order_service = TravelOrderPersistenceService(session_factory)
        application.state.user_profile_service = UserProfileService(session_factory, encryption)
    else:
        application.state.auth_service = AuthService.create_development_service(
            session_ttl_seconds=application_settings.session_ttl_seconds,
            login_failure_limit=application_settings.login_failure_limit,
            login_failure_window_seconds=application_settings.login_failure_window_seconds,
        )
        application.state.audit_service = InMemoryAuditService()
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(application_settings.cors_allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            application_settings.csrf_header_name,
            "X-Trace-Id",
            "X-Request-Id",
            "X-Run-Id",
            "X-Thread-Id",
        ],
    )
    install_observability_middleware(application)
    register_error_handlers(application)
    application.include_router(auth.router)
    application.include_router(admin.router)
    application.include_router(conversations.router)
    application.include_router(internal_travel.router)

    @application.on_event("shutdown")
    async def close_database_engine() -> None:
        """在启用持久化时释放异步连接池，避免开发重载残留连接。"""
        database_engine = getattr(application.state, "database_engine", None)
        if database_engine is not None:
            await database_engine.dispose()

    @application.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        """返回 API Server 健康状态，不探测或连接未配置的真实外部服务。"""
        return {
            "service": "api-server",
            "status": "ok",
            "external_capabilities": "not_configured_policy_denied",
        }

    return application


app = create_app()
