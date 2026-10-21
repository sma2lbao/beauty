"""workspace-service HTTP 客户端:交付物镜像 + 通用二进制对象(P3 技能包)。

阶段 A 的桥梁:orchestrator 仍保留自己的一份交付物(读契约不变),
**同时**把元数据+正文推给 workspace-service(§7 里 artifacts 归属 workspace);
阶段 B 切为以 workspace 为准、读路径走 workspace。测试装配 FakeWorkspace。

通用对象 API(P3-A):POST/GET/DELETE /v1/objects,JSON 里 base64 传字节,
供 skill 包 zip 这类二进制内容存储;key 由服务端生成(租户前缀),orchestrator
只保存引用。测试注入 FakeWorkspace,零网络。
"""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx
from common.errors import UpstreamError


class Workspace(Protocol):
    """工作区接口:登记交付物 + 通用对象读写(skill 包二进制)。"""

    async def create_artifact(
        self,
        tenant_id: str,
        *,
        title: str,
        kind: str,
        content: str | None = None,
        run_id: str | None = None,
        filename: str | None = None,
        content_type: str | None = None,
        data: bytes | None = None,
    ) -> dict[str, Any]: ...

    async def put_object(
        self,
        tenant_id: str,
        data: bytes,
        *,
        filename: str | None = None,
        content_type: str = "application/zip",
    ) -> dict[str, Any]: ...

    async def get_object(self, tenant_id: str, key: str) -> bytes: ...

    async def delete_object(self, tenant_id: str, key: str) -> None: ...


class WorkspaceClient:
    """workspace-service HTTP 客户端:服务间 Bearer,租户在 token claim 里。"""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        token_provider: Callable[[str], Awaitable[str]],
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=timeout_seconds, transport=transport
        )
        self._token_provider = token_provider

    async def _headers(self, tenant_id: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self._token_provider(tenant_id)}"}

    async def create_artifact(
        self,
        tenant_id: str,
        *,
        title: str,
        kind: str,
        content: str | None = None,
        run_id: str | None = None,
        filename: str | None = None,
        content_type: str | None = None,
        data: bytes | None = None,
    ) -> dict[str, Any]:
        """登记交付物:文本走 content,二进制走 content_base64(二选一)。

        文件名/类型是给下载端点的元数据(workspace-service 侧 filename/content_type);
        镜像二进制件时传 data,文本报告仍传 content,两者不会同时出现。
        """
        payload: dict[str, Any] = {"title": title, "kind": kind, "run_id": run_id}
        if data is not None:
            payload["content_base64"] = base64.b64encode(data).decode("ascii")
            payload["content_type"] = content_type or "application/octet-stream"
            payload["filename"] = filename
        else:
            payload["content"] = content or ""
            payload["content_type"] = content_type
            payload["filename"] = filename
        try:
            response = await self._client.post(
                "/v1/artifacts",
                json=payload,
                headers=await self._headers(tenant_id),
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(
                "workspace.upstream_error", f"工作区服务不可达:{exc}"
            ) from exc
        if response.status_code != 201:
            raise UpstreamError(
                "workspace.upstream_error",
                f"工作区服务返回 {response.status_code}",
                details={"body": response.text[:200]},
            )
        return dict(response.json())

    # ---- 通用对象 API(P3-A:POST/GET/DELETE /v1/objects) ----

    async def put_object(
        self,
        tenant_id: str,
        data: bytes,
        *,
        filename: str | None = None,
        content_type: str = "application/zip",
    ) -> dict[str, Any]:
        """上传二进制对象;返回 {key, size_bytes, sha256}(key 由服务端生成)。"""
        payload = {
            "filename": filename,
            "content_type": content_type,
            "content_base64": base64.b64encode(data).decode("ascii"),
        }
        try:
            response = await self._client.post(
                "/v1/objects", json=payload, headers=await self._headers(tenant_id)
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(
                "workspace.upstream_error", f"工作区服务不可达:{exc}"
            ) from exc
        if response.status_code != 201:
            raise UpstreamError(
                "workspace.upstream_error",
                f"工作区对象存储返回 {response.status_code}",
                details={"body": response.text[:200]},
            )
        return dict(response.json())

    async def get_object(self, tenant_id: str, key: str) -> bytes:
        """按 key 读二进制对象;越权/不存在一律上游错误(不猜存在性)。"""
        try:
            response = await self._client.get(
                "/v1/objects",
                params={"key": key},
                headers=await self._headers(tenant_id),
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(
                "workspace.upstream_error", f"工作区服务不可达:{exc}"
            ) from exc
        if response.status_code != 200:
            raise UpstreamError(
                "workspace.upstream_error",
                f"工作区对象读取返回 {response.status_code}",
                details={"body": response.text[:200]},
            )
        return base64.b64decode(response.json()["content_base64"])

    async def delete_object(self, tenant_id: str, key: str) -> None:
        """删除对象(服务端幂等:不存在/越权也是 204)。"""
        try:
            response = await self._client.delete(
                "/v1/objects",
                params={"key": key},
                headers=await self._headers(tenant_id),
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(
                "workspace.upstream_error", f"工作区服务不可达:{exc}"
            ) from exc
        if response.status_code not in (200, 204):
            raise UpstreamError(
                "workspace.upstream_error",
                f"工作区对象删除返回 {response.status_code}",
                details={"body": response.text[:200]},
            )

    async def aclose(self) -> None:
        await self._client.aclose()
