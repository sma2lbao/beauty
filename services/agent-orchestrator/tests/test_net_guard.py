"""出站 SSRF 校验(P0-6):地址判定、DNS 解析校验、白名单与 MCP client 收口。

零网络:解析器一律注入 fake,不解析真实域名(MockTransport 也不发请求)。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from agent_orchestrator.services import net_guard
from agent_orchestrator.services.mcp_client import (
    MCPClient,
    configure_url_guard,
    reset_url_guard,
)

# ---------------------------------------------------------------------------
# 地址判定(单一真源,file_capture 也复用它)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.9",
        "192.168.0.1",
        "169.254.169.254",  # 云厂商元数据
        "100.64.0.1",  # 运营商级 NAT(CGNAT)
        "::1",
        "fe80::1",
        "fc00::1",
        "0.0.0.0",
        "224.0.0.1",
        "::ffff:127.0.0.1",  # IPv4-mapped IPv6 不能绕过
    ],
)
def test_private_and_reserved_addresses_are_blocked(address: str) -> None:
    assert net_guard.is_blocked_address(address) is True


@pytest.mark.parametrize(
    "address", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111"]
)
def test_public_addresses_are_allowed(address: str) -> None:
    assert net_guard.is_blocked_address(address) is False


def test_unparsable_address_is_blocked() -> None:
    """判定不出具体 IP 的一律不信(宁可拒绝)。"""
    assert net_guard.is_blocked_address("not-an-ip") is True


def test_parse_allowed_hosts_normalizes_case_and_blanks() -> None:
    assert net_guard.parse_allowed_hosts([" Internal.Example ", "", "  "]) == (
        "internal.example",
    )


# ---------------------------------------------------------------------------
# 连接前校验:域名解析结果命中内网即拒绝
# ---------------------------------------------------------------------------


def test_domain_resolving_to_private_address_is_blocked() -> None:
    with pytest.raises(net_guard.BlockedTargetError) as exc:
        asyncio.run(
            net_guard.assert_url_allowed(
                "https://mcp.example.com/mcp",
                resolver=lambda host: ["93.184.216.34", "10.1.2.3"],
            )
        )
    assert exc.value.host == "mcp.example.com"
    assert exc.value.addresses == ("10.1.2.3",)
    assert "10.1.2.3" in str(exc.value)
    assert "MCP_ALLOWED_HOSTS" in str(exc.value)


def test_domain_resolving_to_public_address_passes() -> None:
    asyncio.run(
        net_guard.assert_url_allowed(
            "https://mcp.example.com/mcp",
            resolver=lambda host: ["93.184.216.34"],
        )
    )


def test_allowed_hosts_bypass_resolution() -> None:
    """自建内网 MCP:显式白名单放行,不再做 DNS 判定。"""
    asyncio.run(
        net_guard.assert_url_allowed(
            "http://internal.example/mcp",
            allowed_hosts=["Internal.Example"],
            resolver=lambda host: ["192.168.0.9"],
        )
    )


def test_resolution_failure_is_allowed() -> None:
    """解析失败 → 放行:本次请求不可达,由真实连接报错(禁网/抖动不误杀)。"""

    def boom(host: str) -> list[str]:
        raise OSError("dns unavailable")

    asyncio.run(
        net_guard.assert_url_allowed("https://mcp.example.com/mcp", resolver=boom)
    )


def test_literal_metadata_ip_is_blocked_without_dns() -> None:
    resolved: list[str] = []

    def resolver(host: str) -> list[str]:
        resolved.append(host)
        return ["93.184.216.34"]

    with pytest.raises(net_guard.BlockedTargetError):
        asyncio.run(
            net_guard.assert_url_allowed(
                "http://169.254.169.254/latest/meta-data", resolver=resolver
            )
        )
    assert resolved == []


def test_literal_public_ip_passes_without_dns() -> None:
    resolved: list[str] = []

    def resolver(host: str) -> list[str]:
        resolved.append(host)
        return ["10.0.0.1"]

    asyncio.run(
        net_guard.assert_url_allowed("https://93.184.216.34/mcp", resolver=resolver)
    )
    assert resolved == []


def test_localhost_name_is_blocked_without_dns() -> None:
    with pytest.raises(net_guard.BlockedTargetError):
        asyncio.run(net_guard.assert_url_allowed("http://localhost:9999/mcp"))


def test_url_without_host_is_blocked() -> None:
    with pytest.raises(net_guard.BlockedTargetError) as exc:
        asyncio.run(net_guard.assert_url_allowed("file:///etc/passwd"))
    assert "主机名" in str(exc.value)


# ---------------------------------------------------------------------------
# MCP client 收口:首个请求前拦住,一个字节都不发出去
# ---------------------------------------------------------------------------


def _recording_transport(requests: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "x"}},
        )

    return httpx.MockTransport(handler)


def test_mcp_client_blocks_metadata_url_before_any_request() -> None:
    requests: list[httpx.Request] = []

    async def scenario() -> None:
        client = MCPClient(
            "http://169.254.169.254/latest/meta-data",
            transport=_recording_transport(requests),
        )
        try:
            with pytest.raises(net_guard.BlockedTargetError):
                await client.initialize()
        finally:
            await client.aclose()

    asyncio.run(scenario())
    assert requests == []


def test_mcp_client_blocks_domain_resolving_to_loopback() -> None:
    requests: list[httpx.Request] = []
    configure_url_guard(resolver=lambda host: ["127.0.0.1"])

    async def scenario() -> None:
        client = MCPClient(
            "http://mcp.example.com/mcp", transport=_recording_transport(requests)
        )
        try:
            with pytest.raises(net_guard.BlockedTargetError):
                await client.initialize()
        finally:
            await client.aclose()

    try:
        asyncio.run(scenario())
    finally:
        reset_url_guard()
    assert requests == []


def test_mcp_client_allowed_host_still_reaches_server() -> None:
    """白名单 host:校验放行,请求照常发出(不误杀自建内网 server)。"""
    requests: list[httpx.Request] = []
    configure_url_guard(
        allowed_hosts=["internal.example"], resolver=lambda host: ["10.0.0.9"]
    )

    async def scenario() -> str:
        client = MCPClient(
            "http://internal.example/mcp", transport=_recording_transport(requests)
        )
        try:
            return json.dumps(await client.initialize(), ensure_ascii=False)
        finally:
            await client.aclose()

    try:
        payload = asyncio.run(scenario())
    finally:
        reset_url_guard()
    assert "protocolVersion" in payload
    # initialize + notifications/initialized 两个 POST 都真的发出去了
    assert len(requests) == 2


# ---------------------------------------------------------------------------
# 降级可见:外部工具加载失败必须留痕(不再 except Exception 静默吞掉)
# ---------------------------------------------------------------------------


def test_tool_extras_failure_is_logged_not_swallowed() -> None:
    from structlog.testing import capture_logs

    from agent_orchestrator.graph.nodes.toolkit import _load_toolkit

    async def failing_extras(account_id: str) -> list:
        raise RuntimeError("mcp spec load blew up")

    with capture_logs() as logs:
        toolkit = asyncio.run(
            _load_toolkit(  # type: ignore[arg-type]
                "acct-1", None, failing_extras, None
            )
        )

    # 行为不变:外部工具故障只降级(没有基础工具工厂时就是空工具箱)
    assert toolkit is None
    failures = [
        entry
        for entry in logs
        if entry.get("event") == "tool extras load failed; falling back to base tools"
    ]
    assert len(failures) == 1
    assert failures[0]["account_id"] == "acct-1"
    assert "RuntimeError" in failures[0]["error"]
