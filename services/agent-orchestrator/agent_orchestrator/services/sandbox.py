"""workspace-service 沙箱 HTTP 客户端(P1:只做基础设施,不向模型暴露工具)。

P2 的 stdio MCP / skill 包执行会复用这里的 ``Sandbox`` 协议;沙箱引擎
(microsandbox)在 workspace-service 侧实现,本服务不直接依赖它。

服务间调用沿用 TokenBroker 签发的内部 JWT:租户在 claim 里,请求只带
``Authorization: Bearer``,不发租户头。失败统一映射 ``UpstreamError``
(不可达 = ``sandbox.unreachable``,下游非 2xx / 响应非法 = ``sandbox.upstream_error``)。
测试用 ``httpx.MockTransport`` 打桩,零网络。
"""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import httpx
from common.errors import UpstreamError
from common.middleware import inject_request_headers

#: 下游错误响应体截断长度(避免把整段 HTML/堆栈塞进 details)
ERROR_BODY_LIMIT = 200
#: 下游不可达(连接/超时等传输层失败)
UNREACHABLE_CODE = "sandbox.unreachable"
#: 下游返回非 2xx 或响应体无法解析
UPSTREAM_ERROR_CODE = "sandbox.upstream_error"


def _optional_str(value: Any) -> str | None:
    """可空字符串字段:None 原样返回,其余转 str。"""
    return None if value is None else str(value)


def _optional_datetime(payload: dict[str, Any], key: str) -> datetime | None:
    """可空时间字段:ISO 8601 解析;缺失返回 None,非法值映射 UpstreamError。"""
    raw = payload.get(key)
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError as exc:
        raise UpstreamError(
            UPSTREAM_ERROR_CODE,
            f"沙箱服务响应字段 {key} 不是合法时间",
            details={"value": str(raw)[:ERROR_BODY_LIMIT]},
        ) from exc


