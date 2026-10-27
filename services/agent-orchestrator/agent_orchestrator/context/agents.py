"""从沙箱工作区发现并注入 AGENTS.md(工作区规则)。

设计见 `docs/design/context-engineering.md` §6.4。要点:

- 文件真源是**沙箱 /workspace**(租户/对话由 scope_key 隔离),不读宿主文件系统;
- 根 → 深、同层路径字典序;最多 `agents_md_max_files` 个;单文件按
  `agents_md_file_max_chars` 截断,合计按 `agents_md_max_chars` 截断;
- 读取失败/文件缺失跳过(不阻断 run);没有文档时 provider 返回 None,
  管线记为 `empty`。
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import ClassVar

from agent_orchestrator.context.pipeline import ContextRequest, ContextSection
from agent_orchestrator.context.policy import SECTION_AGENTS_MD, ContextPolicy
from agent_orchestrator.context.sandbox_files import (
    WORKSPACE_ROOT,
    WorkspaceFiles,
    scope_key_for,
)

#: 注入段标题(渲染成 "## 工作区规则(AGENTS.md)")。
SECTION_TITLE = "工作区规则(AGENTS.md)"
#: 超总预算时的说明(固定长度,便于把正文严格控制在预算内)。
BUDGET_NOTE = "\n\n(内容超出总预算,已截断;完整内容请读取工作区文件。)"


@dataclass(frozen=True, slots=True)
class AgentsDocument:
    """一个已读取的 AGENTS.md(单文件已按预算截断)。"""

    path: str
    content: str
    original_chars: int
    truncated: bool


def _relative_path(path: str) -> str | None:
    """`/workspace` 下的相对路径;不在工作区内返回 None。"""

    prefix = WORKSPACE_ROOT + "/"
    if not path.startswith(prefix):
        return None
    relative = path[len(prefix) :]
    return relative or None


async def discover_agents_documents(
    files: WorkspaceFiles,
    policy: ContextPolicy,
    *,
    tenant_id: str,
    scope_key: str,
) -> list[AgentsDocument]:
    """发现并读取 AGENTS.md:浅→深、同层字典序,应用数量与单文件预算。"""

    if not policy.agents_md_enabled:
        return []
    names = tuple(
        name for name in (str(item).strip() for item in policy.agents_md_names) if name
    )
    if not names:
        return []
    paths = await files.find(
        tenant_id,
        scope_key,
        names,
        max_depth=policy.agents_md_max_depth,
    )
    max_depth = max(0, policy.agents_md_max_depth)
    found: list[tuple[int, str, str]] = []
    seen: set[str] = set()
    for raw in paths:
        path = str(raw).strip()
        relative = _relative_path(path)
        if relative is None or path in seen:
            continue
        if posixpath.basename(relative) not in names:
            continue
        depth = relative.count("/")
        if depth > max_depth:
            continue
        seen.add(path)
        found.append((depth, relative, path))
    found.sort(key=lambda item: (item[0], item[1]))
    max_files = max(0, policy.agents_md_max_files)
    file_limit = max(0, policy.agents_md_file_max_chars)
    documents: list[AgentsDocument] = []
    for _depth, _relative, path in found[:max_files]:
        try:
            text = await files.read_text(tenant_id, scope_key, path)
        except Exception:  # noqa: BLE001 - 单个文件读取失败只跳过它
            continue
        if text is None:
            continue
        documents.append(
            AgentsDocument(
                path=path,
                content=text[:file_limit],
                original_chars=len(text),
                truncated=len(text) > file_limit,
            )
        )
    return documents


def _render_documents(documents: list[AgentsDocument], budget: int) -> tuple[str, bool]:
    """按总预算拼接文档;超限时丢弃后续文件并在末尾注明。"""

    blocks = [f"### {document.path}\n{document.content}" for document in documents]
    truncated = any(document.truncated for document in documents)
    joined = "\n\n".join(blocks)
    if budget <= 0:
        return "", True
    if len(joined) <= budget:
        return joined, truncated
    if budget <= len(BUDGET_NOTE):
        return joined[:budget], True
    limit = budget - len(BUDGET_NOTE)
    kept: list[str] = []
    used = 0
    for block in blocks:
        extra = len(block) + (2 if kept else 0)
        if used + extra > limit:
            break
        kept.append(block)
        used += extra
    return "\n\n".join(kept) + BUDGET_NOTE, True


class AgentsMdProvider:
    """`agents_md` 段提供者:沙箱工作区 AGENTS.md → ContextSection。"""

    section_id: ClassVar[str] = SECTION_AGENTS_MD

    def __init__(self, files: WorkspaceFiles, policy: ContextPolicy) -> None:
        self.files = files
        self.policy = policy

    async def build(self, request: ContextRequest) -> ContextSection | None:
        """按请求的租户/对话/run 算 scope_key,发现文档并渲染成一段。"""

        if not self.policy.agents_md_enabled:
            return None
        scope_key = scope_key_for(
            request.tenant_id,
            conversation_id=request.conversation_id,
            run_id=request.run_id,
        )
        documents = await discover_agents_documents(
            self.files,
            self.policy,
            tenant_id=request.tenant_id,
            scope_key=scope_key,
        )
        if not documents:
            return None
        content, truncated = _render_documents(
            documents, max(0, self.policy.agents_md_max_chars)
        )
        if not content.strip():
            return None
        source = ",".join(
            _relative_path(document.path) or document.path for document in documents
        )
        return ContextSection(
            id=self.section_id,
            title=SECTION_TITLE,
            content=content,
            source=source,
            truncated=truncated,
        )
