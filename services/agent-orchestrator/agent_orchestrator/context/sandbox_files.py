"""沙箱工作区文件访问:与 tool-service 完全一致的 scope_key 规则。

设计见 `docs/design/context-engineering.md` §6.3。AGENTS.md 发现与大工具结果
offload 都走这里,保证与工具看到的是**同一个工作区**:

- 复用粒度 `conversation:<id>` > `run:<id>` > `tenant:<id>`,与
  `services/tool-service/tool_service/services/sandbox.py` 的 `scope_key_for`
  逐字对齐(跨服务靠约定 + 单测固定);
- 进程内缓存 `(tenant_id, scope_key) -> sandbox_id`,避免每个文件操作都打一次
  ensure;多副本一致性仍由 workspace-service 的 (tenant, scope) 唯一约束兜底;
- 读文件失败一律降级为 None(上下文注入不该阻断 run);写失败抛异常,由调用方
  (offload)决定是否降级;
- find 只走沙箱 exec(不在宿主读文件),且只返回 `/workspace` 下的绝对路径。
"""

from __future__ import annotations

import posixpath
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from common.clients.sandbox import Sandbox

#: scope_key 前缀:对话级优先,其次 run 级,最后租户级兜底。
SCOPE_CONVERSATION_PREFIX = "conversation:"
SCOPE_RUN_PREFIX = "run:"
SCOPE_TENANT_PREFIX = "tenant:"

#: 沙箱内工作区根(与 tool-service 的默认 cwd 一致)。
WORKSPACE_ROOT = "/workspace"


def scope_key_for(
    tenant_id: str,
    *,
    conversation_id: str | None = None,
    run_id: str | None = None,
) -> str:
    """决定沙箱复用粒度:对话 > 任务(run)> 租户。

    与 tool-service 的 `scope_key_for` 同规则(那边多一个显式
    `sandbox_scope` 覆盖参数);这里只服务上下文注入,不需要显式覆盖。
    """

    if conversation_id:
        return SCOPE_CONVERSATION_PREFIX + conversation_id
    if run_id:
        return SCOPE_RUN_PREFIX + run_id
    return SCOPE_TENANT_PREFIX + tenant_id


def run_id_from_scope_key(scope_key: str) -> str | None:
    """从 scope_key 反推 run_id(仅 `run:` 作用域有;ensure 时透传给下游)。"""

    if not scope_key.startswith(SCOPE_RUN_PREFIX):
        return None
    run_id = scope_key[len(SCOPE_RUN_PREFIX) :]
    return run_id or None


class WorkspaceFiles(Protocol):
    """上下文注入/offload 需要的最小工作区文件能力(测试可注入内存假实现)。"""

    async def read_text(
        self, tenant_id: str, scope_key: str, path: str
    ) -> str | None: ...

    async def write_bytes(
        self, tenant_id: str, scope_key: str, path: str, data: bytes
    ) -> None: ...

    async def find(
        self,
        tenant_id: str,
        scope_key: str,
        names: Sequence[str],
        *,
        max_depth: int,
    ) -> list[str]: ...


@dataclass
class SandboxWorkspaceFiles:
    """基于 workspace-service 沙箱的 WorkspaceFiles 实现。

    ensure 参数与 tool-service `HttpSandboxProvider` 一致(`scope_key` /
    `ttl_seconds` / `run_id`);沙箱 id 按 `(tenant_id, scope_key)` 进程内缓存,
    同一对话/run 的连续读写只 ensure 一次。
    """

    sandbox: Sandbox
    ttl_seconds: int = 900
    #: 进程内 ensure 缓存:(tenant_id, scope_key) -> sandbox_id
    _ids: dict[tuple[str, str], str] = field(default_factory=dict)

    async def _sandbox_id(self, tenant_id: str, scope_key: str) -> str:
        """取(或 ensure 出)当前作用域的沙箱 id。"""

        cache_key = (tenant_id, scope_key)
        cached = self._ids.get(cache_key)
        if cached:
            return cached
        info, _created = await self.sandbox.ensure(
            tenant_id,
            scope_key=scope_key,
            ttl_seconds=self.ttl_seconds,
            run_id=run_id_from_scope_key(scope_key),
        )
        self._ids[cache_key] = info.id
        return info.id

    async def read_text(self, tenant_id: str, scope_key: str, path: str) -> str | None:
        """读文本文件;不存在/不可读/解码失败一律返回 None(调用方降级)。"""

        try:
            sandbox_id = await self._sandbox_id(tenant_id, scope_key)
            data = await self.sandbox.read_file(tenant_id, sandbox_id, path)
        except Exception:  # noqa: BLE001 - 读失败降级为"没有文件"
            return None
        return data.decode("utf-8", errors="replace")

    async def write_bytes(
        self, tenant_id: str, scope_key: str, path: str, data: bytes
    ) -> None:
        """写文件;父目录不存在时先 `mkdir -p`,失败抛异常由调用方决定降级。"""

        sandbox_id = await self._sandbox_id(tenant_id, scope_key)
        parent = posixpath.dirname(path)
        if parent and parent != WORKSPACE_ROOT:
            result = await self.sandbox.exec(
                tenant_id, sandbox_id, ["mkdir", "-p", parent]
            )
            if result.exit_code != 0:
                raise RuntimeError(f"创建沙箱目录失败: {parent}")
        await self.sandbox.write_file(tenant_id, sandbox_id, path, data)

    async def find(
        self,
        tenant_id: str,
        scope_key: str,
        names: Sequence[str],
        *,
        max_depth: int,
    ) -> list[str]:
        """按文件名在 `/workspace` 内查找(深度含根文件),只返回文件路径。

        `max_depth` 的口径:0 = 只看 /workspace 根下的文件;1 = 再含一层子目录。
        对应 `find -maxdepth (max_depth + 1)`(find 把起始目录自身算作第 0 层)。
        """

        candidates = [str(name).strip() for name in names if str(name).strip()]
        if not candidates:
            return []
        depth = max(0, int(max_depth))
        argv = ["find", WORKSPACE_ROOT, "-maxdepth", str(depth + 1), "-type", "f", "("]
        for index, name in enumerate(candidates):
            if index:
                argv.append("-o")
            argv.extend(["-name", name])
        argv.extend([")", "-print"])
        try:
            sandbox_id = await self._sandbox_id(tenant_id, scope_key)
            result = await self.sandbox.exec(tenant_id, sandbox_id, argv)
        except Exception:  # noqa: BLE001 - 发现失败降级为"没有文档"
            return []
        if result.exit_code != 0:
            return []
        paths: list[str] = []
        seen: set[str] = set()
        prefix = WORKSPACE_ROOT + "/"
        for line in result.stdout.splitlines():
            path = line.strip()
            if not path.startswith(prefix):
                continue
            relative = path[len(prefix) :]
            if relative.count("/") > depth:
                continue
            if path in seen:
                continue
            seen.add(path)
            paths.append(path)
        return paths
