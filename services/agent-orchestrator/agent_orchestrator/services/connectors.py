"""connector-service HTTP 客户端:工具执行的唯一出口(架构 §8)。

上层(工具注册表)不感知 HTTP 细节;测试装配 FakeConnector(duck typing),零网络。
任何失败统一映射 UpstreamError(502)——由注册表回喂给模型,而非中断图。
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx
from common.errors import UpstreamError


class Connector(Protocol):
    """连接器服务接口:列连接 + 读邮件(只读)+ 发信(写操作)。"""

    async def list_connections(self, tenant_id: str) -> list[dict[str, Any]]: ...

    async def fetch_emails(
        self, tenant_id: str, connection_id: str, limit: int
    ) -> list[dict[str, Any]]: ...

    async def send_email(
        self,
        tenant_id: str,
        connection_id: str,
        to: str,
        subject: str,
        body: str,
    ) -> str: ...


class ConnectorClient:
    """connector-service HTTP 客户端:租户经 X-Tenant-Id 头传递。"""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=timeout_seconds, transport=transport
        )

    async def _get(
        self, path: str, tenant_id: str, params: dict[str, Any] | None = None
    ) -> Any:
        try:
            resp = await self._client.get(
                path, params=params, headers={"X-Tenant-Id": tenant_id}
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(
                "connector.upstream_error", f"连接器服务不可达:{exc}"
            ) from exc
        if resp.status_code != 200:
            raise UpstreamError(
                "connector.upstream_error",
                f"连接器服务返回 {resp.status_code}",
                details={"body": resp.text[:200]},
            )
        return resp.json()

    async def list_connections(self, tenant_id: str) -> list[dict[str, Any]]:
        return list(await self._get("/v1/connections", tenant_id))

    async def fetch_emails(
        self, tenant_id: str, connection_id: str, limit: int
    ) -> list[dict[str, Any]]:
        return list(
            await self._get(
                f"/v1/connections/{connection_id}/emails",
                tenant_id,
                params={"limit": limit},
            )
        )

    async def send_email(
        self,
        tenant_id: str,
        connection_id: str,
        to: str,
        subject: str,
        body: str,
    ) -> str:
        """发信(写操作):调用方必须先拿到人工放行(§6.2)。"""
        payload = {"to": to, "subject": subject, "body": body}
        try:
            resp = await self._client.post(
                f"/v1/connections/{connection_id}/emails",
                json=payload,
                headers={"X-Tenant-Id": tenant_id},
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(
                "connector.upstream_error", f"连接器服务不可达:{exc}"
            ) from exc
        if resp.status_code != 200:
            raise UpstreamError(
                "connector.send_failed",
                f"连接器发信失败 {resp.status_code}",
                details={"body": resp.text[:200]},
            )
        return str(resp.json().get("detail") or f"已发送至 {to}")

    async def aclose(self) -> None:
        await self._client.aclose()
