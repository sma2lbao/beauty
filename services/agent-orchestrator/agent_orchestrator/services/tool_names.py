"""历史工具名归一(P2 下线旧进程内代码工具的兼容层)。

P2 把进程内 `code_*` 代码工具迁到 tool-service 后,历史 `Run.skill_tools` 与
技能包快照里仍可能出现旧名。这里只做最小映射,**仅用于读取历史快照的边界**
(见 `services/run_execution.py` 的 `_allowed_tools_from_snapshot`),
不进 catalog、不进执行路径;P3 删除。
"""

from __future__ import annotations

from collections.abc import Iterable

#: 旧进程内代码工具名 → tool-service 正式工具名。
LEGACY_TOOL_NAMES: dict[str, str] = {
    "code_read_file": "read",
    "code_write_file": "write",
    "code_edit_file": "edit",
    "code_search_text": "grep",
    "code_list_files": "glob",
}


def normalize_tool_names(names: Iterable[str] | None) -> list[str]:
    """把历史快照里的工具名归一到正式名:去重保序,未命中原样保留。

    `None`/空表返回空表。注意调用方需自行保留快照缺省(`None`=不限制)与
    空表(`[]`=不用工具)的差异,本函数只负责非 `None` 列表的归一。
    """
    if not names:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for name in names:
        canonical = LEGACY_TOOL_NAMES.get(name, name)
        if canonical in seen:
            continue
        seen.add(canonical)
        normalized.append(canonical)
    return normalized
