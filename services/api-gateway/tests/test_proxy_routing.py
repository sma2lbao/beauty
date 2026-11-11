"""网关路径解析:声明式前缀表 →(低基数上游名, 上游 URL)。

路由表是数据:新增服务只往 `_SERVICE_BASES` / `resolve_upstream` 里加一条,
日志/指标/span 的 `upstream` 维度自动跟着变(见 docs/observability.md §10)。
"""

from __future__ import annotations

import pytest

from api_gateway.api.proxy import Upstream, resolve_upstream
from tests.conftest import settings

BASES = {
    "account_base_url": "http://account",
    "workspace_base_url": "http://workspace",
    "knowledge_base_url": "http://knowledge",
    "connector_base_url": "http://connector",
    "llm_gateway_base_url": "http://llm",
}


@pytest.mark.parametrize(
    ("path", "service", "url"),
    [
        ("v1/runs", "runtime", "http://runtime/api/v1/runs"),
        (
            "v1/runs/r1/events/stream",
            "runtime",
            "http://runtime/api/v1/runs/r1/events/stream",
        ),
        ("models", "llm-gateway", "http://llm/v1/models"),
        ("usage/today", "account", "http://account/v1/usage/today"),
        ("byok/keys", "account", "http://account/v1/byok/keys"),
        (
            "connector/connections/c1/refresh",
            "connector",
            "http://connector/v1/connections/c1/refresh",
        ),
        (
            "connector/v1/oauth/demo/callback",
            "connector",
            "http://connector/v1/oauth/demo/callback",
        ),
        ("account/v1/devices", "account", "http://account/v1/devices"),
        ("workspace/v1/workspaces", "workspace", "http://workspace/v1/workspaces"),
        (
            "knowledge/v1/knowledge/search",
            "knowledge",
            "http://knowledge/v1/knowledge/search",
        ),
    ],
)
def test_resolve_upstream(path: str, service: str, url: str) -> None:
    assert resolve_upstream(settings(**BASES), path) == Upstream(service, url)


def test_unknown_prefix_is_not_routed() -> None:
    assert resolve_upstream(settings(**BASES), "unknown/path") is None
