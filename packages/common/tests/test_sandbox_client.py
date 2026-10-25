"""common.clients.sandbox:路径/鉴权/编码/错误映射(MockTransport,零网络)。"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from common.clients.sandbox import (
    SandboxClient,
    SandboxExecResult,
    SandboxInfo,
)
from common.errors import UpstreamError

BASE_URL = "http://sandbox.local"

SANDBOX_PAYLOAD: dict[str, Any] = {
    "id": "sb-1",
    "name": "bm-abc123",
    "kind": "generic",
    "provider": "microsandbox",
    "image": "python",
    "cpus": 1.0,
    "memory_mb": 512,
    "network": "deny",
    "workspace_id": None,
    "run_id": "run-1",
    "status": "running",
    "created_at": "2026-10-20T12:00:00+00:00",
    "last_used_at": "2026-10-20T12:00:00+00:00",
    "expires_at": "2026-10-20T12:30:00+00:00",
    "last_error": None,
}

EXEC_PAYLOAD: dict[str, Any] = {
    "exit_code": 0,
    "stdout": "Python 3.12.0",
    "stderr": "",
    "duration_ms": 12,
    "truncated": False,
}


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    tokens: list[str],
    timeout_seconds: float = 60.0,
) -> SandboxClient:
    """按租户换发可预测 token,并把换发次数记进 tokens(零网络)。"""

    async def token_provider(tenant_id: str) -> str:
        tokens.append(tenant_id)
        return f"tok-{tenant_id}"

    return SandboxClient(
        BASE_URL,
        timeout_seconds,
        transport=httpx.MockTransport(handler),
        token_provider=token_provider,
    )


async def test_create_posts_only_non_none_fields_with_bearer() -> None:
    """创建:只传非 None 字段;Bearer 来自 token_provider。"""
    seen: list[httpx.Request] = []
    tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json=SANDBOX_PAYLOAD)

    client = _client(handler, tokens)
    info = await client.create(
        "t-a", kind="skill", cpus=2.0, memory_mb=1024, network="public", run_id="r-1"
    )
    assert isinstance(info, SandboxInfo)
    assert info.id == "sb-1"
    assert info.status == "running"
    assert info.expires_at is not None
    assert info.expires_at.isoformat() == "2026-10-20T12:30:00+00:00"
    request = seen[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/sandboxes"
    assert request.headers["Authorization"] == "Bearer tok-t-a"
    assert json.loads(request.content) == {
        "kind": "skill",
        "cpus": 2.0,
        "memory_mb": 1024,
        "network": "public",
        "run_id": "r-1",
    }
    assert tokens == ["t-a"]
    await client.aclose()


async def test_create_minimal_payload() -> None:
    """创建:只给 kind 时 body 只有 kind,其余交给下游默认值。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json=SANDBOX_PAYLOAD)

    client = _client(handler, [])
    await client.create("t-a", image="python", ttl_seconds=300, workspace_id="ws-1")
    assert json.loads(seen[0].content) == {
        "kind": "generic",
        "image": "python",
        "ttl_seconds": 300,
        "workspace_id": "ws-1",
    }
    await client.aclose()


async def test_get_exec_and_touch_hit_expected_paths() -> None:
    """读/执行/续期:路径与 body 对齐契约;exec 结果解析成数据类。"""
    seen: list[httpx.Request] = []
    tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/exec"):
            return httpx.Response(200, json=EXEC_PAYLOAD)
        return httpx.Response(200, json=SANDBOX_PAYLOAD)

    client = _client(handler, tokens)
    info = await client.get("t-a", "sb-1")
    assert info.run_id == "run-1"
    result = await client.exec(
        "t-a",
        "sb-1",
        ["python", "-V"],
        env={"PYTHONUNBUFFERED": "1"},
        stdin="",
        timeout_seconds=5,
    )
    assert result == SandboxExecResult(
        exit_code=0,
        stdout="Python 3.12.0",
        stderr="",
        duration_ms=12,
        truncated=False,
    )
    touched = await client.touch("t-a", "sb-1")
    assert touched.status == "running"
    assert [request.method for request in seen] == ["GET", "POST", "POST"]
    assert [request.url.path for request in seen] == [
        "/v1/sandboxes/sb-1",
        "/v1/sandboxes/sb-1/exec",
        "/v1/sandboxes/sb-1/touch",
    ]
    assert json.loads(seen[1].content) == {
        "argv": ["python", "-V"],
        "cwd": "/workspace",
        "env": {"PYTHONUNBUFFERED": "1"},
        "stdin": "",
        "timeout_seconds": 5,
    }
    assert tokens == ["t-a", "t-a", "t-a"]
    await client.aclose()


