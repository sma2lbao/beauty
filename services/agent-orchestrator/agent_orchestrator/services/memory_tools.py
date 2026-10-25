"""记忆工具:模型自己搜索/写入/删除长期记忆(读走 READ,写走 WRITE)。

handler 只能拿到 args dict,租户/工作区/来源从 current_tool_context() 读;
取不到上下文或业务校验失败时返回 JSON 错误字符串而不是抛异常——工具失败
不应炸掉整个 run(与 services/tools.py 的约定一致)。
"""

from __future__ import annotations

import json
from typing import Any

from common.errors import AppError
from database.session import session_scope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.services.memory import (
    MEMORY_KINDS,
    MEMORY_SCOPES,
    delete_memory,
    search_memories,
    write_memory,
)
from agent_orchestrator.services.tool_context import current_tool_context
from agent_orchestrator.services.tools import ToolRisk, ToolSpec

#: 工具参数里的查询/内容长度上限,防止模型把整篇文档塞进一次调用。
_MAX_QUERY_CHARS = 2000
#: 单次检索最多返回条数(与 API 上限一致)。
_MAX_LIMIT = 20

SessionFactory = async_sessionmaker[AsyncSession]


def _error(message: str) -> str:
    """工具错误统一回喂 JSON,模型能读懂并自行修正参数。"""
    return json.dumps({"error": message}, ensure_ascii=False)


def _string_arg(args: dict[str, Any], key: str, *, max_chars: int) -> str:
    value = args.get(key)
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_chars]


def _limit_arg(args: dict[str, Any], default: int) -> int:
    value = args.get("limit")
    if not isinstance(value, int) or isinstance(value, bool):
        return default
    return max(1, min(value, _MAX_LIMIT))


def _memory_source(context: Any) -> tuple[str, str | None]:
    """记忆来源:run 优先于对话;都没有(脚本/API 直调)记为 tool。"""
    if context.run_id:
        return "run", context.run_id
    if context.conversation_id:
        return "chat", context.conversation_id
    return "tool", None


def build_memory_tools(
    session_factory: SessionFactory,
    *,
    max_content_chars: int = 8000,
    default_limit: int = 5,
) -> list[ToolSpec]:
    """构造 memory_search/memory_write/memory_delete 三个工具。

    handler 内用 session_scope 自建事务(工具不是 HTTP 请求,没有外层事务),
    因此写入/删除在工具返回时已经提交,后续步骤能立刻检索到。
    """

    async def memory_search(args: dict[str, Any]) -> str:
        context = current_tool_context()
        if context is None:
            return _error("缺少工具上下文(租户),无法检索记忆")
        query = _string_arg(args, "query", max_chars=_MAX_QUERY_CHARS)
        if not query:
            return _error("参数 query 不能为空")
        limit = _limit_arg(args, default_limit)
        async with session_scope(session_factory) as session:
            try:
                hits = await search_memories(
                    session,
                    context.tenant_id,
                    query,
                    workspace_id=context.workspace_id,
                    limit=limit,
                )
            except AppError as exc:
                return _error(exc.message)
            payload = [
                {
                    "id": memory.id,
                    "kind": memory.kind,
                    "content": memory.content[:max_content_chars],
                }
                for memory in hits
            ]
        return json.dumps({"hits": payload}, ensure_ascii=False)

    async def memory_write(args: dict[str, Any]) -> str:
        context = current_tool_context()
        if context is None:
            return _error("缺少工具上下文(租户),无法写入记忆")
        content = _string_arg(args, "content", max_chars=max_content_chars)
        if not content:
            return _error("参数 content 不能为空")
        kind = _string_arg(args, "kind", max_chars=16) or "note"
        scope = _string_arg(args, "scope", max_chars=16) or "tenant"
        if kind not in MEMORY_KINDS:
            return _error(f"未知记忆类型: {kind}")
        if scope not in MEMORY_SCOPES:
            return _error(f"未知记忆作用域: {scope}")
        if scope == "workspace" and not context.workspace_id:
            return _error("工作区级记忆需要工作区上下文")
        source_type, source_id = _memory_source(context)
        async with session_scope(session_factory) as session:
            try:
                memory = await write_memory(
                    session,
                    context.tenant_id,
                    content=content,
                    kind=kind,
                    scope=scope,
                    workspace_id=context.workspace_id,
                    source_type=source_type,
                    source_id=source_id,
                    pinned=bool(args.get("pinned")),
                )
            except AppError as exc:
                return _error(exc.message)
            memory_id = memory.id
        return json.dumps({"id": memory_id}, ensure_ascii=False)

    async def memory_delete(args: dict[str, Any]) -> str:
        context = current_tool_context()
        if context is None:
            return _error("缺少工具上下文(租户),无法删除记忆")
        memory_id = _string_arg(args, "memory_id", max_chars=64)
        if not memory_id:
            return _error("参数 memory_id 不能为空")
        async with session_scope(session_factory) as session:
            try:
                await delete_memory(session, context.tenant_id, memory_id)
            except AppError as exc:
                return _error(exc.message)
        return json.dumps({"deleted": memory_id}, ensure_ascii=False)

    return [
        ToolSpec(
            name="memory_search",
            description=(
                "检索长期记忆(用户偏好、事实、此前约定)。回答涉及用户习惯或"
                "历史承诺时先检索再回答;无命中就按通用方式处理,不要编造记忆。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "检索关键词或一句问题",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": _MAX_LIMIT,
                        "description": "返回条数,默认 5",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=memory_search,
            risk=ToolRisk.READ,
        ),
        ToolSpec(
            name="memory_write",
            description=(
                "写入一条长期记忆。只记跨对话仍有价值的信息(稳定偏好、事实、"
                "约定),不要记录一次性细节或敏感凭据;同主题先检索再决定是否重复写。"
                "kind=procedure 的条目会在后续会话常驻注入(工作区约定),仅用户"
                "明确要求固定做法时使用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "记忆正文(简洁、自包含的一句或几句)",
                    },
                    "kind": {
                        "type": "string",
                        "enum": sorted(MEMORY_KINDS),
                        "description": "记忆类型,默认 note",
                    },
                    "scope": {
                        "type": "string",
                        "enum": sorted(MEMORY_SCOPES),
                        "description": "tenant=全租户可见,workspace=仅当前工作区",
                    },
                    "pinned": {
                        "type": "boolean",
                        "description": "是否置顶(检索时加权)",
                    },
                },
                "required": ["content"],
                "additionalProperties": False,
            },
            handler=memory_write,
            risk=ToolRisk.WRITE,
        ),
        ToolSpec(
            name="memory_delete",
            description=(
                "删除一条长期记忆(软删除)。只有用户明确要求或记忆已被证伪时"
                "才调用,memory_id 来自 memory_search 的返回。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "记忆 id(memory_search 返回)",
                    },
                },
                "required": ["memory_id"],
                "additionalProperties": False,
            },
            handler=memory_delete,
            risk=ToolRisk.WRITE,
        ),
    ]
