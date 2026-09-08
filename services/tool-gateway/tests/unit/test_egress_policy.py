# 本文件验证 Egress Proxy 的精确百炼白名单配置。
# 定义 test_egress_proxy_allows_only_approved_dashscope_host，用于确保未登记域名不能出网。
from pathlib import Path


def test_egress_proxy_allows_only_approved_dashscope_host() -> None:
    """代理配置只能放行已审批百炼域名的 HTTPS CONNECT，其他请求仍必须拒绝。"""
    config_path = Path(__file__).resolve().parents[4] / "docker/egress-proxy/squid.conf"
    config = config_path.read_text(encoding="utf-8")

    assert "http_access deny all" in config
    assert "ws-afyh9lpghkjx1iz8.cn-beijing.maas.aliyuncs.com" in config
    assert "http_access allow connect_method dashscope_host ssl_port" in config
    assert "cache deny all" in config
    assert "access_log none" in config
