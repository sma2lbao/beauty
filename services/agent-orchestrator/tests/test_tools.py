"""工具注册表单元:schemas 形状、错误回喂、参数校验、build_tools 装配。"""

import json

from agent_orchestrator.services.tools import (
    MAX_FETCH_LIMIT,
    ToolRegistry,
    ToolSpec,
    build_tools,
)
from tests.conftest import FakeConnector


async def _echo(args: dict) -> str:
    return json.dumps(args, ensure_ascii=False)


def _registry(*specs: ToolSpec) -> ToolRegistry:
    return ToolRegistry(list(specs))


def test_schemas_openai_shape() -> None:
    spec = ToolSpec(
        name="echo",
        description="回显",
        parameters={"type": "object", "properties": {}},
        handler=_echo,
    )
    registry = _registry(spec)
    assert registry.schemas() == [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "回显",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    assert len(registry) == 1


async def test_unknown_tool_and_bad_arguments() -> None:
    registry = _registry(
        ToolSpec(name="echo", description="", parameters={}, handler=_echo)
    )
    assert json.loads(await registry.execute("nope", "{}"))["error"].startswith(
        "未知工具"
    )
    assert (
        "不是合法 JSON" in json.loads(await registry.execute("echo", "{bad"))["error"]
    )
    assert (
        "必须是 JSON 对象" in json.loads(await registry.execute("echo", "[1]"))["error"]
    )


async def test_handler_exception_becomes_error_text() -> None:
    async def boom(args: dict) -> str:
        raise RuntimeError("炸了")

    registry = _registry(
        ToolSpec(name="boom", description="", parameters={}, handler=boom)
    )
    payload = json.loads(await registry.execute("boom", "{}"))
    assert "RuntimeError: 炸了" in payload["error"]


async def test_build_tools_list_and_fetch() -> None:
    connector = FakeConnector()
    registry = build_tools(connector, "t-1")
    names = {schema["function"]["name"] for schema in registry.schemas()}
    assert names == {"list_connections", "fetch_emails", "send_email"}
    # 写操作必须在执行前被标记为需确认(§6.2)
    assert registry.requires_confirmation("send_email") is True
    assert registry.requires_confirmation("fetch_emails") is False

    listed = json.loads(await registry.execute("list_connections", "{}"))
    assert listed["connections"][0]["id"] == "conn-1"
    assert "credentials" not in json.dumps(listed)  # 即便连接器误返回也不外泄

    fetched = json.loads(
        await registry.execute(
            "fetch_emails", '{"connection_id": "conn-1", "limit": 1}'
        )
    )
    assert fetched["emails"][0]["subject"] == "周报"
    assert connector.calls[-1] == ("fetch_emails", "t-1", "conn-1", 1)


async def test_fetch_emails_requires_id_and_clamps_limit() -> None:
    connector = FakeConnector()
    registry = build_tools(connector, "t-1")
    missing = json.loads(await registry.execute("fetch_emails", "{}"))
    assert "缺少 connection_id" in missing["error"]

    await registry.execute("fetch_emails", '{"connection_id": "c", "limit": 999}')
    assert connector.calls[-1][-1] == MAX_FETCH_LIMIT
