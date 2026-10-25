"""健康检查:根探针 + 同源 `/api/health`(M0-01 验收点)+ 就绪探针。

就绪语义(BFF 无 DB,与 service_kit 契约一致,见 RFC-005 §R-37):
- 判据是**关键配置完整**:session_secret 已从默认值覆盖(Cookie 会话可加密)、
  internal_secret 非空(内部签发/stream token 可用)——缺任一即 503;
- 可选上游探测 READY_PROBE_UPSTREAMS(默认 false):开启时逐个 GET 上游 /healthz,
  任一不可达即 503;默认关闭,避免级联判定(上游抖动不该把 BFF 打成不就绪)。
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request

from bff.core.config import Settings

health_router = APIRouter()

# 部署环境必须覆盖的本地兜底默认值(见 core/config.py session_secret)
_DEV_DEFAULT_SESSION_SECRET = "dev-bff-session-secret-change-me"


@health_router.get("/healthz")
async def healthz() -> dict[str, str]:
    """存活探针:进程活着即 200。"""
    return {"status": "ok"}


@health_router.get("/api/health")
async def api_health() -> dict[str, str]:
    """部署 smoke 用:经 Nginx 同源访问的 BFF 健康检查。"""
    return {"status": "ok", "service": "bff"}


def _missing_config(settings: Settings) -> str | None:
    """就绪前置:关键配置完整;返回 None 表示通过,否则返回原因。"""
    if not settings.session_secret or settings.session_secret == (
        _DEV_DEFAULT_SESSION_SECRET
    ):
        return "session_secret is empty or left at dev default"
    if not settings.internal_secret:
        return "internal_secret is empty"
    return None


async def _probe_upstream(client: httpx.AsyncClient, name: str, url: str) -> None:
    """上游 /healthz 探测:不可达/非 200 即抛(统一映射 503)。"""
    try:
        response = await client.get(f"{url.rstrip('/')}/healthz", timeout=1.0)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"upstream {name} unreachable: {exc}") from exc
    if response.status_code != 200:
        raise RuntimeError(f"upstream {name} returned {response.status_code}")


@health_router.get("/readyz")
async def readyz(request: Request) -> dict[str, str]:
    """就绪探针:关键配置完整;可选上游探测(READY_PROBE_UPSTREAMS)。"""
    settings: Settings = request.app.state.settings
    reason = _missing_config(settings)
    if reason is not None:
        raise HTTPException(status_code=503, detail=reason)
    if settings.ready_probe_upstreams:
        client: httpx.AsyncClient = request.app.state.upstream_client
        for name, url in (
            ("runtime", settings.runtime_base_url),
            ("account", settings.account_base_url),
            ("workspace", settings.workspace_base_url),
            ("knowledge", settings.knowledge_base_url),
        ):
            try:
                await _probe_upstream(client, name, url)
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ready"}
