"""工具注册表(架构 §6/§8):模型可调用的动作在这里注册、执行、回喂。

约定:

- 工具是模型接触外部世界的**唯一**通道(经 connectors/… 客户端出去);
- 执行失败不抛给图,而是以 ``{"error": ...}`` 回喂模型让它决策(§6.1)——
  工具坏了不该把整条 run 炸掉;
- 每次调用产出 ToolCallRecord,由 services 层落 ``run.tool.called.v1`` 事件。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from agent_orchestrator.services.connectors import Connector

MAX_FETCH_LIMIT = 20


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    handler: Callable[[dict[str, Any]], Awaitable[str]]


@dataclass(frozen=True)
class ToolCallRecord:
    """一次工具调用的审计记录(状态里累积,services 层落事件)。"""

    name: str
    arguments: str
    ok: bool
    detail: str


def _error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


class ToolRegistry:
    """按名字执行工具:未知工具/坏 JSON/执行异常一律回喂错误文本。"""

    def __init__(self, specs: list[ToolSpec]) -> None:
        self._specs = {spec.name: spec for spec in specs}

    def __len__(self) -> int:
        return len(self._specs)

    def schemas(self) -> list[dict[str, Any]]:
        """OpenAI tools 格式(网关原样透传给 litellm)。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
            for spec in self._specs.values()
        ]

    async def execute(self, name: str, arguments: str) -> str:
        spec = self._specs.get(name)
        if spec is None:
            return _error(f"未知工具:{name}")
        try:
            args = json.loads(arguments or "{}")
        except ValueError as exc:
            return _error(f"工具参数不是合法 JSON:{exc}")
        if not isinstance(args, dict):
            return _error("工具参数必须是 JSON 对象")
        try:
            return await spec.handler(args)
        except Exception as exc:
            return _error(f"{type(exc).__name__}: {exc}")


def build_tools(connector: Connector, tenant_id: str) -> ToolRegistry:
    """装配该租户可见的工具集(阶段 A:邮件连接器两个只读工具)。"""

    async def list_connections(args: dict[str, Any]) -> str:
        conns = await connector.list_connections(tenant_id)
        brief = [
            {
                "id": conn.get("id"),
                "account": conn.get("account"),
                "provider": conn.get("provider"),
                "status": conn.get("status"),
            }
            for conn in conns
        ]
        return json.dumps({"connections": brief}, ensure_ascii=False)

    async def fetch_emails(args: dict[str, Any]) -> str:
        connection_id = str(args.get("connection_id") or "")
        if not connection_id:
            return _error("缺少 connection_id(可先调 list_connections 获取)")
        limit = int(args.get("limit") or 5)
        emails = await connector.fetch_emails(
            tenant_id, connection_id, max(1, min(limit, MAX_FETCH_LIMIT))
        )
        return json.dumps({"emails": emails}, ensure_ascii=False)

    return ToolRegistry(
        [
            ToolSpec(
                name="list_connections",
                description="列出当前租户已连接的外部账号(id/account/provider/status)",
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                handler=list_connections,
            ),
            ToolSpec(
                name="fetch_emails",
                description="读取指定连接的收件箱最近邮件(只读)",
                parameters={
                    "type": "object",
                    "properties": {
                        "connection_id": {
                            "type": "string",
                            "description": "list_connections 返回的连接 id",
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_FETCH_LIMIT,
                            "description": "读取封数,默认 5",
                        },
                    },
                    "required": ["connection_id"],
                    "additionalProperties": False,
                },
                handler=fetch_emails,
            ),
        ]
    )
