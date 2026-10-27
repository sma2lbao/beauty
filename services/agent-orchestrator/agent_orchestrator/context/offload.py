"""大工具结果 offload:完整结果落沙箱工作区,消息流只留引用信封 + 预览。

设计见 `docs/design/context-engineering.md` §6.6。判定与降级:

- 关闭 / 未超阈值 / 取不到 ToolContext / 写沙箱失败 → **原样返回**(行为与改造前
  一致,offload 不该把工具结果弄丢);
- 成功 → 落 `/workspace/<offload_dir>/<run_id>/<tool>-<sha8>.txt`(按
  `offload_max_chars` 截断并在信封标 truncated),回填 JSON 引用信封;
- 模型需要细节时调用 `read_offloaded`(本模块构造的 ToolSpec),只允许读本策略
  offload 目录下的路径,分页返回。
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from dataclasses import dataclass
from typing import Any

from agent_orchestrator.context.policy import ContextPolicy
from agent_orchestrator.context.sandbox_files import (
    WORKSPACE_ROOT,
    WorkspaceFiles,
    scope_key_for,
)
from agent_orchestrator.services.tool_context import current_tool_context
from agent_orchestrator.services.tools import ToolRisk, ToolSpec

#: 引用信封里的落盘说明(模型据此决定是否回读)。
OFFLOAD_NOTE = "结果已完整落盘;需要细节时调用 read_offloaded(path=...) 分段读取。"
#: read_offloaded 一次返回的默认/最大字符数(防止一次把整份结果灌回上下文)。
DEFAULT_PAGE_CHARS = 4000
MAX_PAGE_CHARS = 20000
#: 文件名/路径段的安全化:只保留字母数字与 ._- (防止路径分隔符/穿越)。
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _error(message: str) -> str:
    """工具错误统一回喂 JSON(与 services/tools.py 的约定一致)。"""

    return json.dumps({"error": message}, ensure_ascii=False)


def _safe_component(value: str, *, fallback: str = "anonymous") -> str:
    """把 id/工具名安全化成单段路径组件(不含 /,长度受限)。"""

    cleaned = _UNSAFE.sub("-", str(value or "")).strip("-.")
    return cleaned[:64] or fallback


def _int_arg(args: dict[str, Any], key: str, default: int) -> int:
    value = args.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


@dataclass(frozen=True, slots=True)
class OffloadedOutput:
    """offload 结果:回填 tool 消息的内容 + 事件记录(未 offload 时 record 为 None)。"""

    content: str
    record: dict[str, Any] | None


class ToolOutputOffloader:
    """工具结果 offload 端口实现:落盘 + 回读(IO 全在上下文层,节点不碰)。"""

    def __init__(self, files: WorkspaceFiles, policy: ContextPolicy) -> None:
        self.files = files
        self.policy = policy

    @property
    def prefix(self) -> str:
        """本策略的 offload 目录(绝对路径,去掉首尾斜杠后拼到 /workspace 下)。"""

        relative = str(self.policy.offload_dir or "").strip().strip("/")
        return f"{WORKSPACE_ROOT}/{relative}" if relative else WORKSPACE_ROOT

    async def maybe_offload(self, tool_name: str, output: str) -> OffloadedOutput:
        """超阈值且上下文可用时落盘并返回引用信封;否则原样返回。"""

        if (
            not self.policy.offload_enabled
            or len(output) <= self.policy.offload_threshold_chars
        ):
            return OffloadedOutput(content=output, record=None)
        context = current_tool_context()
        if context is None or not context.tenant_id:
            return OffloadedOutput(content=output, record=None)
        scope_key = scope_key_for(
            context.tenant_id,
            conversation_id=context.conversation_id,
            run_id=context.run_id,
        )
        sha256 = hashlib.sha256(output.encode("utf-8")).hexdigest()
        segment = _safe_component(context.run_id or context.conversation_id)
        name = _safe_component(tool_name, fallback="tool")
        path = f"{self.prefix}/{segment}/{name}-{sha256[:8]}.txt"
        max_chars = max(0, self.policy.offload_max_chars)
        stored = output[:max_chars]
        truncated = len(output) > max_chars
        try:
            await self.files.write_bytes(
                context.tenant_id, scope_key, path, stored.encode("utf-8")
            )
        except Exception:  # noqa: BLE001 - 落盘失败降级为原样回填
            return OffloadedOutput(content=output, record=None)
        preview_chars = max(0, self.policy.offload_preview_chars)
        envelope = {
            "ok": True,
            "offloaded": True,
            "tool": tool_name,
            "path": path,
            "chars": len(output),
            "bytes": len(output.encode("utf-8")),
            "sha256": sha256,
            "truncated": truncated,
            "preview": output[:preview_chars],
            "note": OFFLOAD_NOTE,
        }
        return OffloadedOutput(
            content=json.dumps(envelope, ensure_ascii=False),
            record={
                "offloaded": True,
                "offload_path": path,
                "original_chars": len(output),
            },
        )

    async def read(
        self, path: str, *, offset: int = 0, limit: int = DEFAULT_PAGE_CHARS
    ) -> tuple[str, int, bool]:
        """分页读 offload 文件:返回 (片段, 总字符数, 是否还有下一段)。

        只允许本策略 offload 目录下的路径;越界/无上下文/文件缺失抛 ValueError,
        由 `read_offloaded` 工具兜成 JSON 错误。
        """

        normalized = posixpath.normpath(str(path or "").strip())
        if not normalized.startswith(self.prefix + "/"):
            raise ValueError(f"路径不在 offload 目录内: {path}")
        context = current_tool_context()
        if context is None or not context.tenant_id:
            raise ValueError("缺少工具上下文(租户),无法读取 offload 文件")
        scope_key = scope_key_for(
            context.tenant_id,
            conversation_id=context.conversation_id,
            run_id=context.run_id,
        )
        try:
            text = await self.files.read_text(context.tenant_id, scope_key, normalized)
        except Exception as exc:  # noqa: BLE001 - 统一翻译成 ValueError 由工具兜
            raise ValueError(f"读取 offload 文件失败: {exc}") from exc
        if text is None:
            raise ValueError(f"offload 文件不存在: {normalized}")
        start = max(0, offset)
        size = max(1, limit)
        chunk = text[start : start + size]
        has_more = start + len(chunk) < len(text)
        return chunk, len(text), has_more


def build_read_offloaded_tool(offloader: ToolOutputOffloader) -> ToolSpec:
    """构造 `read_offloaded` 工具:按 path 分页回读被 offload 的工具结果。"""

    async def read_offloaded(args: dict[str, Any]) -> str:
        context = current_tool_context()
        if context is None or not context.tenant_id:
            return _error("缺少工具上下文(租户),无法读取 offload 文件")
        raw_path = args.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return _error("参数 path 不能为空")
        offset = max(0, _int_arg(args, "offset", 0))
        limit = min(max(1, _int_arg(args, "limit", DEFAULT_PAGE_CHARS)), MAX_PAGE_CHARS)
        try:
            content, total_chars, truncated = await offloader.read(
                raw_path.strip(), offset=offset, limit=limit
            )
        except ValueError as exc:
            return _error(str(exc))
        except Exception as exc:  # noqa: BLE001 - 工具失败只回喂错误,不炸 run
            return _error(f"读取 offload 文件失败: {exc}")
        return json.dumps(
            {
                "path": raw_path.strip(),
                "offset": offset,
                "limit": limit,
                "content": content,
                "total_chars": total_chars,
                "truncated": truncated,
            },
            ensure_ascii=False,
        )

    return ToolSpec(
        name="read_offloaded",
        description=(
            "读取被 offload 的工具结果文件(分页)。工具结果返回 offloaded=true 时,"
            "用信封里的 path 调用本工具取回完整内容;offset 从 0 开始,limit 为本次"
            "最多返回字符数,返回的 truncated=true 表示还有后续内容,继续加大 offset "
            "才能读到。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "offload 文件路径(取自工具结果信封的 path)",
                },
                "offset": {
                    "type": "integer",
                    "default": 0,
                    "minimum": 0,
                    "description": "起始字符偏移,默认 0",
                },
                "limit": {
                    "type": "integer",
                    "default": DEFAULT_PAGE_CHARS,
                    "minimum": 1,
                    "description": f"本次最多返回字符数,默认 {DEFAULT_PAGE_CHARS}",
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        handler=read_offloaded,
        risk=ToolRisk.READ,
    )
