"""stdio 型 MCP(P2):在沙箱里拉起 MCP 进程,经 stdin/stdout JSON-RPC 调用。

为什么必须进沙箱:stdio MCP 是任意本地进程(npx/uvx 拉第三方包),直接跑在
orchestrator 进程里等于把租户配置变成宿主机代码执行。这里统一交给
workspace-service 的沙箱(microsandbox),orchestrator 只做两件事:

1. StdioSandboxPool:按作用域(对话/run)常驻沙箱、写 probe、执行 probe;
2. PROBE_PY / PROBE_JS:沙箱内的一次性客户端——读一行 JSON 请求,spawn MCP
   子进程,走 initialize → tools/list / tools/call,把结果按 BEAUTY_MCP_RESULT:
   marker 打回 stdout。进程退出码恒 0,失败用 {"ok": false, "error": ...} 表达,
   服务层只需解析 stdout。

probe 只用标准库/内置模块:沙箱镜像不保证有第三方依赖,probe 自己也绝不能
再依赖"先装包"。
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any

from common.errors import UpstreamError
from common.logging import get_logger

from agent_orchestrator.models.mcp import MCPServer
from agent_orchestrator.services.mcp_client import MCPError
from agent_orchestrator.services.mcp_stdio_probe import PROBE_JS as PROBE_JS
from agent_orchestrator.services.mcp_stdio_probe import PROBE_PY as PROBE_PY
from agent_orchestrator.services.sandbox import Sandbox, SandboxExecResult

logger = get_logger(__name__)

#: probe 输出标记:stdout 里以它开头的最后一行是结果(其余是 MCP 进程自己的日志)
MARKER = "BEAUTY_MCP_RESULT:"
#: probe 在沙箱内的落地目录(workspace-service 写文件会自动建父目录)
PROBE_DIR = "/workspace/.mcp"
PROBE_PATHS = {
    "python": PROBE_DIR + "/probe.py",
    "node": PROBE_DIR + "/probe.js",
}
#: 运行时 → 解释器(沙箱镜像里的可执行名)
RUNTIME_BINS = {"python": "python3", "node": "node"}


PROBE_SOURCES = {"python": PROBE_PY, "node": PROBE_JS}


def parse_probe_output(result: SandboxExecResult) -> dict[str, Any]:
    """解析 probe 的 stdout:取最后一条 marker 行,成功返回 JSON-RPC result。

    失败一律抛 MCPError(服务层再转成可读错误/回喂模型),不裸抛 ValueError。
    """
    payload: dict[str, Any] | None = None
    for line in (result.stdout or "").splitlines():
        text = line.strip()
        if not text.startswith(MARKER):
            continue
        raw = text[len(MARKER) :].strip()
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise MCPError(f"MCP probe 输出不是合法 JSON:{raw[:200]}") from exc
        if isinstance(parsed, dict):
            payload = parsed
    if payload is None:
        tail = (result.stderr or "")[-2000:]
        raise MCPError(
            f"MCP probe 没有返回结果(marker 缺失);exit={result.exit_code} stderr={tail}"
        )
    if not payload.get("ok"):
        raise MCPError(str(payload.get("error") or "MCP 调用失败"))
    value = payload.get("result")
    return value if isinstance(value, dict) else {"result": value}


class StdioSandboxPool:
    """按作用域常驻的 stdio MCP 沙箱池(进程内缓存 + 每 key 一把锁)。

    作用域 key 由调用方给(<sandbox_scope>:<server_id>):同一对话命中同一个
    沙箱;测试端点用一次性 key 并在结束时 release。

    缓存失效策略:第二次用到时先 touch 续期,失败(404/409/502 等
    UpstreamError)说明沙箱过期/被回收,清缓存重建——不向上抛,用户只是多等
    一次冷启动。
    """

    def __init__(
        self,
        sandbox: Sandbox,
        *,
        ttl_seconds: int = 1800,
        network: str = "public",
        python_image: str = "python",
        node_image: str = "node",
        timeout_seconds: int = 60,
    ) -> None:
        self._sandbox = sandbox
        self._ttl_seconds = ttl_seconds
        self._network = network
        self._images = {"python": python_image, "node": node_image}
        self._timeout_seconds = timeout_seconds
        self._sandboxes: dict[str, str] = {}
        self._probes: set[tuple[str, str]] = set()
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def cached_ids(self) -> dict[str, str]:
        """当前常驻沙箱快照(运维/测试只读)。"""
        return dict(self._sandboxes)

    def _lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def _forget(self, key: str) -> None:
        sandbox_id = self._sandboxes.pop(key, None)
        if sandbox_id is None:
            return
        self._probes -= {
            (item_id, runtime)
            for item_id, runtime in self._probes
            if item_id == sandbox_id
        }

    async def ensure(self, tenant_id: str, key: str, server: MCPServer) -> str:
        """确保 key 对应的沙箱存在并续期,返回 sandbox_id(同 key 并发只建一个)。"""
        async with self._lock(key):
            sandbox_id = self._sandboxes.get(key)
            if sandbox_id is not None:
                try:
                    await self._sandbox.touch(tenant_id, sandbox_id)
                    return sandbox_id
                except UpstreamError:
                    logger.info("stdio MCP 沙箱已失效,重建", key=key)
                    self._forget(key)
            runtime = server.runtime or "python"
            image = server.image or self._images.get(runtime, "python")
            info = await self._sandbox.create(
                tenant_id,
                kind="mcp",
                image=image,
                ttl_seconds=self._ttl_seconds,
                network=self._network,
            )
            self._sandboxes[key] = info.id
            return info.id

    async def _probe_path(self, tenant_id: str, sandbox_id: str, runtime: str) -> str:
        if runtime not in PROBE_SOURCES:
            raise MCPError(f"不支持的 MCP runtime:{runtime}")
        marker = (sandbox_id, runtime)
        if marker not in self._probes:
            await self._sandbox.write_file(
                tenant_id,
                sandbox_id,
                PROBE_PATHS[runtime],
                PROBE_SOURCES[runtime].encode("utf-8"),
            )
            self._probes.add(marker)
        return PROBE_PATHS[runtime]

    async def run(
        self, tenant_id: str, key: str, server: MCPServer, request: dict[str, Any]
    ) -> dict[str, Any]:
        """在沙箱内跑一次 probe,返回 JSON-RPC result(失败抛 MCPError)。"""
        sandbox_id = await self.ensure(tenant_id, key, server)
        runtime = server.runtime or "python"
        probe_path = await self._probe_path(tenant_id, sandbox_id, runtime)
        timeout = max(int(self._timeout_seconds), int(server.timeout_seconds or 0) + 30)
        result = await self._sandbox.exec(
            tenant_id,
            sandbox_id,
            [RUNTIME_BINS[runtime], probe_path],
            stdin=json.dumps(request, ensure_ascii=False),
            timeout_seconds=timeout,
        )
        return parse_probe_output(result)

    async def release(self, tenant_id: str, key: str) -> None:
        """销毁该 key 的沙箱并清缓存(测试端点等一次性场景用)。"""
        sandbox_id = self._sandboxes.get(key)
        self._forget(key)
        if sandbox_id is None:
            return
        with suppress(UpstreamError):
            await self._sandbox.destroy(tenant_id, sandbox_id)

    def forget(self, key: str) -> None:
        """只清本地缓存(不销毁):沙箱交给 TTL 自然回收。"""
        self._forget(key)
