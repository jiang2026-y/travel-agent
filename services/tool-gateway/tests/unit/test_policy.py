# 本文件验证远程工具白名单策略。
# 定义默认拒绝、写操作拒绝和域名白名单拒绝测试，确保未登记 Provider 不能产生网络调用。

import pytest

from travel_agent_tool_gateway.policy import (
    OperationDefinition,
    ProviderDefinition,
    ProviderRegistry,
    ToolPolicyDenied,
)


def test_empty_registry_denies_all_operations() -> None:
    """未登记 Provider 时，策略必须默认拒绝。"""
    registry = ProviderRegistry()

    with pytest.raises(ToolPolicyDenied, match="provider_not_registered"):
        registry.authorize("bailian", "retrieve")


def test_write_operation_is_denied_even_when_registered() -> None:
    """即使 Provider 已登记，写语义操作仍必须被全局策略拒绝。"""
    registry = ProviderRegistry(
        [
            ProviderDefinition(
                provider_key="travel",
                base_url="https://readonly.example.test",
                allowed_hosts=("readonly.example.test",),
                operations=(
                    OperationDefinition(
                        operation_key="create_order",
                        method="POST",
                        path="/orders",
                        read_only=False,
                    ),
                ),
            ),
        ],
    )

    with pytest.raises(ToolPolicyDenied, match="write_operation_denied"):
        registry.authorize("travel", "create_order")


def test_provider_base_host_must_be_in_allowlist() -> None:
    """Provider 基地址主机不在白名单时，配置必须不可用。"""
    with pytest.raises(ValueError, match="base_host_not_allowlisted"):
        ProviderDefinition(
            provider_key="invalid",
            base_url="https://untrusted.example.test",
            allowed_hosts=("readonly.example.test",),
            operations=(
                OperationDefinition(
                    operation_key="search",
                    method="GET",
                    path="/search",
                    read_only=True,
                ),
            ),
        )
