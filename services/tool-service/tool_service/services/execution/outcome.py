"""结果与审计封装:失败信封、交互工具归一、tool_calls 审计列映射。"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from common.tools import ToolDescriptor, ToolError, ToolExecuteResponse

from tool_service.repositories import WRITE_TOOL_NAMES
from tool_service.services.execution.arguments import arguments_digest
from tool_service.services.execution.limits import (
    ARGUMENTS_AUDIT_CHARS,
    MAX_AUDIT_PATHS,
    PATCH_FIELDS,
)
from tool_service.services.execution.models import (
    ExecutionContext,
    ExecutionOutcome,
    PatchAudit,
)


def normalize_interaction(arguments: dict[str, Any]) -> dict[str, Any]:
    """把交互工具参数归一成 orchestrator 可直接建中断的形状(设计 §8.3)。

    只做字段归一与默认值补齐(allow_custom 默认 true、multi_select 默认
    false),不做任何副作用;schema 校验已在上游完成。
    """

    questions: list[dict[str, Any]] = []
    for item in arguments.get("questions") or []:
        question: dict[str, Any] = {"question": item["question"]}
        header = item.get("header")
        if header:
            question["header"] = header
        options = item.get("options")
        if options:
            question["options"] = [
                {
                    "label": option["label"],
                    **(
                        {"description": option["description"]}
                        if option.get("description")
                        else {}
                    ),
                }
                for option in options
            ]
        question["multi_select"] = bool(item.get("multi_select", False))
        questions.append(question)
    return {
        "kind": "interaction",
        "interaction": {"type": "question"},
        "normalized": {
            "questions": questions,
            "allow_custom": bool(arguments.get("allow_custom", True)),
        },
    }


def failure_outcome(
    descriptor: ToolDescriptor,
    context: ExecutionContext,
    code: str,
    message: str,
    *,
    hint: str = "",
    retryable: bool = False,
    duration_ms: int = 0,
    sandbox_id: str | None = None,
) -> ExecutionOutcome:
    """构造失败信封;API 层的读前置/配额检查在执行前也复用它。"""

    return ExecutionOutcome(
        descriptor=descriptor,
        response=ToolExecuteResponse(
            ok=False,
            error=ToolError(code=code, message=message, hint=hint, retryable=retryable),
            duration_ms=duration_ms,
            tool_call_id=context.tool_call_id,
        ),
        sandbox_id=sandbox_id,
    )


def audit_columns(
    outcome: ExecutionOutcome,
    context: ExecutionContext,
    arguments: dict[str, Any],
    *,
    decision: str,
) -> dict[str, Any]:
    """把执行结果映射成 tool_calls 表的列(调用方负责 insert)。"""

    response = outcome.response
    result = response.result or {}
    error = response.error
    patch = outcome.patch
    return {
        "tool_name": outcome.descriptor.name,
        "tool_version": outcome.descriptor.version,
        "risk": str(outcome.descriptor.risk),
        "decision": decision,
        "arguments": json.dumps(arguments, ensure_ascii=False, sort_keys=True)[
            :ARGUMENTS_AUDIT_CHARS
        ],
        "arguments_sha256": arguments_digest(arguments),
        "ok": response.ok,
        "error_code": error.code if error is not None else None,
        "duration_ms": response.duration_ms,
        "result_chars": _result_chars_str(result),
        "bytes_written": _bytes_written(outcome, result),
        "truncated": response.truncated,
        "paths": json.dumps(_result_paths(result), ensure_ascii=False),
        "sha256_before": _optional_str(result.get("sha256_before")),
        "sha256_after": _optional_str(result.get("sha256_after")),
        "patch": patch.patch if patch is not None else None,
        "lines_added": patch.lines_added if patch is not None else None,
        "lines_removed": patch.lines_removed if patch is not None else None,
        "patch_truncated": patch.patch_truncated if patch is not None else False,
        "sandbox_id": outcome.sandbox_id,
    }


def _bytes_written(outcome: ExecutionOutcome, result: dict[str, Any]) -> int:
    """只有成功的写工具才累计写配额字节。"""

    if not outcome.response.ok or outcome.descriptor.name not in WRITE_TOOL_NAMES:
        return 0
    value = result.get("bytes")
    return int(value) if isinstance(value, int) else 0


def _result_chars_str(result: dict[str, Any]) -> int:
    return len(json.dumps(result, ensure_ascii=False)) if result else 0


def _result_paths(result: dict[str, Any]) -> list[str]:
    """从结果里提取文件路径(审计展示用,最多 MAX_AUDIT_PATHS 条)。"""

    raw: list[str] = []
    path = result.get("path")
    if isinstance(path, str):
        raw.append(path)
    for key in ("entries", "matches"):
        values = result.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, str):
                raw.append(item)
            elif isinstance(item, dict) and isinstance(item.get("path"), str):
                raw.append(item["path"])
            if len(raw) >= MAX_AUDIT_PATHS:
                break
    return raw[:MAX_AUDIT_PATHS]


def _split_patch(
    response: ToolExecuteResponse,
) -> tuple[PatchAudit | None, ToolExecuteResponse]:
    """把写工具的补丁字段从结果信封里剥离,返回(补丁审计, 干净信封)。

    编排器会把 tool result 原样喂回模型,补丁可能上万字符,必须只留在
    tool-service 的审计表里。
    """

    result = response.result
    if not response.ok or not result:
        return None, response
    if not any(field in result for field in PATCH_FIELDS):
        return None, response
    patch = PatchAudit(
        patch=_optional_str(result.get("patch")),
        lines_added=_optional_int(result.get("lines_added")),
        lines_removed=_optional_int(result.get("lines_removed")),
        patch_truncated=bool(result.get("patch_truncated")),
    )
    cleaned = {key: value for key, value in result.items() if key not in PATCH_FIELDS}
    return patch, replace(response, result=cleaned)


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None
