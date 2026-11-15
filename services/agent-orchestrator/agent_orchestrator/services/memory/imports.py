"""记忆批量导入:条目校验、近重复去重、导入结果汇总。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from common.errors import AppError
from common.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.repositories import MemoryRepository

from .constants import (
    _PROCEDURE_KIND,
    LOGGER_NAME,
    MEMORY_INJECTION_MODES,
    MEMORY_KINDS,
    MEMORY_SCOPES,
    MEMORY_SOURCE_TYPES,
)
from .scoring import content_similarity, find_sensitive
from .store import write_memory

logger = get_logger(LOGGER_NAME)

#: 导入时用于近重复检测的既有记忆扫描上限(与账号默认额度同量级)。
_IMPORT_SCAN_LIMIT = 2000
#: 导入结果里保留的错误明细条数:前 50 条足够定位问题,避免响应体爆炸。
_IMPORT_ERROR_LIMIT = 50


def _import_dedupe_key(
    kind: str, scope: str, workspace_id: str | None
) -> tuple[str, str, str | None]:
    """近重复判定只在同类型、同作用域、同工作区内比较。"""
    return (kind, scope, workspace_id or None)


def _validate_import_item(
    item: Mapping[str, Any], *, reject_sensitive: bool
) -> tuple[dict[str, Any] | None, tuple[str, str] | None]:
    """校验一条导入项;返回 (规范化字段, 错误码+消息)。"""
    content = str(item.get("content") or "").strip()
    if not content:
        return None, ("memory.empty", "记忆内容不能为空")
    kind = str(item.get("kind") or "note")
    if kind not in MEMORY_KINDS:
        return None, ("memory.invalid_kind", f"未知记忆类型: {kind}")
    scope = str(item.get("scope") or "account")
    if scope not in MEMORY_SCOPES:
        return None, ("memory.invalid_scope", f"未知记忆作用域: {scope}")
    workspace_id = item.get("workspace_id")
    workspace_id = str(workspace_id) if workspace_id else None
    if scope == "workspace" and not workspace_id:
        return None, ("memory.workspace_required", "工作区级记忆缺少 workspace_id")
    if scope == "account":
        workspace_id = None
    injection_mode = str(item.get("injection_mode") or "retrieved")
    if injection_mode not in MEMORY_INJECTION_MODES:
        return None, ("memory.invalid_injection", f"未知注入模式: {injection_mode}")
    if kind == _PROCEDURE_KIND and injection_mode == "always":
        return None, (
            "memory.invalid_injection",
            "procedure 已有常驻约定块,不能重复设为 always 注入",
        )
    try:
        confidence = float(item.get("confidence", 1.0))
    except (TypeError, ValueError):
        return None, ("memory.invalid_confidence", "置信度必须在 0~1 之间")
    if not 0.0 <= confidence <= 1.0:
        return None, ("memory.invalid_confidence", "置信度必须在 0~1 之间")
    try:
        ttl_days = int(item.get("ttl_days") or 0)
    except (TypeError, ValueError):
        return None, ("memory.invalid_ttl", "ttl_days 不能为负数")
    if ttl_days < 0:
        return None, ("memory.invalid_ttl", "ttl_days 不能为负数")
    if reject_sensitive and find_sensitive(content) is not None:
        return None, (
            "memory.sensitive_rejected",
            "记忆内容疑似包含敏感凭据,已拒绝写入",
        )
    source_type = str(item.get("source_type") or "manual")
    if source_type not in MEMORY_SOURCE_TYPES:
        return None, ("memory.invalid_source", f"未知记忆来源: {source_type}")
    source_id = item.get("source_id")
    return (
        {
            "content": content,
            "kind": kind,
            "scope": scope,
            "workspace_id": workspace_id,
            "pinned": bool(item.get("pinned", False)),
            "ttl_days": ttl_days,
            "injection_mode": injection_mode,
            "confidence": confidence,
            "source_type": source_type,
            "source_id": str(source_id) if source_id else None,
        },
        None,
    )


async def import_memories(
    session: AsyncSession,
    account_id: str,
    items: Sequence[Mapping[str, Any]],
    *,
    dry_run: bool = False,
    max_content_chars: int = 8000,
    max_entries: int = 2000,
    dedupe_threshold: float = 0.9,
    reject_sensitive: bool = True,
) -> dict[str, Any]:
    """批量导入记忆(迁移/恢复/外部导入);近重复与非法条目分别计数。

    与普通写入的区别:导入是"补数据"而不是"表达新事实",所以不触发新旧替代
    (conflict_threshold=0),重复内容直接跳过;dry_run=True 时不落库,只估算
    会创建多少条、跳过多少条,便于前端先预览。
    """
    repository = MemoryRepository(session)
    existing = await repository.list_visible(
        account_id, include_invalid=False, limit=_IMPORT_SCAN_LIMIT
    )
    buckets: dict[tuple[str, str, str | None], list[str]] = {}
    for row in existing:
        buckets.setdefault(
            _import_dedupe_key(row.kind, row.scope, row.workspace_id), []
        ).append(row.content)

    received = len(items)
    created = 0
    skipped_duplicate = 0
    skipped_invalid = 0
    errors: list[dict[str, Any]] = []

    def _record_error(index: int, code: str, message: str) -> None:
        if len(errors) < _IMPORT_ERROR_LIMIT:
            errors.append({"index": index, "code": code, "message": message})

    for index, item in enumerate(items):
        fields, error = _validate_import_item(item, reject_sensitive=reject_sensitive)
        if error is not None or fields is None:
            skipped_invalid += 1
            if error is not None:
                _record_error(index, error[0], error[1])
            continue
        key = _import_dedupe_key(
            fields["kind"], fields["scope"], fields["workspace_id"]
        )
        content = fields["content"]
        if max_content_chars > 0 and len(content) > max_content_chars:
            content = content[:max_content_chars]
            fields["content"] = content
        if any(
            content_similarity(content, seen) >= dedupe_threshold
            for seen in buckets.get(key, [])
        ):
            skipped_duplicate += 1
            continue
        if dry_run:
            created += 1
            buckets.setdefault(key, []).append(content)
            continue
        try:
            memory = await write_memory(
                session,
                account_id,
                content=content,
                kind=fields["kind"],
                scope=fields["scope"],
                workspace_id=fields["workspace_id"],
                source_type=fields["source_type"],
                source_id=fields["source_id"],
                pinned=fields["pinned"],
                ttl_days=fields["ttl_days"],
                max_content_chars=max_content_chars,
                max_entries=max_entries,
                injection_mode=fields["injection_mode"],
                confidence=fields["confidence"],
                # 导入不触发替代:批量补数据不该把已有事实标成失效。
                conflict_threshold=0.0,
                reject_sensitive=reject_sensitive,
            )
        except AppError as exc:
            skipped_invalid += 1
            _record_error(index, exc.code, exc.message)
            continue
        created += 1
        buckets.setdefault(key, []).append(memory.content)

    if not dry_run and created:
        await session.flush()
        logger.info(
            "memories imported",
            account_id=account_id,
            received=received,
            created=created,
            skipped_duplicate=skipped_duplicate,
            skipped_invalid=skipped_invalid,
        )
    return {
        "received": received,
        "created": created,
        "skipped_duplicate": skipped_duplicate,
        "skipped_invalid": skipped_invalid,
        "dry_run": dry_run,
        "errors": errors,
    }
