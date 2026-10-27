"""仓储层:租户隔离 + 统一 404(仓储不提交,事务由 session_scope 负责)。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from database.repository import TenantRepository
from sqlalchemy import func, select
from sqlalchemy.sql.elements import ColumnElement

from tool_service.models.file_read import FileRead
from tool_service.models.tool_call import ToolCall
from tool_service.schemas.tools import ToolCallRead

PATH_LIST_PREFIX = "["
#: 计入单 run 写配额的正式工具名。
WRITE_TOOL_NAMES = ("write", "edit")


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
        conversation_id: str | None = None,
        tool_name: str | None = None,
        ok: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ToolCall]:
        criteria = self._filters(
            run_id=run_id,
            conversation_id=conversation_id,
            tool_name=tool_name,
            ok=ok,
        )
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
        conversation_id: str | None = None,
        tool_name: str | None = None,
        ok: bool | None = None,
    ) -> int:
        criteria = self._filters(
            run_id=run_id,
            conversation_id=conversation_id,
            tool_name=tool_name,
            ok=ok,
        )
        statement = (
            select(func.count())
            .select_from(ToolCall)
            .where(ToolCall.tenant_id == tenant_id, *criteria)
        )
        return int(await self.session.scalar(statement) or 0)

    async def delete_by_call_id(self, tenant_id: str, tool_call_id: str) -> int:
        return await self.delete_where(tenant_id, ToolCall.tool_call_id == tool_call_id)

    async def write_usage_for_run(self, tenant_id: str, run_id: str) -> tuple[int, int]:
        """单 run 已成功的写次数与累计字节数(写配额依据)。"""

        statement = select(
            func.count(),
            func.coalesce(func.sum(ToolCall.bytes_written), 0),
        ).where(
            ToolCall.tenant_id == tenant_id,
            ToolCall.run_id == run_id,
            ToolCall.tool_name.in_(WRITE_TOOL_NAMES),
            ToolCall.ok.is_(True),
        )
        row = (await self.session.execute(statement)).one()
        return int(row[0] or 0), int(row[1] or 0)

    def _filters(
        self,
        *,
        run_id: str | None,
        conversation_id: str | None,
        tool_name: str | None,
        ok: bool | None,
    ) -> tuple[ColumnElement[bool], ...]:
        criteria: list[ColumnElement[bool]] = []
        if run_id is not None:
            criteria.append(ToolCall.run_id == run_id)
        if conversation_id is not None:
            criteria.append(ToolCall.conversation_id == conversation_id)
        if tool_name is not None:
            criteria.append(ToolCall.tool_name == tool_name)
        if ok is not None:
            criteria.append(ToolCall.ok == ok)
        return tuple(criteria)


def to_read(entity: ToolCall, *, include_patch: bool = False) -> ToolCallRead:
    """ORM → HTTP schema(集中处理 paths JSON 解码)。

    补丁默认不回包(可能很大):显式 include_patch=true 才带,行数字段照常。
    """

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
        bytes_written=entity.bytes_written,
        truncated=entity.truncated,
        sha256_before=entity.sha256_before,
        sha256_after=entity.sha256_after,
        paths=decode_paths(entity.paths),
        patch=entity.patch if include_patch else None,
        lines_added=entity.lines_added,
        lines_removed=entity.lines_removed,
        patch_truncated=entity.patch_truncated,
        sandbox_id=entity.sandbox_id,
        created_at=entity.created_at,
    )


def encode_arguments(arguments: dict[str, Any]) -> str:
    """审计列只存前 2000 字符;完整参数由调用方在事件流里留。"""

    return json.dumps(arguments, ensure_ascii=False, sort_keys=True)[:2000]


class FileReadRepository(TenantRepository[FileRead]):
    """file_reads 表:read 成功后 upsert,write/edit 前校验(读前置)。"""

    model = FileRead

    async def find_read(
        self, tenant_id: str, *, scope_key: str, path: str
    ) -> FileRead | None:
        return await self.session.scalar(
            self.scoped(
                tenant_id,
                FileRead.scope_key == scope_key,
                FileRead.path == path,
            )
        )

    async def upsert_read(
        self,
        tenant_id: str,
        *,
        scope_key: str,
        path: str,
        sha256: str,
        run_id: str | None,
        conversation_id: str | None,
    ) -> FileRead:
        """记录一次读取;已存在则刷新 sha256/read_at(同一 scope 内文件版本)。"""

        record = await self.find_read(tenant_id, scope_key=scope_key, path=path)
        if record is None:
            record = FileRead(
                tenant_id=tenant_id,
                scope_key=scope_key,
                path=path,
                sha256=sha256,
                run_id=run_id,
                conversation_id=conversation_id,
            )
            self.session.add(record)
            await self.session.flush()
            return record
        record.sha256 = sha256
        record.run_id = run_id
        record.conversation_id = conversation_id
        record.read_at = datetime.now(UTC)
        await self.session.flush()
        return record