async def test_exec_omits_optional_fields() -> None:
    """exec:未给 env/stdin/timeout 时 body 只有 argv 与 cwd。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=EXEC_PAYLOAD)

    client = _client(handler, [])
    await client.exec("t-a", "sb-1", ["true"])
    assert json.loads(seen[0].content) == {"argv": ["true"], "cwd": "/workspace"}
    await client.aclose()


async def test_write_file_base64_roundtrip() -> None:
    """文件写:正文 base64 进 JSON;204 视为成功。"""
    data = "你好,沙箱".encode()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    client = _client(handler, [])
    assert await client.write_file("t-a", "sb-1", "/workspace/a.bin", data) is None
    assert seen[0].method == "PUT"
    assert seen[0].url.path == "/v1/sandboxes/sb-1/files"
    body = json.loads(seen[0].content)
    assert body["path"] == "/workspace/a.bin"
    assert base64.b64decode(body["content_base64"]) == data
    await client.aclose()


async def test_read_file_decodes_base64_from_query() -> None:
    """文件读:path 走 query,正文 base64 解码回原始字节。"""
    data = b"\x00\x01hello\xff"
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "path": "/workspace/a.bin",
                "content_base64": base64.b64encode(data).decode("ascii"),
                "size_bytes": len(data),
            },
        )

    client = _client(handler, [])
    assert await client.read_file("t-a", "sb-1", "/workspace/a.bin") == data
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/v1/sandboxes/sb-1/files"
    assert seen[0].url.params["path"] == "/workspace/a.bin"
    await client.aclose()


async def test_destroy_accepts_204() -> None:
    """删除:204 即成功(幂等由下游保证)。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    client = _client(handler, [])
    await client.destroy("t-a", "sb-1")
    assert seen[0].method == "DELETE"
    assert seen[0].url.path == "/v1/sandboxes/sb-1"
    await client.aclose()


async def test_transport_timeout_maps_to_unreachable() -> None:
    """传输层超时 → sandbox.unreachable,不裸抛。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("连接超时", request=request)

    client = _client(handler, [])
    with pytest.raises(UpstreamError) as excinfo:
        await client.get("t-a", "sb-1")
    assert excinfo.value.code == "sandbox.unreachable"
    await client.aclose()


async def test_downstream_500_maps_to_upstream_error_with_details() -> None:
    """非 2xx → sandbox.upstream_error,details 带 status 与截断响应体。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="x" * 400)

    client = _client(handler, [])
    with pytest.raises(UpstreamError) as excinfo:
        await client.exec("t-a", "sb-1", ["true"])
    assert excinfo.value.code == "sandbox.upstream_error"
    assert excinfo.value.details["status"] == 500
    assert len(excinfo.value.details["body"]) <= 200
    await client.aclose()


async def test_invalid_json_response_maps_to_upstream_error() -> None:
    """响应体不是 JSON → sandbox.upstream_error(不裸抛 ValueError)。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>boom</html>")

    client = _client(handler, [])
    with pytest.raises(UpstreamError) as excinfo:
        await client.get("t-a", "sb-1")
    assert excinfo.value.code == "sandbox.upstream_error"
    await client.aclose()


async def test_non_object_json_response_maps_to_upstream_error() -> None:
    """响应体是 JSON 数组 → 同样映射 sandbox.upstream_error。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2, 3])

    client = _client(handler, [])
    with pytest.raises(UpstreamError) as excinfo:
        await client.touch("t-a", "sb-1")
    assert excinfo.value.code == "sandbox.upstream_error"
    await client.aclose()


