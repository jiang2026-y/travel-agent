# 文件职责：验证签证网关端点的工具映射、鉴权头、免 Key 放行与降级行为。
# 定义六个工具的路径映射、缺 Key 降级、上游认证失败降级与参数校验测试。
from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from travel_agent_tool_gateway.http_client import RestrictedHttpClient
from travel_agent_tool_gateway.main import GatewaySettings, _dashscope_registry, create_app


def _build(tmp_path, handler, *, with_key: bool = True) -> tuple[TestClient, str]:
    """构造注入 MockTransport 的网关客户端，可选是否配置签证密钥。"""
    gateway_token_file = tmp_path / "agent_gateway_internal_token"
    gateway_token_file.write_text("g" * 32, encoding="utf-8")
    visa_key_file = tmp_path / "orizn_visa_api_key"
    visa_key_file.write_text("orizn_visa_test_key" if with_key else "", encoding="utf-8")
    settings = GatewaySettings(
        agent_gateway_token_file=str(gateway_token_file),
        orizn_visa_api_key_file=str(visa_key_file),
    )
    registry = _dashscope_registry(settings)
    restricted_client = RestrictedHttpClient(registry, transport=httpx.MockTransport(handler))
    application = create_app(registry=registry, settings=settings, client=restricted_client)
    return TestClient(application), "g" * 32


def test_visa_tool_paths_and_headers(tmp_path) -> None:
    """六个工具必须命中登记路径，并携带 x-api-key 与 Referer。"""
    seen: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        """记录请求路径、查询参数与请求头。"""
        seen.append(
            {
                "host": request.url.host,
                "path": request.url.path,
                "query": request.url.query.decode(),
                "api_key": request.headers.get("x-api-key"),
                "referer": request.headers.get("referer"),
            }
        )
        return httpx.Response(200, json={"status": "ok"})

    client, token = _build(tmp_path, handler)
    headers = {"Authorization": f"Bearer {token}"}
    cases = (
        ("quick_check", {"passport": "CHN", "destination": "THA"}, "/api/v1/visa/check"),
        (
            "requirement",
            {"passport": "CHN", "destination": "JPN", "lang": "zh"},
            "/api/v1/visa",
        ),
        (
            "transit",
            {"passport": "CHN", "transit_country": "SGP", "lang": "en"},
            "/api/v1/visa",
        ),
        (
            "compare",
            {"passport": "CHN", "destinations": ["THA", "JPN"]},
            "/api/v1/visa/bulk",
        ),
        ("changes", {"destination": "THA"}, "/api/v1/visa/changes"),
        ("coverage", {}, "/api/v1/visa/stats"),
    )
    for tool, payload, expected_path in cases:
        response = client.post(f"/internal/v1/visa/{tool}", headers=headers, json=payload)
        assert response.status_code == 200, (tool, response.text)
        assert response.json()["available"] is True
        assert seen[-1]["path"] == expected_path
        assert seen[-1]["host"] == "visa.orizn.app"
        assert seen[-1]["api_key"] == "orizn_visa_test_key"
        assert seen[-1]["referer"] == "https://visa.orizn.app/mcp"

    assert seen[2]["query"] == "passport=CHN&destination=SGP&lang=en"
    compare_query = str(seen[3]["query"])
    assert "destination=THA%2CJPN" in compare_query


def test_visa_missing_key_keeps_keyless_tools_and_degrades_the_rest(tmp_path) -> None:
    """未配置密钥时只放行免 Key 工具，其余返回稳定降级码。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        """免 Key 调用也应带 Referer。"""
        assert request.headers.get("referer") == "https://visa.orizn.app/mcp"
        assert request.headers.get("x-api-key") is None
        return httpx.Response(200, json={"status": "ok"})

    client, token = _build(tmp_path, handler, with_key=False)
    headers = {"Authorization": f"Bearer {token}"}
    allowed = client.post(
        "/internal/v1/visa/coverage", headers=headers, json={}
    )
    quick = client.post(
        "/internal/v1/visa/quick_check",
        headers=headers,
        json={"passport": "CHN", "destination": "THA"},
    )
    blocked = client.post(
        "/internal/v1/visa/requirement",
        headers=headers,
        json={"passport": "CHN", "destination": "THA"},
    )

    assert allowed.status_code == 200 and allowed.json()["available"] is True
    assert quick.status_code == 200 and quick.json()["available"] is True
    assert blocked.status_code == 200
    assert blocked.json()["error_code"] == "orizn_visa_key_required"


def test_visa_upstream_auth_failure_degrades_without_leaking_body(tmp_path) -> None:
    """上游 403（需付费计划）与 401（密钥缺失或无效）必须映射为不同稳定错误码。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        """模拟上游返回包含密钥片段的未授权响应。"""
        del request
        return httpx.Response(403, json={"message": "bad key orizn_visa_secret"})

    client, token = _build(tmp_path, handler)
    response = client.post(
        "/internal/v1/visa/requirement",
        headers={"Authorization": f"Bearer {token}"},
        json={"passport": "CHN", "destination": "THA"},
    )
    assert response.status_code == 200
    assert response.json()["error_code"] == "orizn_visa_plan_required"
    assert "付费计划" in response.json()["message"]
    assert "orizn_visa_secret" not in response.text


def test_visa_upstream_401_maps_to_auth_failed(tmp_path) -> None:
    """上游 401 表示密钥缺失或无效，应与 403 区分开。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        """模拟上游拒绝当前密钥。"""
        del request
        return httpx.Response(401, json={"error": "API key required"})

    client, token = _build(tmp_path, handler)
    response = client.post(
        "/internal/v1/visa/requirement",
        headers={"Authorization": f"Bearer {token}"},
        json={"passport": "CHN", "destination": "THA"},
    )

    assert response.status_code == 200
    assert response.json()["error_code"] == "orizn_visa_auth_failed"


def test_visa_upstream_429_maps_to_quota_exceeded(tmp_path) -> None:
    """上游 429 表示免费额度用尽，必须与认证失败区分开。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        """模拟上游配额用尽。"""
        del request
        return httpx.Response(429, json={"error": {"code": "quota_exceeded"}})

    client, token = _build(tmp_path, handler)
    response = client.post(
        "/internal/v1/visa/requirement",
        headers={"Authorization": f"Bearer {token}"},
        json={"passport": "CHN", "destination": "THA"},
    )

    assert response.status_code == 200
    assert response.json()["error_code"] == "orizn_visa_quota_exceeded"
    assert "额度" in response.json()["message"]


def test_visa_parameter_validation_rejects_bad_input(tmp_path) -> None:
    """非法国家码、非法语言码与未登记工具都必须拒绝。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        """参数非法时不应产生任何上游请求。"""
        del request
        raise AssertionError("unexpected_upstream_call")

    client, token = _build(tmp_path, handler)
    headers = {"Authorization": f"Bearer {token}"}
    bad_country = client.post(
        "/internal/v1/visa/quick_check",
        headers=headers,
        json={"passport": "CHINA", "destination": "THA"},
    )
    bad_lang = client.post(
        "/internal/v1/visa/requirement",
        headers=headers,
        json={"passport": "CHN", "destination": "THA", "lang": "zh-CN"},
    )
    unknown = client.post("/internal/v1/visa/unknown_tool", headers=headers, json={})
    missing = client.post("/internal/v1/visa/requirement", headers=headers, json={})

    assert bad_country.status_code == 422
    assert bad_lang.status_code == 422
    assert unknown.status_code == 404
    assert missing.status_code == 422
    assert json.dumps(missing.json(), ensure_ascii=False)
