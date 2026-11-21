"""SandboxdProvider 测试夹具:httpx.MockTransport + 可编程假控制面。

不连真实 sandboxd、不发任何网络请求;所有响应由内存路由返回。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs

import httpx

from workspace.services.sandbox_provider import SandboxSpec

DEFAULT_EXEC = {
    "exit_code": 0,
    "stdout": "ok\n",
    "stderr": "",
    "timed_out": False,
    "truncated": False,
    "duration_ms": 12,
}


class FakeWebSocket:
    """最小 WebSocket 双工桩:push() 注入服务端帧,sent 记录客户端帧。"""

    def __init__(self) -> None:
        self.incoming: asyncio.Queue[str | bytes | None] = asyncio.Queue()
        self.sent: list[str] = []
        self.closed = False

    def push(self, message: str | bytes) -> None:
        self.incoming.put_nowait(message)

    def finish(self) -> None:
        self.incoming.put_nowait(None)

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        self.closed = True
        self.finish()

    def __aiter__(self) -> FakeWebSocket:
        return self

    async def __anext__(self) -> str | bytes:
        message = await self.incoming.get()
        if message is None:
            raise StopAsyncIteration
        return message


class PtyHandshakeError(Exception):
    """模拟 websockets 握手失败:带 response.status_code。"""

    def __init__(self, status: int) -> None:
        super().__init__(f"handshake failed: {status}")
        self.response = SimpleNamespace(status_code=status)


class FakePtyConnector:
    """替代 websockets.asyncio.client.connect,记录连接参数并返回假连接。"""

    def __init__(
        self,
        websocket: FakeWebSocket | None = None,
        *,
        error: BaseException | None = None,
    ) -> None:
        self.websocket = websocket if websocket is not None else FakeWebSocket()
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, url: str, **kwargs: Any) -> FakeWebSocket:
        self.calls.append({"url": url, **kwargs})
        if self.error is not None:
            raise self.error
        return self.websocket


def _spec(**overrides: Any) -> SandboxSpec:
    base: dict[str, Any] = {
        "account_id": "acc-1",
        "name": "bm-abc",
        "kind": "generic",
        "image": "python",
        "cpus": 1.0,
        "memory_mb": 512,
        "network": "deny",
        "ttl_seconds": 900,
    }
    base.update(overrides)
    return SandboxSpec(**base)


class SandboxdStub:
    """内存 sandboxd:按 method + path 路由,并记录完整请求。"""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.create_status = 201
        self.create_payload: Any = {"id": "sbx-1"}
        self.list_status = 200
        self.list_payload: Any = {"sandboxes": []}
        self.exec_status = 200
        self.exec_payload: Any = dict(DEFAULT_EXEC)
        self.extend_status = 200
        self.delete_status = 204
        self.upload_statuses: list[int] = []
        self.download_status = 200
        self.download_body = b"content"
        self.file_list_status = 200
        self.file_lists: dict[str, Any] = {}
        self.chunks: list[bytes] = []

    # -- helpers -----------------------------------------------------------
    def calls(self, method: str, path: str | None = None) -> list[httpx.Request]:
        return [
            request
            for request in self.requests
            if request.method == method and (path is None or request.url.path == path)
        ]

    @staticmethod
    def body(request: httpx.Request) -> Any:
        return json.loads(request.content or b"{}")

    @staticmethod
    def query(request: httpx.Request) -> dict[str, str]:
        parsed = parse_qs(request.url.query.decode())
        return {key: values[0] for key, values in parsed.items() if values}

    def _file_list(self, path: str) -> httpx.Response:
        if self.file_list_status >= 400:
            return httpx.Response(self.file_list_status, json={"detail": "no such dir"})
        payload = self.file_lists.get(path, {"entries": []})
        if isinstance(payload, list):
            payload = {"entries": payload}
        return httpx.Response(200, json=payload)

    # -- router ------------------------------------------------------------
    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        method = request.method
        path = request.url.path

        if method == "POST" and path == "/v1/sandboxes":
            return httpx.Response(self.create_status, json=self.create_payload)
        if method == "GET" and path == "/v1/sandboxes":
            return httpx.Response(self.list_status, json=self.list_payload)
        if method == "POST" and path.endswith("/exec"):
            return httpx.Response(self.exec_status, json=self.exec_payload)
        if method == "POST" and path.endswith("/extend"):
            return httpx.Response(self.extend_status, json={"ok": True})
        if path.endswith("/files/list"):
            return self._file_list(self.query(request).get("path", ""))
        if path.endswith("/files"):
            if method == "PUT":
                self.chunks.append(request.content)
                status = self.upload_statuses.pop(0) if self.upload_statuses else 204
                return httpx.Response(status, content=b"")
            if method == "GET":
                return httpx.Response(self.download_status, content=self.download_body)
            if method == "DELETE":
                return httpx.Response(204, content=b"")
        if method == "DELETE" and path.startswith("/v1/sandboxes/"):
            return httpx.Response(self.delete_status, content=b"")
        return httpx.Response(404, json={"detail": f"unhandled {method} {path}"})
