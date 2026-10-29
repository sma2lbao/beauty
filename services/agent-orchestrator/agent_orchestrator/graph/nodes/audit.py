"""工具调用审计与过程项上报:一次调用 → 审计记录 + item 相位(设计 §11.1/§4/§5)。

自 graph/nodes.py 拆出,行为与字段语义不变。
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any

from agent_orchestrator.graph import items
from agent_orchestrator.graph.ports import RISK_DANGER, OffloadPort
from agent_orchestrator.graph.runtime import GraphRuntime
from agent_orchestrator.graph.tool_results import (
    is_tool_error,
    parse_tool_error,
    tool_result_fields,
)

#: 审计记录 decision 值域:策略自动放行 / 人工确认后放行 / 未执行(拒绝)。
DECISION_AUTO = "auto"
DECISION_APPROVED = "approved"
DECISION_DENIED = "denied"

#: detail 里保留的结果原文长度(与 P0 一致,前端旧字段语义不变)。
DETAIL_CHARS = 200


def _elapsed_ms(started: float) -> int:
    """perf_counter 起点 -> 毫秒整数(审计不关心微秒)。"""

    return int((time.perf_counter() - started) * 1000)


def _record(
    name: str,
    arguments: str,
    output: str,
    *,
    tool_call_id: str | None = None,
    risk: str = RISK_DANGER,
    decision: str = DECISION_AUTO,
    duration_ms: int = 0,
    offload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """一次工具调用的审计记录(设计 §11.1)。

    P0 字段 name/arguments/ok/detail 语义不变;P1 扩充:

    - tool_call_id:模型的调用 id(tool-service 幂等键,缺省空串);
    - risk:read/write/danger;decision:auto/approved/denied;
    - error_code:从结果信封解析(parse_tool_error),成功为 None;
    - duration_ms:调用方用 perf_counter 计时;result_chars 是原始结果长度;
    - truncated/paths/sha256_before|after:来自成功信封(§7.2/§7.3),
      没有就缺省(False/[]/None),事件 payload 侧再按 16KB 上限裁剪;
    - offload:OffloadPort 的决策记录(offloaded/offload_path/original_chars),
      非空时原样并入(长结果被换成引用信封后,result_chars 是信封长度)。
    """

    fields = tool_result_fields(output)
    record: dict[str, Any] = {
        "name": name,
        "arguments": arguments,
        "ok": not is_tool_error(output),
        "detail": output[:DETAIL_CHARS],
        "tool_call_id": tool_call_id or "",
        "risk": risk,
        "decision": decision,
        "error_code": parse_tool_error(output),
        "duration_ms": duration_ms,
        "result_chars": len(output),
        "truncated": fields.get("truncated", False),
        "paths": fields.get("paths", []),
        "sha256_before": fields.get("sha256_before"),
        "sha256_after": fields.get("sha256_after"),
    }
    if offload:
        # offload 决策自带审计字段;graph 侧只负责并入,不解释其语义。
        record.update(dict(offload))
    return record


async def _maybe_offload(
    offloader: OffloadPort | None,
    tool_name: str,
    output: str,
) -> tuple[str, Mapping[str, Any] | None]:
    """按注入的 offload 口决定是否把大结果换成引用信封(§6.6)。

    未注入 offloader 时原样返回(与改造前完全一致);落盘、配额与失败降级
    都由 OffloadPort 实现负责,graph 侧不感知沙箱细节。
    """
    if offloader is None:
        return output, None
    decision = await offloader.maybe_offload(tool_name, output)
    return decision.content, decision.record


def _tool_error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


# ---------------------------------------------------------------- 过程项(P1-D)


def _tool_item_begin(
    runtime: GraphRuntime, name: str, arguments: str, *, node: str
) -> tuple[str, items.ItemSpan | None]:
    """上报工具项的 ``started`` 相位(§4/§5),返回 (kind, span) 供 finish 续用。

    没有 sink(直跑节点的单测)时 span 为 None,后续 finish/denied 全部 no-op。
    """

    kind = items.classify_tool_kind(name)
    span = runtime.items.begin(
        kind,
        items.tool_item_title(kind, name, arguments),
        node=node,
        data=items.tool_item_data(kind, name, arguments),
    )
    return kind, span


def _tool_item_finish(
    runtime: GraphRuntime,
    kind: str,
    name: str,
    arguments: str,
    span: items.ItemSpan | None,
    record: dict[str, Any],
) -> None:
    """上报 ``completed`` 相位:data 复用节点 _record 产出的审计字段(§4)。"""

    runtime.items.finish(span, data=items.tool_item_data(kind, name, arguments, record))


def _tool_item_denied(
    runtime: GraphRuntime,
    name: str,
    arguments: str,
    record: dict[str, Any],
    *,
    node: str,
) -> None:
    """未执行的调用(策略禁止/确认拒绝/参数非法):只落一条 completed(§5)。

    status=denied,不伪造 started 相位;data 里 decision/ok 如实保留。
    """

    kind = items.classify_tool_kind(name)
    runtime.items.record(
        kind,
        items.tool_item_title(kind, name, arguments),
        status=items.STATUS_DENIED,
        node=node,
        data=items.tool_item_data(kind, name, arguments, record),
    )


def _tool_item_fail(
    runtime: GraphRuntime,
    span: items.ItemSpan | None,
    name: str,
    arguments: str,
    exc: Exception,
) -> None:
    """执行抛异常:把 started 项收成 failed 相位再向上抛(run.failed 由边界补)。"""

    runtime.items.finish(
        span,
        status=items.STATUS_FAILED,
        data={
            "name": name,
            "arguments": arguments,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        },
    )


# ---------------------------------------------------------------- 交互提问(§10)
