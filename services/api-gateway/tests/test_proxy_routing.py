"""网关路径映射:连接器前缀直通 /v1(含 OAuth 回调的显式 v1 形态)。"""

from __future__ import annotations

from api_gateway.api.proxy import _resolve_upstream
from tests.conftest import settings


def test_connector_rest_gets_v1_prefix() -> None:
    base = settings(connector_base_url="http://connector")
    assert (
        _resolve_upstream(base, "connector/connections/c1/refresh")
        == "http://connector/v1/connections/c1/refresh"
    )


def test_connector_rest_with_explicit_v1_passes_through() -> None:
    """OAuth 回调地址是 .../api/connector/v1/oauth/{provider}/callback。"""

    base = settings(connector_base_url="http://connector")
    assert (
        _resolve_upstream(base, "connector/v1/oauth/demo/callback")
        == "http://connector/v1/oauth/demo/callback"
    )


def test_other_prefixes_unchanged() -> None:
    base = settings(
        account_base_url="http://account", knowledge_base_url="http://knowledge"
    )
    assert (
        _resolve_upstream(base, "knowledge/v1/knowledge/search")
        == "http://knowledge/v1/knowledge/search"
    )
    assert _resolve_upstream(base, "unknown/path") is None
