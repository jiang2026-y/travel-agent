# 本文件定义远程工具的默认拒绝策略。
# 定义 OperationDefinition、ProviderDefinition、ProviderRegistry 与 ToolPolicyDenied。
# 用于校验只读操作、基地址和域名白名单。

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


class ToolPolicyDenied(RuntimeError):
    """表示工具调用被安全策略拒绝。"""


@dataclass(frozen=True, slots=True)
class OperationDefinition:
    """描述 Provider 中一个经审查的远程操作。"""

    operation_key: str
    method: str
    path: str
    read_only: bool

    def __post_init__(self) -> None:
        """校验操作标识、HTTP 方法与相对路径。"""
        if not self.operation_key:
            raise ValueError("operation_key_required")
        if self.method.upper() not in {"GET", "HEAD", "POST"}:
            raise ValueError("method_not_supported")
        if not self.path.startswith("/") or "//" in self.path:
            raise ValueError("operation_path_invalid")


@dataclass(frozen=True, slots=True)
class ProviderDefinition:
    """描述一个远程 HTTP Provider 的基地址、域名和操作白名单。"""

    provider_key: str
    base_url: str
    allowed_hosts: tuple[str, ...]
    operations: tuple[OperationDefinition, ...]

    def __post_init__(self) -> None:
        """校验 Provider 只使用 HTTPS，且基地址主机位于精确白名单中。"""
        parsed = urlsplit(self.base_url)
        if not self.provider_key:
            raise ValueError("provider_key_required")
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("provider_base_url_must_use_https")
        if parsed.hostname not in self.allowed_hosts:
            raise ValueError("base_host_not_allowlisted")
        if not self.operations:
            raise ValueError("provider_operations_required")
        operation_keys = {operation.operation_key for operation in self.operations}
        if len(operation_keys) != len(self.operations):
            raise ValueError("operation_key_duplicated")

    def get_operation(self, operation_key: str) -> OperationDefinition:
        """返回指定操作；未登记时抛出安全拒绝。"""
        for operation in self.operations:
            if operation.operation_key == operation_key:
                return operation
        raise ToolPolicyDenied("operation_not_registered")


@dataclass(frozen=True, slots=True)
class AuthorizedOperation:
    """表示已经通过白名单和只读策略校验的操作。"""

    provider: ProviderDefinition
    operation: OperationDefinition


class ProviderRegistry:
    """维护 Provider 定义并执行默认拒绝、只读与域名白名单校验。"""

    def __init__(self, providers: list[ProviderDefinition] | None = None) -> None:
        """以可选的受审查 Provider 列表初始化注册表。"""
        self._providers = {provider.provider_key: provider for provider in providers or []}
        if len(self._providers) != len(providers or []):
            raise ValueError("provider_key_duplicated")

    @property
    def provider_count(self) -> int:
        """返回已通过配置校验的 Provider 数量，不暴露其名称或敏感配置。"""
        return len(self._providers)

    def authorize(self, provider_key: str, operation_key: str) -> AuthorizedOperation:
        """校验 Provider 和操作，且一律拒绝写语义调用。"""
        provider = self._providers.get(provider_key)
        if provider is None:
            raise ToolPolicyDenied("provider_not_registered")
        operation = provider.get_operation(operation_key)
        if not operation.read_only:
            raise ToolPolicyDenied("write_operation_denied")
        return AuthorizedOperation(provider=provider, operation=operation)
