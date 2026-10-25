"""仓储层:租户隔离 + 统一 404(仓储不提交,事务由 session_scope 负责)。"""

from __future__ import annotations

import json
from typing import Any

from database.repository import TenantRepository
from sqlalchemy import func, select
from sqlalchemy.sql.elements import ColumnElement

from tool_service.models.tool_call import ToolCall
from tool_service.schemas.tools import ToolCallRead

PATH_LIST_PREFIX = "["


def decode_paths(raw: str) -> list[str]:
    """审计记录里的 paths 列是 JSON 数组字符串;坏数据按空列表降级。"""

    if not raw.startswith(PATH_LIST_PREFIX):
        return []
    try:
        value = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


class ToolCallRepository(TenantRepository[ToolCall]):
    """tool_calls 表:按 tool_call_id 幂等查询 + 运维过滤。"""

    model = ToolCall
    not_found_code = "tool.call_not_found"
    not_found_message = "工具调用记录不存在"
    id_detail_key = "tool_call_id"

    async def find_by_call_id(
        self, tenant_id: str, tool_call_id: str
    ) -> ToolCall | None:
        return await self.session.scalar(
            self.scoped(tenant_id, ToolCall.tool_call_id == tool_call_id)
        )

    async def require_by_call_id(self, tenant_id: str, tool_call_id: str) -> ToolCall:
        """按业务幂等键取审计行;不存在(或跨租户)一律 404。"""

        record = await self.find_by_call_id(tenant_id, tool_call_id)
        if record is None:
            raise self._not_found(tool_call_id)
        return record

    async def list_page(
        self,
        tenant_id: str,
        *,
        run_id: str | None = None,
        tool_name: str | None = None,
        ok: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ToolCall]:
        criteria = self._filters(run_id=run_id, tool_name=tool_name, ok=ok)
        return await self.list_scoped(
            tenant_id,
            *criteria,
            limit=limit,
            offset=offset,
            order_by=(ToolCall.created_at.desc(), ToolCall.id.desc()),
        )

    async def count_page(
        self,
        tenant_id: str,
        *,
        run_id: str | None = None,
        tool_name: str | None = None,
        ok: bool | None = None,
    ) -> int:
        criteria = self._filters(run_id=run_id, tool_name=tool_name, ok=ok)
        statement = (
            select(func.count())
            .select_from(ToolCall)
            .where(ToolCall.tenant_id == tenant_id, *criteria)
        )
        return int(await self.session.scalar(statement) or 0)

    async def delete_by_call_id(self, tenant_id: str, tool_call_id: str) -> int:
        return await self.delete_where(tenant_id, ToolCall.tool_call_id == tool_call_id)

    def _filters(
        self,
        *,
        run_id: str | None,
        tool_name: str | None,
        ok: bool | None,
    ) -> tuple[ColumnElement[bool], ...]:
        criteria: list[ColumnElement[bool]] = []
        if run_id is not None:
            criteria.append(ToolCall.run_id == run_id)
        if tool_name is not None:
            criteria.append(ToolCall.tool_name == tool_name)
        if ok is not None:
            criteria.append(ToolCall.ok == ok)
        return tuple(criteria)


def to_read(entity: ToolCall) -> ToolCallRead:
    """ORM → HTTP schema(集中处理 paths JSON 解码)。"""

    return ToolCallRead(
        id=entity.id,
        tool_call_id=entity.tool_call_id,
        run_id=entity.run_id,
        conversation_id=entity.conversation_id,
        tool_name=entity.tool_name,
        tool_version=entity.tool_version,
        risk=entity.risk,
        decision=entity.decision,
        ok=entity.ok,
        error_code=entity.error_code,
        duration_ms=entity.duration_ms,
        result_chars=entity.result_chars,
        truncated=entity.truncated,
        paths=decode_paths(entity.paths),
        sandbox_id=entity.sandbox_id,
        created_at=entity.created_at,
    )


def encode_arguments(arguments: dict[str, Any]) -> str:
    """审计列只存前 2000 字符;完整参数由调用方在事件流里留。"""

    return json.dumps(arguments, ensure_ascii=False, sort_keys=True)[:2000]