async def test_invalid_base64_response_maps_to_upstream_error() -> None:
    """文件读响应不是合法 base64 → sandbox.upstream_error。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"path": "/workspace/a.bin", "content_base64": "!!!"}
        )

    client = _client(handler, [])
    with pytest.raises(UpstreamError) as excinfo:
        await client.read_file("t-a", "sb-1", "/workspace/a.bin")
    assert excinfo.value.code == "sandbox.upstream_error"
    await client.aclose()


async def test_malformed_payload_maps_to_upstream_error() -> None:
    """缺必填字段/类型不符 → sandbox.upstream_error(不裸露 KeyError)。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"name": "bm"})

    client = _client(handler, [])
    with pytest.raises(UpstreamError) as excinfo:
        await client.get("t-a", "sb-1")
    assert excinfo.value.code == "sandbox.upstream_error"
    await client.aclose()


async def test_invalid_datetime_maps_to_upstream_error() -> None:
    """时间字段非法 → sandbox.upstream_error + details 带原始值。"""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = dict(SANDBOX_PAYLOAD, expires_at="not-a-time")
        return httpx.Response(200, json=payload)

    client = _client(handler, [])
    with pytest.raises(UpstreamError) as excinfo:
        await client.get("t-a", "sb-1")
    assert excinfo.value.code == "sandbox.upstream_error"
    assert excinfo.value.details["value"] == "not-a-time"
    await client.aclose()


async def test_exec_result_malformed_maps_to_upstream_error() -> None:
    """exec 响应缺 exit_code → sandbox.upstream_error。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"stdout": "x"})

    client = _client(handler, [])
    with pytest.raises(UpstreamError) as excinfo:
        await client.exec("t-a", "sb-1", ["true"])
    assert excinfo.value.code == "sandbox.upstream_error"
    await client.aclose()


async def test_aclose_closes_underlying_client() -> None:
    """aclose 关闭底层 httpx 客户端:关闭后请求直接失败。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=SANDBOX_PAYLOAD)

    client = _client(handler, [])
    await client.aclose()
    with pytest.raises(RuntimeError):
        await client.get("t-a", "sb-1")


async def test_ensure_posts_scope_key_and_returns_created_flag() -> None:
    """ensure:POST /v1/sandboxes:ensure,只传非 None 字段,解析 {sandbox,created}。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"sandbox": SANDBOX_PAYLOAD, "created": True})

    client = _client(handler, [])
    info, created = await client.ensure(
        "t-a",
        scope_key="conversation:c-1",
        image="python",
        ttl_seconds=1800,
        run_id="r-1",
    )
    assert created is True
    assert info.id == "sb-1"
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/v1/sandboxes:ensure"
    assert seen[0].headers["Authorization"] == "Bearer tok-t-a"
    assert json.loads(seen[0].content) == {
        "kind": "generic",
        "scope_key": "conversation:c-1",
        "image": "python",
        "ttl_seconds": 1800,
        "run_id": "r-1",
    }
    await client.aclose()


async def test_ensure_reports_reuse_with_created_false() -> None:
    """ensure 复用已有沙箱:200 + created=false 同样正常返回。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"sandbox": SANDBOX_PAYLOAD, "created": False})

    client = _client(handler, [])
    info, created = await client.ensure("t-a", scope_key="run:r-1")
    assert created is False
    assert info.name == "bm-abc123"
    await client.aclose()


async def test_ensure_rejects_incomplete_response() -> None:
    """ensure 缺 sandbox / created → sandbox.upstream_error(不裸露 KeyError)。"""

    for payload in ({"created": True}, {"sandbox": SANDBOX_PAYLOAD}):

        def handler(
            request: httpx.Request, body: dict[str, Any] = payload
        ) -> httpx.Response:
            return httpx.Response(200, json=body)

        client = _client(handler, [])
        with pytest.raises(UpstreamError) as excinfo:
            await client.ensure("t-a", scope_key="run:r-1")
        assert excinfo.value.code == "sandbox.upstream_error"
        await client.aclose()


async def test_ensure_maps_conflict_to_upstream_error() -> None:
    """下游 409(ensure_busy 等)不是预期状态 → sandbox.upstream_error。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"code": "sandbox.ensure_busy"})

    client = _client(handler, [])
    with pytest.raises(UpstreamError) as excinfo:
        await client.ensure("t-a", scope_key="run:r-1")
    assert excinfo.value.code == "sandbox.upstream_error"
    assert excinfo.value.details["status"] == 409
    await client.aclose()
