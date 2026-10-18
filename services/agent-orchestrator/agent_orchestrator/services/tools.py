"""工具注册表(架构 §6/§8):模型可调用的动作在这里注册、执行、回喂。

约定:

- 工具是模型接触外部世界的**唯一**通道;能力按**提供方(provider)**分组——
  连接器(邮件)、本地文档库、日历、知识库各产出一组 `ToolSpec`,`build_tools` 汇总;
  **没有配置的提供方不暴露工具**(能力即权限边界);
- 执行失败不抛给图,而是以 ``{"error": ...}`` 回喂模型让它决策(§6.1)——
  工具坏了不该把整条 run 炸掉;
- 每次调用产出 ToolCallRecord,由 services 层落 ``run.tool.called.v1`` 事件。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any

from agent_orchestrator.services.calendar import CalendarFeed
from agent_orchestrator.services.code_tools import code_specs
from agent_orchestrator.services.connectors import Connector
from agent_orchestrator.services.documents import DocumentLibrary
from agent_orchestrator.services.knowledge import Knowledge

MAX_FETCH_LIMIT = 20
MAX_DOC_LIMIT = 50
MAX_EVENT_LIMIT = 50
MAX_KNOWLEDGE_LIMIT = 10
MAX_DOC_CHARS = 20_000  # 回喂给模型的正文上限(防提示词爆炸)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    handler: Callable[[dict[str, Any]], Awaitable[str]]
    requires_confirmation: bool = False  # 写操作:执行前必须人工放行(§6.2)


@dataclass(frozen=True)
class ToolCallRecord:
    """一次工具调用的审计记录(状态里累积,services 层落事件)。"""

    name: str
    arguments: str
    ok: bool
    detail: str


def _error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def _int_arg(args: dict[str, Any], key: str, default: int, *, maximum: int) -> int:
    try:
        value = int(args.get(key) or default)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


class ToolRegistry:
    """按名字执行工具:未知工具/坏 JSON/执行异常一律回喂错误文本。"""

    def __init__(self, specs: list[ToolSpec]) -> None:
        self._specs = {spec.name: spec for spec in specs}

    def __len__(self) -> int:
        return len(self._specs)

    @property
    def names(self) -> list[str]:
        return list(self._specs)

    def extended(self, specs: list[ToolSpec]) -> ToolRegistry:
        """合并外部工具(如 MCP);同名后者覆盖前者。"""
        return ToolRegistry([*self._specs.values(), *specs])

    def restricted(self, allowed: list[str]) -> ToolRegistry:
        """按 Skill 的 tools 白名单裁剪(能力边界);未知/未列出工具不暴露。"""
        allow = set(allowed)
        return ToolRegistry(
            [spec for name, spec in self._specs.items() if name in allow]
        )

    def requires_confirmation(self, name: str) -> bool:
        """写操作工具:execute 不得直接执行,必须交确认闸(§6.2)。"""
        spec = self._specs.get(name)
        return bool(spec and spec.requires_confirmation)

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


def connector_specs(tenant_id: str, connector: Connector) -> list[ToolSpec]:
    """邮件连接器:列连接 / 读邮件(只读)+ 发信(写操作,需人工确认)。"""

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
        emails = await connector.fetch_emails(
            tenant_id,
            connection_id,
            _int_arg(args, "limit", 5, maximum=MAX_FETCH_LIMIT),
        )
        return json.dumps({"emails": emails}, ensure_ascii=False)

    async def list_attachments(args: dict[str, Any]) -> str:
        connection_id = str(args.get("connection_id") or "")
        message_id = str(args.get("message_id") or "")
        if not connection_id or not message_id:
            return _error("list_email_attachments 需要 connection_id/message_id")
        items = await connector.list_attachments(
            tenant_id, connection_id, message_id
        )
        return json.dumps({"attachments": items}, ensure_ascii=False)

    async def attachment_text(args: dict[str, Any]) -> str:
        connection_id = str(args.get("connection_id") or "")
        message_id = str(args.get("message_id") or "")
        attachment_id = str(args.get("attachment_id") or "")
        if not connection_id or not message_id or not attachment_id:
            return _error(
                "fetch_email_attachment_text 需要 "
                "connection_id/message_id/attachment_id"
            )
        result = await connector.attachment_text(
            tenant_id, connection_id, message_id, attachment_id
        )
        return json.dumps(result, ensure_ascii=False)

    async def send_email(args: dict[str, Any]) -> str:
        connection_id = str(args.get("connection_id") or "")
        to = str(args.get("to") or "")
        subject = str(args.get("subject") or "")
        body = str(args.get("body") or "")
        if not connection_id or not to or not subject or not body:
            return _error("send_email 需要 connection_id/to/subject/body")
        detail = await connector.send_email(tenant_id, connection_id, to, subject, body)
        return json.dumps({"sent": True, "detail": detail}, ensure_ascii=False)

    return [
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
        ToolSpec(
            name="list_email_attachments",
            description="列出某封邮件的附件(只读;附件 id 用于读取文本)",
            parameters={
                "type": "object",
                "properties": {
                    "connection_id": {"type": "string"},
                    "message_id": {"type": "string"},
                },
                "required": ["connection_id", "message_id"],
                "additionalProperties": False,
            },
            handler=list_attachments,
        ),
        ToolSpec(
            name="fetch_email_attachment_text",
            description="把邮件附件解析为文本(PDF/docx/xlsx/文本,只读)",
            parameters={
                "type": "object",
                "properties": {
                    "connection_id": {"type": "string"},
                    "message_id": {"type": "string"},
                    "attachment_id": {"type": "string"},
                },
                "required": ["connection_id", "message_id", "attachment_id"],
                "additionalProperties": False,
            },
            handler=attachment_text,
        ),
        ToolSpec(
            name="send_email",
            description="通过已有连接发送邮件(写操作,需用户确认后才会执行)",
            parameters={
                "type": "object",
                "properties": {
                    "connection_id": {
                        "type": "string",
                        "description": "list_connections 返回的连接 id",
                    },
                    "to": {"type": "string", "description": "收件人邮箱"},
                    "subject": {"type": "string", "description": "主题"},
                    "body": {"type": "string", "description": "正文"},
                },
                "required": ["connection_id", "to", "subject", "body"],
                "additionalProperties": False,
            },
            handler=send_email,
            requires_confirmation=True,
        ),
    ]


def document_specs(documents: DocumentLibrary) -> list[ToolSpec]:
    """本地文档库:列文档 / 读文档(只读,目录受限)。"""

    async def list_documents(args: dict[str, Any]) -> str:
        entries = await documents.list(
            limit=_int_arg(args, "limit", 20, maximum=MAX_DOC_LIMIT)
        )
        return json.dumps(
            {"documents": [asdict(entry) for entry in entries]}, ensure_ascii=False
        )

    async def read_document(args: dict[str, Any]) -> str:
        path = str(args.get("path") or "")
        if not path:
            return _error("缺少 path(可先调 list_documents 获取)")
        text = await documents.read(path)
        return json.dumps(
            {
                "path": path,
                "chars": len(text),
                "truncated": len(text) > MAX_DOC_CHARS,
                "content": text[:MAX_DOC_CHARS],
            },
            ensure_ascii=False,
        )

    return [
        ToolSpec(
            name="list_documents",
            description="列出工作区文档库中的文档(相对路径/大小/修改时间,只读)",
            parameters={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_DOC_LIMIT,
                        "description": "返回条数,默认 20",
                    }
                },
                "additionalProperties": False,
            },
            handler=list_documents,
        ),
        ToolSpec(
            name="read_document",
            description="读取文档库中某个文档的正文(只读,超长会截断)",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "list_documents 返回的相对路径",
                    }
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=read_document,
        ),
    ]


def calendar_specs(calendar: CalendarFeed) -> list[ToolSpec]:
    """日历:列事件(只读;不展开 RRULE,重复事件只出现一次)。"""

    async def list_calendar_events(args: dict[str, Any]) -> str:
        events = await calendar.events(
            limit=_int_arg(args, "limit", 20, maximum=MAX_EVENT_LIMIT)
        )
        return json.dumps(
            {"events": [asdict(event) for event in events]}, ensure_ascii=False
        )

    return [
        ToolSpec(
            name="list_calendar_events",
            description="列出日历事件(按开始时间排序,只读;重复规则不展开)",
            parameters={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_EVENT_LIMIT,
                        "description": "返回条数,默认 20",
                    }
                },
                "additionalProperties": False,
            },
            handler=list_calendar_events,
        )
    ]


def knowledge_specs(tenant_id: str, knowledge: Knowledge) -> list[ToolSpec]:
    """企业知识库检索(只读):让模型先查资料再回答。"""

    async def search_knowledge(args: dict[str, Any]) -> str:
        query = str(args.get("query") or "").strip()
        if not query:
            return _error("缺少 query")
        result = await knowledge.search(
            tenant_id, query, _int_arg(args, "limit", 5, maximum=MAX_KNOWLEDGE_LIMIT)
        )
        hits = [
            {
                "title": hit.get("title"),
                "score": hit.get("score"),
                "content": hit.get("content"),
            }
            for hit in result.get("hits") or []
        ]
        return json.dumps(
            {
                "query": query,
                "provider": result.get("provider"),
                "model": result.get("model"),
                "hits": hits,
            },
            ensure_ascii=False,
        )

    return [
        ToolSpec(
            name="search_knowledge",
            description="在团队知识库中做向量检索,返回最相关的资料片段(只读)",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索问题/关键词"},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_KNOWLEDGE_LIMIT,
                        "description": "返回片段数,默认 5",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=search_knowledge,
        )
    ]


def build_tools(
    tenant_id: str,
    *,
    connector: Connector | None = None,
    documents: DocumentLibrary | None = None,
    calendar: CalendarFeed | None = None,
    knowledge: Knowledge | None = None,
    code_roots: list[str] | None = None,
) -> ToolRegistry:
    """汇总已配置提供方的工具;未配置的能力不暴露(能力即权限边界)。"""
    specs: list[ToolSpec] = []
    if connector is not None:
        specs += connector_specs(tenant_id, connector)
    if documents is not None:
        specs += document_specs(documents)
    if calendar is not None:
        specs += calendar_specs(calendar)
    if knowledge is not None:
        specs += knowledge_specs(tenant_id, knowledge)
    if code_roots:
        specs += code_specs(code_roots)
    return ToolRegistry(specs)
