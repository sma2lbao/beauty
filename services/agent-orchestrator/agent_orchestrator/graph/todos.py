"""任务清单的纯逻辑(M4):校验、统计与提示块渲染(设计见 §13)。

读写都在 services 层(`runs.todos` 是快照真源,变更落 `run.todo.updated.v1`);
本模块只放无副作用的纯函数,供 graph 节点与工具共用:

- 节点是纯函数(架构 §4),execute 从工具结果回写 `state["todos"]`,
  verify/deliver 只读同一份;
- 工具入参用 `validate_todos` 做防御:非法参数回 JSON 错误,不炸掉整个 run;
- 注入块是**替换式**:每轮消息重建时用最新 state 渲染,历史消息里不累积
  (否则清单随轮次重复增长,反而吃掉上下文)。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

#: 工具名:发布后即权限键(审批策略按名字匹配),改名等于破坏租户配置。
#: 定义在 graph 层:节点要按名字认出工具结果并回写 state,services 层
#: 从这一处取用(单一来源,避免两层各写一遍字符串)。
TODO_TOOL_NAME = "todo_write"
#: 三态清单:进度可见、状态可收敛(不做阻塞/取消等更多态,够用且好解释)。
TODO_STATUSES = ("pending", "in_progress", "completed")
#: 注入块里的极简标记:不塞 JSON,省 token 也让模型不容易整段照抄回来。
_STATUS_MARKS = {"completed": "[x]", "in_progress": "[~]", "pending": "[ ]"}


def todo_counts(todos: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """按状态计数:固定三个键(缺的记 0),工具返回与 API 读端共用。"""

    counts = {status: 0 for status in TODO_STATUSES}
    for item in todos:
        status = str(item.get("status") or "")
        if status in counts:
            counts[status] += 1
    return counts


def sanitize_todos(raw: Any) -> list[dict[str, str]]:
    """宽松归一:丢掉形状不对的项、保留原顺序(读快照/解析工具结果用)。

    读路径上不能因为一条脏数据整体失败(旧 checkpoint、手改过的行、
    被截断的工具结果都会走到这里),所以这里只做"能认的多留"的清洗。
    """

    if not isinstance(raw, list):
        return []
    todos: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        content = item.get("content")
        status = item.get("status")
        if not isinstance(content, str) or not content.strip():
            continue
        if status not in TODO_STATUSES:
            continue
        todos.append({"content": content.strip(), "status": str(status)})
    return todos


def validate_todos(
    raw: Any, *, max_items: int, max_content_chars: int
) -> list[dict[str, str]]:
    """严格校验 `todo_write` 入参:非法抛 ValueError(消息直接回喂模型)。

    错误消息要能指导模型自己改对(第几项、错在哪),而不是笼统的"参数错误"。
    """

    if not isinstance(raw, list):
        raise ValueError("参数 todos 必须是数组")
    if not raw:
        raise ValueError("清单不能为空:一次传完整清单(至少 1 项)")
    if len(raw) > max_items:
        raise ValueError(f"清单最多 {max_items} 项,当前 {len(raw)} 项")
    todos: list[dict[str, str]] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"第 {index} 项必须是对象")
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"第 {index} 项 content 不能为空")
        content = content.strip()
        if len(content) > max_content_chars:
            raise ValueError(f"第 {index} 项 content 超过 {max_content_chars} 字符")
        status = item.get("status")
        if status not in TODO_STATUSES:
            raise ValueError(
                f"第 {index} 项 status 必须是 "
                f"{'/'.join(TODO_STATUSES)} 之一,当前 {status!r}"
            )
        todos.append({"content": content, "status": str(status)})
    running = sum(1 for todo in todos if todo["status"] == "in_progress")
    if running > 1:
        raise ValueError(f"同时最多一项 in_progress,当前 {running} 项")
    return todos


def render_todo_block(todos: Any) -> str:
    """渲染注入块(空清单返回空串):首行给进度,每项一行极简标记。"""

    items = sanitize_todos(todos)
    if not items:
        return ""
    counts = todo_counts(items)
    lines = [f"当前任务清单({counts['completed']}/{len(items)} 完成):"]
    lines.extend(
        f"- {_STATUS_MARKS[todo['status']]} {todo['content']}" for todo in items
    )
    return "\n".join(lines)