@dataclass(frozen=True, slots=True)
class SandboxInfo:
    """沙箱描述(字段对齐 workspace-service 的 SandboxRead)。"""

    id: str
    name: str
    status: str
    kind: str = "generic"
    provider: str = ""
    image: str | None = None
    cpus: float = 0.0
    memory_mb: int = 0
    network: str = "deny"
    workspace_id: str | None = None
    run_id: str | None = None
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    last_error: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> SandboxInfo:
        """从下游 JSON 构造;缺必填字段/类型不符时映射 UpstreamError。"""
        try:
            return cls(
                id=str(payload["id"]),
                name=str(payload["name"]),
                status=str(payload["status"]),
                kind=str(payload.get("kind", "generic")),
                provider=str(payload.get("provider", "")),
                image=_optional_str(payload.get("image")),
                cpus=float(payload.get("cpus", 0.0)),
                memory_mb=int(payload.get("memory_mb", 0)),
                network=str(payload.get("network") or "deny"),
                workspace_id=_optional_str(payload.get("workspace_id")),
                run_id=_optional_str(payload.get("run_id")),
                created_at=_optional_datetime(payload, "created_at"),
                last_used_at=_optional_datetime(payload, "last_used_at"),
                expires_at=_optional_datetime(payload, "expires_at"),
                last_error=_optional_str(payload.get("last_error")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise UpstreamError(
                UPSTREAM_ERROR_CODE,
                "沙箱服务响应格式非法",
                details={"body": str(payload)[:ERROR_BODY_LIMIT]},
            ) from exc


@dataclass(frozen=True, slots=True)
class SandboxExecResult:
    """沙箱内一次命令执行的结果(字段对齐 SandboxExecRead)。"""

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> SandboxExecResult:
        """从下游 JSON 构造;缺必填字段/类型不符时映射 UpstreamError。"""
        try:
            return cls(
                exit_code=int(payload["exit_code"]),
                stdout=str(payload.get("stdout", "")),
                stderr=str(payload.get("stderr", "")),
                duration_ms=int(payload.get("duration_ms", 0)),
                truncated=bool(payload.get("truncated", False)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise UpstreamError(
                UPSTREAM_ERROR_CODE,
                "沙箱服务响应格式非法",
                details={"body": str(payload)[:ERROR_BODY_LIMIT]},
            ) from exc


class Sandbox(Protocol):
    """沙箱接口:生命周期 + 文件 + 命令执行(P2 的 stdio MCP / skill 包复用)。

    与 workspace-service 内部 API 一一对应;测试可注入 duck typing 的假实现。
    """

    async def create(
        self,
        tenant_id: str,
        *,
        kind: str = "generic",
        image: str | None = None,
        ttl_seconds: int | None = None,
        cpus: float | None = None,
        memory_mb: int | None = None,
        network: str | None = None,
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> SandboxInfo: ...

    async def get(self, tenant_id: str, sandbox_id: str) -> SandboxInfo: ...

    async def exec(
        self,
        tenant_id: str,
        sandbox_id: str,
        argv: Sequence[str],
        *,
        cwd: str = "/workspace",
        env: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout_seconds: int | None = None,
    ) -> SandboxExecResult: ...

    async def write_file(
        self, tenant_id: str, sandbox_id: str, path: str, data: bytes
    ) -> None: ...

    async def read_file(self, tenant_id: str, sandbox_id: str, path: str) -> bytes: ...

    async def touch(self, tenant_id: str, sandbox_id: str) -> SandboxInfo: ...

    async def destroy(self, tenant_id: str, sandbox_id: str) -> None: ...

    async def aclose(self) -> None: ...


class SandboxClient:
    """workspace-service 沙箱 HTTP 客户端:服务间 Bearer,租户在 token claim 里。"""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        token_provider: Callable[[str], Awaitable[str]],
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_seconds,
            transport=transport,
            event_hooks={"request": [inject_request_headers]},
        )
        self._token_provider = token_provider

    async def create(
        self,
        tenant_id: str,
        *,
        kind: str = "generic",
        image: str | None = None,
        ttl_seconds: int | None = None,
        cpus: float | None = None,
        memory_mb: int | None = None,
        network: str | None = None,
        workspace_id: str | None = None,
        run_id: str | None = None,
    ) -> SandboxInfo:
        """创建沙箱:只传非 None 字段,让下游用自己的默认值。"""
        payload: dict[str, Any] = {"kind": kind}
        if image is not None:
            payload["image"] = image
        if ttl_seconds is not None:
            payload["ttl_seconds"] = ttl_seconds
        if cpus is not None:
            payload["cpus"] = cpus
        if memory_mb is not None:
            payload["memory_mb"] = memory_mb
        if network is not None:
            payload["network"] = network
        if workspace_id is not None:
            payload["workspace_id"] = workspace_id
        if run_id is not None:
            payload["run_id"] = run_id
        response = await self._request(
            "POST", "/v1/sandboxes", tenant_id, json=payload, expected=201
        )
        return SandboxInfo.from_payload(self._decode(response))

    async def get(self, tenant_id: str, sandbox_id: str) -> SandboxInfo:
        """按 id 读沙箱(租户由 token claim 决定,越权一律 404)。"""
        response = await self._request(
            "GET", f"/v1/sandboxes/{sandbox_id}", tenant_id, expected=200
        )
        return SandboxInfo.from_payload(self._decode(response))

    async def exec(
        self,
        tenant_id: str,
        sandbox_id: str,
        argv: Sequence[str],
        *,
        cwd: str = "/workspace",
        env: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout_seconds: int | None = None,
    ) -> SandboxExecResult:
        """在沙箱内执行命令;timeout_seconds 交给下游做服务端 cap。"""
        payload: dict[str, Any] = {"argv": list(argv), "cwd": cwd}
        if env is not None:
            payload["env"] = dict(env)
        if stdin is not None:
            payload["stdin"] = stdin
        if timeout_seconds is not None:
            payload["timeout_seconds"] = timeout_seconds
        response = await self._request(
            "POST",
            f"/v1/sandboxes/{sandbox_id}/exec",
            tenant_id,
            json=payload,
            expected=200,
        )
        return SandboxExecResult.from_payload(self._decode(response))

    async def write_file(
        self, tenant_id: str, sandbox_id: str, path: str, data: bytes
    ) -> None:
        """写文件:正文 base64 编码后走 JSON(204 即成功)。"""
        payload = {
            "path": path,
            "content_base64": base64.b64encode(data).decode("ascii"),
        }
        await self._request(
            "PUT",
            f"/v1/sandboxes/{sandbox_id}/files",
            tenant_id,
            json=payload,
            expected=204,
        )

    async def read_file(self, tenant_id: str, sandbox_id: str, path: str) -> bytes:
        """读文件:base64 解码回原始字节。"""
        response = await self._request(
            "GET",
            f"/v1/sandboxes/{sandbox_id}/files",
            tenant_id,
            params={"path": path},
            expected=200,
        )
        payload = self._decode(response)
        try:
            return base64.b64decode(str(payload["content_base64"]), validate=True)
        except (KeyError, TypeError, ValueError) as exc:
            raise UpstreamError(
                UPSTREAM_ERROR_CODE,
                "沙箱文件响应不是合法 base64",
                details={"path": path},
            ) from exc

    async def touch(self, tenant_id: str, sandbox_id: str) -> SandboxInfo:
        """续期(滑动 TTL):返回更新后的沙箱描述。"""
        response = await self._request(
            "POST", f"/v1/sandboxes/{sandbox_id}/touch", tenant_id, expected=200
        )
        return SandboxInfo.from_payload(self._decode(response))

    async def destroy(self, tenant_id: str, sandbox_id: str) -> None:
        """销毁沙箱:204 视为成功(下游幂等)。"""
        await self._request(
            "DELETE", f"/v1/sandboxes/{sandbox_id}", tenant_id, expected=204
        )

    async def _request(
        self,
        method: str,
        path: str,
        tenant_id: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        expected: int,
    ) -> httpx.Response:
        """发一次内部请求:带 Bearer,统一映射传输层/非 2xx 失败。"""
        token = await self._token_provider(tenant_id)
        headers = {"Authorization": f"Bearer {token}"}
        try:
            response = await self._client.request(
                method, path, json=json, params=params, headers=headers
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(UNREACHABLE_CODE, f"沙箱服务不可达:{exc}") from exc
        if response.status_code != expected:
            raise UpstreamError(
                UPSTREAM_ERROR_CODE,
                f"沙箱服务返回 {response.status_code}",
                details={
                    "status": response.status_code,
                    "body": response.text[:ERROR_BODY_LIMIT],
                },
            )
        return response

    def _decode(self, response: httpx.Response) -> dict[str, Any]:
        """解析 JSON 对象响应;非法 JSON 也包 UpstreamError(不裸抛 ValueError)。"""
        try:
            payload = response.json()
        except ValueError as exc:
            raise UpstreamError(
                UPSTREAM_ERROR_CODE,
                "沙箱服务响应不是合法 JSON",
                details={
                    "status": response.status_code,
                    "body": response.text[:ERROR_BODY_LIMIT],
                },
            ) from exc
        if not isinstance(payload, dict):
            raise UpstreamError(
                UPSTREAM_ERROR_CODE,
                "沙箱服务响应不是 JSON 对象",
                details={
                    "status": response.status_code,
                    "body": response.text[:ERROR_BODY_LIMIT],
                },
            )
        return dict(payload)

    async def aclose(self) -> None:
        """关闭底层 HTTP 客户端。"""
        await self._client.aclose()
