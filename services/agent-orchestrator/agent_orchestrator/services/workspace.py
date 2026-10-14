"""workspace-service HTTP 客户端:交付物镜像到工作区(§7 归属方向)。

阶段 A 的桥梁:orchestrator 仍保留自己的一份交付物(读契约不变),
**同时**把元数据+正文推给 workspace-service(§7 里 artifacts 归属 workspace);
阶段 B 切为以 workspace 为准、读路径走 workspace。测试装配 FakeWorkspace。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx
from common.errors import UpstreamError


class Workspace(Protocol):
    """工作区接口:登记交付物。"""

    async def create_artifact(
        self,
        tenant_id: str,
        *,
        title: str,
        kind: str,
        content: str,
        run_id: str | None = None,
    ) -> dict[str, Any]: ...


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

    async def create_artifact(
        self,
        tenant_id: str,
        *,
        title: str,
        kind: str,
        content: str,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {"title": title, "kind": kind, "content": content, "run_id": run_id}
        headers = {"Authorization": f"Bearer {await self._token_provider(tenant_id)}"}
        try:
            response = await self._client.post(
                "/v1/artifacts",
                json=payload,
                headers=headers,
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

    async def aclose(self) -> None:
        await self._client.aclose()
