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

PROBE_PY = r'''#!/usr/bin/env python3
"""stdio MCP probe(Python 版):把一行 JSON 请求翻译成 MCP stdio JSON-RPC。

输入(父进程经 stdin 传一行 JSON):
    {command, args, env, timeout, action, tool?, arguments?}
输出(stdout 单行;进程退出码恒 0,方便服务层解析):
    BEAUTY_MCP_RESULT:{"ok": true, "result": ...}
    BEAUTY_MCP_RESULT:{"ok": false, "error": "..."}

只用标准库:沙箱镜像里不保证有第三方包。
"""

import json
import os
import subprocess
import sys
import threading

MARKER = "BEAUTY_MCP_RESULT:"
DEFAULT_TIMEOUT = 30.0
STDERR_LIMIT = 2000


class ProbeError(Exception):
    """probe 内部失败(会转成 {"ok": false, ...} 输出)。"""


class Probe(object):
    """一次性的 MCP stdio 客户端:拉起子进程、发请求、读匹配 id 的响应。"""

    def __init__(self, request):
        # 注意别叫 self.request:会遮蔽同名的 JSON-RPC request 方法
        self.input = request
        self.timeout = float(request.get("timeout") or DEFAULT_TIMEOUT)
        self.stderr_chunks = []
        self.timed_out = False
        self.proc = None
        self._timer = None
        self._next_id = 0

    # ---- JSON-RPC 通道 ----

    def _write(self, payload):
        stdin = self.proc.stdin
        stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        stdin.flush()

    def notify(self, method):
        self._write({"jsonrpc": "2.0", "method": method})

    def request(self, method, params):
        self._next_id += 1
        want = self._next_id
        self._write(
            {
                "jsonrpc": "2.0",
                "id": want,
                "method": method,
                "params": params or {},
            }
        )
        return self._read(want)

    def _read(self, want_id):
        stdout = self.proc.stdout
        while True:
            line = stdout.readline()
            if not line:
                if self.timed_out:
                    raise ProbeError("MCP 进程超时(%.1fs)" % self.timeout)
                raise ProbeError("MCP 进程在返回 id=%s 前退出" % want_id)
            text = line.strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            # 跳过服务端通知/反向请求(带 method),以及别的 id
            if "method" in message or message.get("id") != want_id:
                continue
            if "error" in message:
                error = message.get("error") or {}
                if isinstance(error, dict):
                    raise ProbeError(str(error.get("message") or "MCP 调用失败"))
                raise ProbeError(str(error))
            return message.get("result")

    # ---- 子进程与超时 ----

    def _drain_stderr(self):
        try:
            stderr = self.proc.stderr
            while True:
                chunk = stderr.readline()
                if not chunk:
                    return
                self.stderr_chunks.append(chunk)
                if len(self.stderr_chunks) > 200:
                    del self.stderr_chunks[:100]
        except (OSError, ValueError):
            return

    def _on_timeout(self):
        self.timed_out = True
        proc = self.proc
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass

    def stderr_tail(self):
        return "".join(self.stderr_chunks)[-STDERR_LIMIT:].strip()

    def _stop(self):
        if self._timer is not None:
            self._timer.cancel()
        proc = self.proc
        if proc is None:
            return
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except (OSError, ValueError):
                pass
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass

    def run(self):
        request = self.input
        env = dict(os.environ)
        for key, value in (request.get("env") or {}).items():
            env[str(key)] = str(value)
        argv = [str(request["command"])]
        argv.extend(str(item) for item in (request.get("args") or []))
        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )
        reader = threading.Thread(target=self._drain_stderr)
        reader.daemon = True
        reader.start()
        self._timer = threading.Timer(self.timeout, self._on_timeout)
        self._timer.daemon = True
        self._timer.start()
        try:
            self.request(
                "initialize",
                {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "beauty-moon", "version": "0.1.0"},
                },
            )
            try:
                self.notify("notifications/initialized")
            except (OSError, ValueError):
                # 通知发不出去不算失败:部分 server 不实现该通知
                pass
            action = request.get("action") or "tools/list"
            if action == "tools/call":
                return self.request(
                    "tools/call",
                    {
                        "name": request.get("tool"),
                        "arguments": request.get("arguments") or {},
                    },
                )
            return self.request("tools/list", {})
        finally:
            self._stop()


def _emit(payload):
    sys.stdout.write(MARKER + json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _with_stderr(message, probe):
    tail = probe.stderr_tail()
    if tail:
        return message + " | stderr: " + tail
    return message


def main():
    raw = sys.stdin.readline()
    try:
        request = json.loads(raw)
    except ValueError as exc:
        _emit({"ok": False, "error": "probe 输入不是合法 JSON:%s" % exc})
        return 0
    if not isinstance(request, dict) or not request.get("command"):
        _emit({"ok": False, "error": "probe 输入缺少 command"})
        return 0
    probe = Probe(request)
    try:
        result = probe.run()
    except ProbeError as exc:
        _emit({"ok": False, "error": _with_stderr(str(exc), probe)})
        return 0
    except Exception as exc:
        _emit(
            {
                "ok": False,
                "error": _with_stderr(
                    "%s: %s" % (type(exc).__name__, exc), probe
                ),
            }
        )
        return 0
    _emit({"ok": True, "result": result})
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

PROBE_JS = r"""#!/usr/bin/env node
// stdio MCP probe(node 版):与 PROBE_PY 同协议,只依赖 Node 内置模块。
"use strict";

const { spawn } = require("child_process");
const readline = require("readline");

const MARKER = "BEAUTY_MCP_RESULT:";
const DEFAULT_TIMEOUT = 30;
const STDERR_LIMIT = 2000;

function emit(payload) {
  process.stdout.write(MARKER + JSON.stringify(payload) + "\n");
}

function readInput() {
  return new Promise((resolve) => {
    let buffer = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => {
      buffer += chunk;
      if (buffer.indexOf("\n") >= 0) {
        resolve(buffer.split("\n")[0]);
      }
    });
    process.stdin.on("end", () => resolve(buffer.trim()));
  });
}

function withStderr(message, stderr) {
  const tail = (stderr || "").trim();
  return tail ? message + " | stderr: " + tail : message;
}

async function runChild(input) {
  const timeoutMs = Number(input.timeout || DEFAULT_TIMEOUT) * 1000;
  const env = Object.assign({}, process.env, input.env || {});
  const argv = [String(input.command)].concat((input.args || []).map(String));
  const child = spawn(argv[0], argv.slice(1), {
    stdio: ["pipe", "pipe", "pipe"],
    env: env,
  });
  let stderr = "";
  child.stderr.on("data", (chunk) => {
    stderr = (stderr + chunk.toString()).slice(-STDERR_LIMIT);
  });
  const stderrTail = () => stderr.slice(-STDERR_LIMIT);
  const pending = new Map();
  let nextId = 0;
  let done = false;
  let timer = null;

  const rejectAll = (message) => {
    for (const entry of pending.values()) {
      entry.reject(new Error(message));
    }
    pending.clear();
  };

  child.on("error", (exc) => {
    rejectAll("启动 MCP 进程失败:" + exc.message);
  });
  child.on("exit", (code) => {
    if (done) {
      return;
    }
    rejectAll(withStderr("MCP 进程退出(code=" + code + ")", stderrTail()));
  });

  const rl = readline.createInterface({ input: child.stdout });
  rl.on("line", (line) => {
    const text = line.trim();
    if (!text) {
      return;
    }
    let message;
    try {
      message = JSON.parse(text);
    } catch (exc) {
      return;
    }
    if (!message || typeof message !== "object" || "method" in message) {
      return;
    }
    const entry = pending.get(message.id);
    if (!entry) {
      return;
    }
    pending.delete(message.id);
    if (message.error) {
      entry.reject(new Error(String(message.error.message || "MCP 调用失败")));
    } else {
      entry.resolve(message.result);
    }
  });

  timer = setTimeout(() => {
    try {
      child.kill("SIGKILL");
    } catch (exc) {}
    rejectAll("MCP 进程超时(" + timeoutMs / 1000 + "s)");
  }, timeoutMs);

  const send = (payload) => {
    child.stdin.write(JSON.stringify(payload) + "\n");
  };

  const request = (method, params) => {
    nextId += 1;
    const id = nextId;
    return new Promise((resolveCall, rejectCall) => {
      pending.set(id, { resolve: resolveCall, reject: rejectCall });
      try {
        send({
          jsonrpc: "2.0",
          id: id,
          method: method,
          params: params || {},
        });
      } catch (exc) {
        pending.delete(id);
        rejectCall(new Error("写入 MCP 进程失败:" + exc.message));
      }
    });
  };

  try {
    await request("initialize", {
      protocolVersion: "2025-03-26",
      capabilities: {},
      clientInfo: { name: "beauty-moon", version: "0.1.0" },
    });
    try {
      send({ jsonrpc: "2.0", method: "notifications/initialized" });
    } catch (exc) {}
    const action = input.action || "tools/list";
    if (action === "tools/call") {
      return await request("tools/call", {
        name: input.tool,
        arguments: input.arguments || {},
      });
    }
    return await request("tools/list", {});
  } finally {
    done = true;
    clearTimeout(timer);
    try {
      rl.close();
    } catch (exc) {}
    try {
      child.stdin.end();
    } catch (exc) {}
    try {
      child.kill("SIGKILL");
    } catch (exc) {}
  }
}

readInput()
  .then((raw) => {
    let input;
    try {
      input = JSON.parse(raw);
    } catch (exc) {
      throw new Error("probe 输入不是合法 JSON:" + exc.message);
    }
    if (!input || typeof input !== "object" || !input.command) {
      throw new Error("probe 输入缺少 command");
    }
    return runChild(input);
  })
  .then((result) => {
    emit({ ok: true, result: result });
  })
  .catch((exc) => {
    emit({ ok: false, error: exc && exc.message ? exc.message : String(exc) });
  })
  .finally(() => {
    process.exitCode = 0;
  });
"""

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
