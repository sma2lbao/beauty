"""连接器工具面:目录 TTL 缓存与降级(零网络)。"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from common.security.internal_secret import ACCOUNT_HEADER, SECRET_HEADER
from common.tools import ExecutorKind

from tests.connectors_support import (
    DEMO_TOOL,
    SECRET,
    FakeConnectorService,
    demo_payload,
    make_gateway,
)

# ---- 目录:TTL 缓存与降级 ------------------------------------------------


async def test_tools_fetches_catalog_once_per_account() -> None:
    service = FakeConnectorService()
    gateway = make_gateway(service)

    first = await gateway.tools("t-1")
    second = await gateway.tools("t-1")
    other = await gateway.tools("t-2")

    assert [tool.name for tool in first] == ["Demo__read_document"]
    assert second == first
    assert [tool.name for tool in other] == ["Demo__read_document"]
    # 同账号命中缓存,跨账号各自拉取。
    assert len(service.catalog_requests()) == 2
    first_request, second_request = service.catalog_requests()
    assert first_request.headers[SECRET_HEADER] == SECRET
    assert first_request.headers[ACCOUNT_HEADER] == "t-1"
    assert second_request.headers[ACCOUNT_HEADER] == "t-2"
    await gateway.aclose()


async def test_tools_refetches_after_ttl() -> None:
    service = FakeConnectorService()
    gateway = make_gateway(service, ttl_seconds=0.0)

    await gateway.tools("t-1")
    await gateway.tools("t-1")

    assert len(service.catalog_requests()) == 2
    await gateway.aclose()


async def test_disabled_gateway_returns_empty() -> None:
    service = FakeConnectorService()
    gateway = make_gateway(service, secret="")

    assert gateway.enabled is False
    assert await gateway.tools("t-1") == ()
    assert await gateway.find("t-1", "Demo__read_document") is None
    assert service.requests == []
    await gateway.aclose()


@pytest.mark.parametrize(
    "configure",
    [
        lambda s: setattr(s, "catalog_status", 500),
        lambda s: setattr(s, "catalog_status", 401),
        lambda s: setattr(s, "catalog_body", "not-json"),
        lambda s: setattr(s, "catalog_body", {"tools": "nope"}),
        lambda s: setattr(s, "catalog_raise", httpx.ConnectError("refused")),
    ],
)
async def test_catalog_failure_degrades_to_empty(configure: Any) -> None:
    service = FakeConnectorService()
    configure(service)
    gateway = make_gateway(service)

    assert await gateway.tools("t-1") == ()
    await gateway.aclose()


async def test_catalog_failure_is_not_cached() -> None:
    service = FakeConnectorService()
    service.catalog_status = 503
    gateway = make_gateway(service)

    assert await gateway.tools("t-1") == ()
    service.catalog_status = 200
    assert [tool.name for tool in await gateway.tools("t-1")] == ["Demo__read_document"]
    assert len(service.catalog_requests()) == 2
    await gateway.aclose()


async def test_catalog_skips_malformed_and_wrong_executor() -> None:
    service = FakeConnectorService()
    service.catalog_body = {
        "tools": [
            {"name": "broken"},
            demo_payload(name="Sandboxish", executor="sandbox"),
            demo_payload(name="Demo__list_files"),
            demo_payload(),
        ]
    }
    gateway = make_gateway(service)

    tools = await gateway.tools("t-1")

    assert [tool.name for tool in tools] == [
        "Demo__list_files",
        "Demo__read_document",
    ]
    assert tools[0].executor is ExecutorKind.CONNECTOR
    assert tools[0].connector == {"provider": "demo"}
    await gateway.aclose()


async def test_find_matches_by_name() -> None:
    service = FakeConnectorService()
    gateway = make_gateway(service)

    found = await gateway.find("t-1", "Demo__read_document")
    missing = await gateway.find("t-1", "Demo__nope")

    assert found == DEMO_TOOL
    assert missing is None
    await gateway.aclose()
