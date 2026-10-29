"""参数摘要与能力开关:幂等键、工具可用性、exec 的 cwd 夹取。"""

from __future__ import annotations

import hashlib
import json
import posixpath
from typing import Any

from tool_service.constants import MAX_EXEC_CWD_CHARS
from tool_service.core.config import Settings
from tool_service.services.helpers import WORKSPACE_ROOT


def arguments_digest(arguments: dict[str, Any]) -> str:
    """参数摘要:用于幂等键冲突判定(排序键 + 紧凑分隔符)。"""

    canonical = json.dumps(
        arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---- 能力开关与 exec 辅助 ------------------------------------------------


def is_available(name: str, settings: Settings) -> bool:
    """能力即权限边界:开关关闭的工具在目录、详情与执行三处一致地"不存在"。"""

    if name == "fetch":
        return bool(settings.fetch_enabled)
    if name == "exec":
        return bool(settings.exec_enabled)
    return True


def resolve_exec_cwd(raw: Any, *, workspace_root: str = WORKSPACE_ROOT) -> str | None:
    """把 exec 的 cwd 夹到工作区内;越界或非法返回 None。"""

    if raw is None:
        return workspace_root
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return workspace_root
    if len(text) > MAX_EXEC_CWD_CHARS or chr(0) in text or chr(92) in text:
        return None
    candidate = text if text.startswith("/") else posixpath.join(workspace_root, text)
    normalized = posixpath.normpath(candidate)
    if normalized == workspace_root or normalized.startswith(workspace_root + "/"):
        return normalized
    return None
