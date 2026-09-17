# 文件职责：验证用户级 API Key 内部接口的鉴权、状态查询与加密保存契约。
# 定义内部 Token 校验、未配置返回、保存与 provider 白名单测试。
from __future__ import annotations

from fastapi.testclient import TestClient

from travel_agent_api.core.settings import Settings
from travel_agent_api.main import create_app


class _FakeApiKeyService:
    """使用内存字典模拟密文读写，不访问数据库。"""

    def __init__(self) -> None:
        self.stored: dict[tuple[str, str], str] = {}
        self.revealed: list[tuple[str, str]] = []

    async def has_key(self, user_id: str, provider: str) -> bool:
        """判断内存中是否已保存密钥。"""
        return (user_id, provider) in self.stored

    async def reveal(self, user_id: str, provider: str) -> str | None:
        """返回内存中的明文密钥，并记录调用。"""
        self.revealed.append((user_id, provider))
        return self.stored.get((user_id, provider))

    async def save(self, user_id: str, provider: str, api_key: str) -> None:
        """保存明文到内存替身。"""
        self.stored[(user_id, provider)] = api_key


def _client(tmp_path) -> tuple[TestClient, _FakeApiKeyService]:
    """构造带内部 Token 和替身密钥服务的 API 应用。"""
    token_file = tmp_path / "api_agent_internal_token"
    token_file.write_text("t" * 32, encoding="utf-8")
    settings = Settings.from_environment(
        {
            "TRAVEL_AGENT_ENV": "test",
            "API_AGENT_INTERNAL_TOKEN_FILE": str(token_file),
        }
    )
    application = create_app(settings)
    service = _FakeApiKeyService()
    application.state.user_api_key_service = service
    return TestClient(application), service


def _headers() -> dict[str, str]:
    """返回内部 Token 与用户上下文请求头。"""
    return {
        "Authorization": f"Bearer {'t' * 32}",
        "X-Internal-User-Id": "user_1",
        "X-Internal-User-Role": "user",
    }


def test_api_key_routes_require_internal_token(tmp_path) -> None:
    """缺少内部 Token 时必须拒绝，不得返回任何密钥状态。"""
    client, _ = _client(tmp_path)
    response = client.get("/internal/v1/users/api-keys/tuniu-cli")
    assert response.status_code == 401


def test_api_key_save_check_and_reveal(tmp_path) -> None:
    """保存后状态为已配置，reveal 返回明文且未配置时返回 404。"""
    client, service = _client(tmp_path)
    headers = _headers()
    missing = client.get(
        "/internal/v1/users/api-keys/tuniu-cli/reveal", headers=headers
    )
    empty = client.get("/internal/v1/users/api-keys/tuniu-cli", headers=headers)
    saved = client.put(
        "/internal/v1/users/api-keys/tuniu-cli",
        headers=headers,
        json={"api_key": "sk-abcdefgh"},
    )
    configured = client.get("/internal/v1/users/api-keys/tuniu-cli", headers=headers)
    revealed = client.get(
        "/internal/v1/users/api-keys/tuniu-cli/reveal", headers=headers
    )

    assert missing.status_code == 404
    assert empty.json() == {"provider": "tuniu-cli", "has_key": False}
    assert saved.json() == {"provider": "tuniu-cli", "status": "saved"}
    assert configured.json() == {"provider": "tuniu-cli", "has_key": True}
    assert revealed.json() == {"provider": "tuniu-cli", "api_key": "sk-abcdefgh"}
    assert service.revealed == [("user_1", "tuniu-cli"), ("user_1", "tuniu-cli")]


def test_unknown_provider_is_rejected(tmp_path) -> None:
    """未登记的 provider 必须拒绝，避免任意键名写入。"""
    client, _ = _client(tmp_path)
    headers = _headers()
    status = client.get("/internal/v1/users/api-keys/unknown-provider", headers=headers)
    saved = client.put(
        "/internal/v1/users/api-keys/unknown-provider",
        headers=headers,
        json={"api_key": "sk-abcdefgh"},
    )
    assert status.status_code == 404
    assert saved.status_code == 404
