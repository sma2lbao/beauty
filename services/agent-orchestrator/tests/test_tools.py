"""工具注册表单元:schemas 形状、错误回喂、参数校验、build_tools 装配。"""

import json

from agent_orchestrator.services.tools import (
    ToolRegistry,
    ToolRisk,
    ToolSpec,
    build_tools,
)
from tests.conftest import FakeDangerTool, FakeKnowledge


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


async def test_build_tools_exposes_only_configured_providers() -> None:
    """能力即权限边界:没配置的提供方不暴露工具(§6.2)。"""
    assert len(build_tools("t-1")) == 0

    knowledge = FakeKnowledge()
    registry = build_tools("t-1", knowledge=knowledge)
    names = {schema["function"]["name"] for schema in registry.schemas()}
    assert names == {"search_knowledge"}
    assert registry.risk("search_knowledge") is ToolRisk.READ
    assert registry.requires_confirmation("search_knowledge") is False

    result = json.loads(
        await registry.execute("search_knowledge", '{"query": "本周日程"}')
    )
    assert result["hits"][0]["title"] == "客户 A 合同"
    assert knowledge.queries == [("t-1", "本周日程", 5)]


async def test_build_tools_extended_keeps_custom_specs() -> None:
    """extended():在已配置提供方之上追加自定义工具(测试/扩展用)。"""
    danger = FakeDangerTool()
    registry = build_tools("t-1", knowledge=FakeKnowledge()).extended([danger.spec()])
    assert registry.risk("external_publish") is ToolRisk.DANGER
    assert registry.requires_confirmation("external_publish") is True
    # 未知工具按最严处理:绝不默认放行
    assert registry.risk("no-such-tool") is ToolRisk.DANGER
