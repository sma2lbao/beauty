"""MCP stdio 沙箱探针脚本源码(Python/JS 两套,自 mcp_stdio.py 拆出)。"""

from __future__ import annotations

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
