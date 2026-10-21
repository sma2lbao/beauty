"""沙箱客户端(workspace-service /v1/sandboxes):路径/鉴权/编码/错误映射。

全部走 ``httpx.MockTransport``:零网络、零外部服务;token_provider 用计数器
替身,顺带验证"每个请求都换发一次"。P1 只做基础设施,不涉及工具与图。
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from common.errors import UpstreamError

from agent_orchestrator.core.config import Settings
from agent_orchestrator.services.sandbox import (
    SandboxClient,
    SandboxExecResult,
    SandboxInfo,
)

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
    """创建:POST /v1/sandboxes,只传非 None 字段;Bearer 来自 token_provider。"""
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
    assert info.name == "bm-abc123"
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
    assert tokens == ["t-a"]  # 创建走一次换发
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
    exec_body = json.loads(seen[1].content)
    assert exec_body == {
        "argv": ["python", "-V"],
        "cwd": "/workspace",  # 默认工作目录
        "env": {"PYTHONUNBUFFERED": "1"},
        "stdin": "",
        "timeout_seconds": 5,
    }
    assert tokens == ["t-a", "t-a", "t-a"]  # 每个请求换发一次,不缓存
    await client.aclose()


async def test_write_file_base64_roundtrip() -> None:
    """文件写:正文 base64 进 JSON;204 视为成功(不解析空响应体)。"""
    data = "你好,沙箱\x00\xff".encode()
    seen: list[httpx.Request] = []
    tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    client = _client(handler, tokens)
    assert await client.write_file("t-a", "sb-1", "/workspace/a.bin", data) is None
    request = seen[0]
    assert request.method == "PUT"
    assert request.url.path == "/v1/sandboxes/sb-1/files"
    body = json.loads(request.content)
    assert body["path"] == "/workspace/a.bin"
    assert base64.b64decode(body["content_base64"]) == data
    await client.aclose()


async def test_read_file_decodes_base64_from_query() -> None:
    """文件读:path 走 query,正文 base64 解码回原始字节。"""
    data = b"\x00\x01hello\xff"
    seen: list[httpx.Request] = []
    tokens: list[str] = []

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

    client = _client(handler, tokens)
    assert await client.read_file("t-a", "sb-1", "/workspace/a.bin") == data
    request = seen[0]
    assert request.method == "GET"
    assert request.url.path == "/v1/sandboxes/sb-1/files"
    assert request.url.params["path"] == "/workspace/a.bin"
    await client.aclose()


async def test_destroy_accepts_204() -> None:
    """删除:204 即成功(幂等由下游保证)。"""
    seen: list[httpx.Request] = []
    tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    client = _client(handler, tokens)
    await client.destroy("t-a", "sb-1")
    assert seen[0].method == "DELETE"
    assert seen[0].url.path == "/v1/sandboxes/sb-1"
    await client.aclose()


async def test_transport_timeout_maps_to_unreachable() -> None:
    """传输层超时(httpx.HTTPError)→ sandbox.unreachable,不裸抛。"""
    tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("连接超时", request=request)

    client = _client(handler, tokens)
    with pytest.raises(UpstreamError) as excinfo:
        await client.get("t-a", "sb-1")
    assert excinfo.value.code == "sandbox.unreachable"
    await client.aclose()


async def test_downstream_500_maps_to_upstream_error_with_details() -> None:
    """非 2xx → sandbox.upstream_error,details 带 status 与截断的响应体。"""
    tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="x" * 400)

    client = _client(handler, tokens)
    with pytest.raises(UpstreamError) as excinfo:
        await client.exec("t-a", "sb-1", ["true"])
    assert excinfo.value.code == "sandbox.upstream_error"
    assert excinfo.value.details["status"] == 500
    assert len(excinfo.value.details["body"]) <= 200
    await client.aclose()


async def test_invalid_json_response_maps_to_upstream_error() -> None:
    """响应体不是 JSON 对象 → sandbox.upstream_error(不裸抛 ValueError)。"""
    tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>boom</html>")

    client = _client(handler, tokens)
    with pytest.raises(UpstreamError) as excinfo:
        await client.get("t-a", "sb-1")
    assert excinfo.value.code == "sandbox.upstream_error"
    await client.aclose()


async def test_invalid_base64_response_maps_to_upstream_error() -> None:
    """文件读响应不是合法 base64 → sandbox.upstream_error。"""
    tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"path": "/workspace/a.bin", "content_base64": "!!!"}
        )

    client = _client(handler, tokens)
    with pytest.raises(UpstreamError) as excinfo:
        await client.read_file("t-a", "sb-1", "/workspace/a.bin")
    assert excinfo.value.code == "sandbox.upstream_error"
    await client.aclose()


async def test_aclose_closes_underlying_client() -> None:
    """aclose 关闭底层 httpx 客户端:关闭后请求直接失败。"""
    tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=SANDBOX_PAYLOAD)

    client = _client(handler, tokens)
    await client.aclose()
    with pytest.raises(RuntimeError):
        await client.get("t-a", "sb-1")


def test_settings_carry_sandbox_knobs() -> None:
    """配置项:沙箱开关/地址/超时(显式入参优先于环境,测试自足)。"""
    settings = Settings(
        _env_file=None,
        sandbox_enabled=True,
        sandbox_service_url="http://sandbox.local",
        sandbox_timeout_seconds=12.5,
    )
    assert settings.sandbox_enabled is True
    assert settings.sandbox_service_url == "http://sandbox.local"
    assert settings.sandbox_timeout_seconds == 12.5


def test_sandbox_disabled_by_default(monkeypatch) -> None:
    """默认关闭:P1 不改变既有行为——不开沙箱就不起客户端、不碰网络。"""
    # 本地 .env 会经 Nx 注入任务环境(Settings(_env_file=None) 挡不住),显式清掉
    monkeypatch.delenv("AGENT_ORCHESTRATOR__SANDBOX_ENABLED", raising=False)
    settings = Settings(_env_file=None)
    assert settings.sandbox_enabled is False
