"""子代理参数校验、工具集裁剪与深度/配额闸(自 subagents.py 拆出)。

契约见 ``docs/design/subagents.md`` §5/§6:工具白名单只收窄、深度与每 run
子代理数有硬上限,越界一律转成 ``subagent.*`` 错误信封回喂模型。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.core.config import Settings
from agent_orchestrator.models.runs import PARENT_KIND_SUBAGENT, Run
from agent_orchestrator.repositories import RunRepository
from agent_orchestrator.services.subagents_spec import (
    DEFAULT_AGENT_NAME,
    MAX_AGENT_NAME_CHARS,
    MAX_INSTRUCTIONS_CHARS,
    MAX_TIMEOUT_SECONDS,
    MAX_TITLE_CHARS,
    MAX_WALK_DEPTH,
    MIN_TIMEOUT_SECONDS,
    SPAWN_TOOL_NAME,
)
from agent_orchestrator.services.tool_names import normalize_tool_names


@dataclass(frozen=True)
class _Request:
    """校验后的 spawn 参数(长度/范围都已在解析时收敛)。"""

    title: str
    task: str
    agent_name: str
    instructions: str | None
    tools: list[str] | None
    timeout_seconds: float


class _Rejected(Exception):
    """参数/上限校验失败:转成 ``subagent.*`` 工具结果回喂模型。"""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _parse(params: dict[str, Any], *, settings: Settings) -> _Request:
    """工具参数校验(长度/枚举/范围越界一律 invalid_arguments,§5)。"""

    title = _required_text(params.get("title"), field="title")
    if len(title) > MAX_TITLE_CHARS:
        raise _Rejected(
            "subagent.invalid_arguments", f"title 不能超过 {MAX_TITLE_CHARS} 字"
        )
    task = _required_text(params.get("task"), field="task")
    if len(task) > settings.subagent_task_max_chars:
        raise _Rejected(
            "subagent.invalid_arguments",
            f"task 不能超过 {settings.subagent_task_max_chars} 字",
        )

    raw_name = params.get("agent_name")
    if raw_name is None:
        agent_name = DEFAULT_AGENT_NAME
    elif isinstance(raw_name, str):
        agent_name = raw_name.strip() or DEFAULT_AGENT_NAME
        if len(agent_name) > MAX_AGENT_NAME_CHARS:
            raise _Rejected(
                "subagent.invalid_arguments",
                f"agent_name 不能超过 {MAX_AGENT_NAME_CHARS} 字",
            )
    else:
        raise _Rejected("subagent.invalid_arguments", "agent_name 必须是字符串")

    raw_instructions = params.get("instructions")
    instructions: str | None = None
    if raw_instructions is not None:
        if not isinstance(raw_instructions, str):
            raise _Rejected("subagent.invalid_arguments", "instructions 必须是字符串")
        instructions = raw_instructions.strip() or None
        if instructions is not None and len(instructions) > MAX_INSTRUCTIONS_CHARS:
            raise _Rejected(
                "subagent.invalid_arguments",
                f"instructions 不能超过 {MAX_INSTRUCTIONS_CHARS} 字",
            )

    tools = _parse_tools(params.get("tools"), settings=settings)
    timeout = _parse_timeout(params.get("timeout_seconds"), settings=settings)
    return _Request(
        title=title,
        task=task,
        agent_name=agent_name,
        instructions=instructions,
        tools=tools,
        timeout_seconds=timeout,
    )


def _parse_tools(raw: Any, *, settings: Settings) -> list[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise _Rejected("subagent.invalid_arguments", "tools 必须是字符串数组")
    # 剔除自身防递归(契约 §5):即使父白名单里意外带了也不允许再下放。
    tools = normalize_tool_names([item.strip() for item in raw if item.strip()])
    tools = [name for name in tools if name != SPAWN_TOOL_NAME]
    if len(tools) > settings.subagent_max_tools:
        raise _Rejected(
            "subagent.invalid_arguments",
            f"tools 最多 {settings.subagent_max_tools} 个",
        )
    return tools


def _parse_timeout(raw: Any, *, settings: Settings) -> float:
    if raw is None:
        value = float(settings.subagent_timeout_seconds)
    else:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise _Rejected("subagent.invalid_arguments", "timeout_seconds 必须是数字")
        value = float(raw)
        if not MIN_TIMEOUT_SECONDS <= value <= MAX_TIMEOUT_SECONDS:
            raise _Rejected(
                "subagent.invalid_arguments",
                f"timeout_seconds 必须在 {MIN_TIMEOUT_SECONDS:.0f}.."
                f"{MAX_TIMEOUT_SECONDS:.0f} 之间",
            )
    # 配置值也走 clamp(契约 §6 的 effective 语义),避免环境变量把超时放开。
    return min(max(value, MIN_TIMEOUT_SECONDS), MAX_TIMEOUT_SECONDS)


def _required_text(raw: Any, *, field: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise _Rejected("subagent.invalid_arguments", f"{field} 必填且必须是非空字符串")
    return raw.strip()


def _parent_tools(parent: Run) -> list[str] | None:
    """父级有效白名单:``agent_tools ?? skill_tools ?? null``(§5)。

    继承时同样剔除 ``spawn_subagent`` 自身:白名单里意外带了这个名字时
    也不该把「继续派生」的能力下放给子代理(深度闸是第二道防线)。
    """

    if parent.agent_tools is not None:
        return _strip_self(normalize_tool_names(parent.agent_tools))
    if parent.skill_tools is not None:
        return _strip_self(normalize_tool_names(parent.skill_tools))
    return None


def _strip_self(names: list[str]) -> list[str]:
    return [name for name in names if name != SPAWN_TOOL_NAME]


def _resolve_tools(request: _Request, parent: Run) -> list[str] | None:
    """子代理工具面:只收窄;未传则原样继承父级有效白名单(可为 None)。"""

    parent_tools = _parent_tools(parent)
    if request.tools is None:
        return parent_tools
    if parent_tools is not None:
        allowed = set(parent_tools)
        extra = [name for name in request.tools if name not in allowed]
        if extra:
            raise _Rejected(
                "subagent.tools_not_subset",
                "以下工具不在父级白名单内:" + ", ".join(extra),
            )
    return request.tools


async def _check_limits(session: AsyncSession, parent: Run, settings: Settings) -> None:
    depth = await _subagent_depth(session, parent)
    if depth + 1 > settings.subagent_max_depth:
        raise _Rejected(
            "subagent.depth_exceeded",
            f"子代理最多嵌套 {settings.subagent_max_depth} 层",
        )
    used = await RunRepository(session).count(
        Run.tenant_id == parent.tenant_id,
        Run.parent_run_id == parent.id,
        Run.parent_kind == PARENT_KIND_SUBAGENT,
    )
    if used >= settings.subagent_max_per_run:
        raise _Rejected(
            "subagent.limit_exceeded",
            f"每个 run 最多 {settings.subagent_max_per_run} 个子代理",
        )


async def _subagent_depth(session: AsyncSession, run: Run) -> int:
    """当前 run 的子代理层数:顶层=0,子代理再派生=1(用于深度上限判定)。"""

    depth = 0
    current = run
    while current.parent_kind == PARENT_KIND_SUBAGENT and current.parent_run_id:
        if depth >= MAX_WALK_DEPTH:
            break
        parent = await session.get(Run, current.parent_run_id)
        if parent is None:
            break
        depth += 1
        current = parent
    return depth
