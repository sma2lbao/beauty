"""图节点过程项采集(P1-D 事件分类学;契约 docs/design/event-taxonomy.md)。

item 是 run 内的原子过程项(plan / tool / file_change / mcp_call / search /
todo),自带 ``started`` → ``completed`` 生命周期。节点是纯函数、不落库:这里
只把过程项上报给 services 层的 sink,由 ``graph.ainvoke`` 边界统一落成
``run.item.v1`` 事件(零迁移,写 ``run_events``)。

分类规则集中在 :func:`classify_tool_kind`(纯函数,可单测);标题/数据组装也
集中在本模块,避免各节点重复拼 payload。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

#: 过程项事件类型:payload 是 ItemSpan 的相位快照;投影见 services.run_items。
EVENT_TYPE = "run.item.v1"

PHASE_STARTED = "started"
PHASE_DELTA = "delta"
PHASE_COMPLETED = "completed"
PHASE_FAILED = "failed"

STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_DENIED = "denied"

KIND_PLAN = "plan"
KIND_TOOL = "tool"
KIND_FILE_CHANGE = "file_change"
KIND_MCP_CALL = "mcp_call"
KIND_SEARCH = "search"
KIND_TODO = "todo"

#: kind 值域(§4):顺序与契约表格一致,前端按下标渲染。
KINDS: tuple[str, ...] = (
    KIND_PLAN,
    KIND_TOOL,
    KIND_FILE_CHANGE,
    KIND_MCP_CALL,
    KIND_SEARCH,
    KIND_TODO,
)

#: phase 值域(§5)。
PHASES: tuple[str, ...] = (PHASE_STARTED, PHASE_DELTA, PHASE_COMPLETED, PHASE_FAILED)

MCP_TOOL_PREFIX = "mcp__"
FILE_CHANGE_TOOLS = frozenset({"write", "edit"})
SEARCH_TOOLS = frozenset({"fetch", "search_knowledge"})
TODO_TOOL_NAME = "todo_write"


def classify_tool_kind(name: str) -> str:
    """工具名 → item kind(§4 分类规则,纯函数)。

    规则顺序即优先级:``todo_write`` → todo;``mcp__*`` → mcp_call;
    ``write``/``edit`` → file_change;``fetch``/``search_knowledge`` → search;
    其余(read/glob/grep/list_dir/exec/ask_question…)→ tool。
    """

    if name == TODO_TOOL_NAME:
        return KIND_TODO
    if name.startswith(MCP_TOOL_PREFIX):
        return KIND_MCP_CALL
    if name in FILE_CHANGE_TOOLS:
        return KIND_FILE_CHANGE
    if name in SEARCH_TOOLS:
        return KIND_SEARCH
    return KIND_TOOL


def _arguments_dict(arguments: str) -> dict[str, Any]:
    """工具参数是模型给的 JSON 字符串:解析失败按空对象处理(只用于标题)。"""

    try:
        parsed = json.loads(arguments) if arguments else {}
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def tool_item_title(kind: str, name: str, arguments: str) -> str:
    """工具项的短标题(前端直接显示,如 ``写入 src/a.ts``、``检索:fastapi``)。"""

    args = _arguments_dict(arguments)
    if kind == KIND_TODO:
        return "更新任务清单"
    if kind == KIND_FILE_CHANGE:
        path = args.get("path") or args.get("file_path")
        verb = "写入" if name == "write" else "编辑"
        return f"{verb} {path}" if path else f"{verb}文件"
    if kind == KIND_SEARCH:
        query = args.get("query") or args.get("url") or args.get("text")
        return f"检索:{query}" if query else f"检索:{name}"
    if kind == KIND_MCP_CALL:
        parts = name.split("__")
        tool = parts[-1] if len(parts) > 2 else name
        return f"MCP:{tool}"
    return f"调用 {name}"


def tool_item_data(
    kind: str,
    name: str,
    arguments: str,
    record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """工具项的 data(§4):started 相位只带入参,completed 相位复用审计记录。"""

    if record is None:
        return {"name": name, "arguments": arguments}
    data = dict(record)
    if kind == KIND_MCP_CALL:
        parts = name.split("__")
        data["server"] = parts[1] if len(parts) > 2 else ""
        data["tool"] = parts[-1] if len(parts) > 2 else name
    return data


class ItemSink(Protocol):
    """span 接收端(services 层实现为内存收集器,调用边界统一落库)。"""

    def push(self, span: ItemSpan) -> None: ...


@dataclass(slots=True)
class ItemSpan:
    """一个过程项的生命周期片段:kind + 标题 + 相位 + 起止 + 结果数据。

    同一个对象先以 ``started`` 相位上报、结束时改相位后再上报一次:sink 在
    ``push`` 时做快照,因此两次上报是不同的相位记录,而 ``item_id`` 由 sink
    在落库时统一分配(plan 版本化由调用方显式给 ``item_id``)。
    """

    kind: str
    title: str
    started_at: datetime
    phase: str = PHASE_STARTED
    item_id: str | None = None
    node: str | None = None
    step_id: str | None = None
    status: str | None = None
    finished_at: datetime | None = None
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> int | None:
        if self.finished_at is None:
            return None
        return int((self.finished_at - self.started_at).total_seconds() * 1000)


#: 哨兵:step_id 未显式传入时取「当前步骤」(与显式传 None 区分)。
_UNSET: object = object()


class ItemProjector:
    """一次图调用的过程项采集器:没有 sink(直跑节点的单测)时全部 no-op。"""

    def __init__(
        self,
        sink: ItemSink | None = None,
        *,
        step_id: Callable[[], str | None] | None = None,
    ) -> None:
        self._sink = sink
        self._step_id = step_id

    def active(self) -> bool:
        return self._sink is not None

    def current_step_id(self) -> str | None:
        """当前步骤 id(来自 StepProjector;取不到为 None)。"""

        if self._step_id is None:
            return None
        return self._step_id()

    def _resolve_step_id(self, step_id: Any) -> str | None:
        return self.current_step_id() if step_id is _UNSET else step_id

    def begin(
        self,
        kind: str,
        title: str,
        *,
        node: str | None = None,
        item_id: str | None = None,
        step_id: Any = _UNSET,
        data: dict[str, Any] | None = None,
        started_at: datetime | None = None,
    ) -> ItemSpan | None:
        """上报 ``started`` 相位(项开始);返回 span 供 :meth:`finish` 续用。"""

        if self._sink is None:
            return None
        span = ItemSpan(
            kind=kind,
            title=title,
            started_at=started_at or datetime.now(UTC),
            item_id=item_id,
            node=node,
            step_id=self._resolve_step_id(step_id),
            data=dict(data or {}),
        )
        self._sink.push(span)
        return span

    def finish(
        self,
        span: ItemSpan | None,
        status: str = STATUS_COMPLETED,
        *,
        data: dict[str, Any] | None = None,
    ) -> None:
        """上报终态相位(``completed``;``status=failed`` 时相位是 ``failed``)。"""

        if self._sink is None or span is None:
            return
        if data:
            span.data = {**span.data, **data}
        span.status = status
        span.phase = PHASE_FAILED if status == STATUS_FAILED else PHASE_COMPLETED
        span.finished_at = datetime.now(UTC)
        self._sink.push(span)

    def record(
        self,
        kind: str,
        title: str,
        *,
        status: str = STATUS_COMPLETED,
        data: dict[str, Any] | None = None,
        node: str | None = None,
        item_id: str | None = None,
        step_id: Any = _UNSET,
        started_at: datetime | None = None,
    ) -> ItemSpan | None:
        """只上报一条终态相位(denied 等「未执行」项,不伪造 started)。"""

        if self._sink is None:
            return None
        now = datetime.now(UTC)
        span = ItemSpan(
            kind=kind,
            title=title,
            started_at=started_at or now,
            phase=PHASE_FAILED if status == STATUS_FAILED else PHASE_COMPLETED,
            item_id=item_id,
            node=node,
            step_id=self._resolve_step_id(step_id),
            status=status,
            finished_at=now,
            data=dict(data or {}),
        )
        self._sink.push(span)
        return span
