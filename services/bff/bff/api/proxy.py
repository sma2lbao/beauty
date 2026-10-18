"""同源代理与文件下载(M0-06)。

- SPA 只访问 /api/*;BFF 注入 Bearer 后转发到内部服务;
- 下游 401 时用 Cookie 里的 refresh token 自动续期并重试一次;
- SSE 走流式转发(不缓冲),仍支持 Last-Event-ID;
- 所有业务路由都要求 BFF 会话;写请求要求 CSRF double-submit。
"""

from __future__ import annotations

import base64
import re
from collections.abc import AsyncIterator
from typing import Annotated, Any
from urllib.parse import quote

import httpx
from common.errors import NotFoundError, UnauthorizedError, UpstreamError
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from bff.api.deps import (
    get_client,
    get_codec,
    get_proxy_auth,
    get_session_state,
    get_settings,
    require_csrf,
)
from bff.core.config import Settings
from bff.core.security import SessionCodec
from bff.schemas.auth import StreamTokenRequest, StreamTokenResponse
from bff.services import account as account_service

router = APIRouter()

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_IMAGE_DATA = re.compile(
    r"^data:(image/(?:png|jpe?g|webp|gif));base64,([A-Za-z0-9+/=\s]+)$", re.I
)
_EXT = {
    "text/markdown": "md",
    "text/x-patch": "patch",
    "application/json": "json",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}


def _base(url: str) -> str:
    return url.rstrip("/")


def _resolve_upstream(settings: Settings, path: str) -> str | None:
    if path.startswith("v1/"):
        return _base(settings.runtime_base_url) + "/api/" + path
    if path.startswith(("account/", "workspace/", "knowledge/")):
        prefix, _, rest = path.partition("/")
        base = {
            "account": settings.account_base_url,
            "workspace": settings.workspace_base_url,
            "knowledge": settings.knowledge_base_url,
        }[prefix]
        if rest.startswith("v1/"):
            return _base(base) + "/" + rest
        return _base(base) + "/v1/" + rest
    return None


def _headers(request: Request, state: dict[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {"Authorization": f"Bearer {state['access_token']}"}
    for name in ("content-type", "accept", "last-event-id"):
        value = request.headers.get(name)
        if value:
            headers[name] = value
    return headers


async def _refresh(
    client: httpx.AsyncClient,
    settings: Settings,
    codec: SessionCodec,
    state: dict[str, Any],
    response: Response,
) -> dict[str, Any]:
    if not state.get("refresh_token"):
        raise UnauthorizedError("auth.session_expired", "登录态已过期,请重新登录")
    fresh = await account_service.refresh(client, settings, state)
    response.set_cookie(
        settings.cookie_name,
        codec.encode(fresh),
        max_age=settings.cookie_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return fresh


@router.get("/api/session")
async def current_session(
    state: Annotated[dict[str, Any], Depends(get_session_state)],
) -> dict[str, Any]:
    """轻量探针:SPA 重启后确认 Cookie 是否仍有效。"""
    return {"authenticated": True, "user": state["user"]}


@router.post("/api/stream-token", response_model=StreamTokenResponse)
async def stream_token(
    body: StreamTokenRequest,
    state: Annotated[dict[str, Any], Depends(get_session_state)],
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_client)],
    _csrf: Annotated[None, Depends(require_csrf)],
) -> StreamTokenResponse:
    result = await account_service.issue_stream_token(
        client,
        settings,
        tenant_id=str(state["user"]["tenant_id"]),
        subject=str(state["user"]["subject"]),
        run_id=body.run_id,
    )
    return StreamTokenResponse.model_validate(result)


@router.get("/api/files/{artifact_id}")
async def download_file(
    artifact_id: str,
    response: Response,
    state: Annotated[dict[str, Any], Depends(get_session_state)],
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_client)],
    codec: Annotated[SessionCodec, Depends(get_codec)],
) -> Response:
    url = f"{_base(settings.workspace_base_url)}/v1/artifacts/{artifact_id}"
    headers = {"Authorization": f"Bearer {state['access_token']}"}
    upstream = await client.get(url, headers=headers)
    if upstream.status_code == 401:
        state = await _refresh(client, settings, codec, state, response)
        upstream = await client.get(
            url, headers={"Authorization": f"Bearer {state['access_token']}"}
        )
    if upstream.is_error:
        return JSONResponse(
            status_code=upstream.status_code,
            content=upstream.json()
            if upstream.headers.get("content-type", "").startswith("application/json")
            else {"code": "bff.download_failed", "message": "下载失败", "details": {}},
        )
    artifact = upstream.json()
    title = str(artifact.get("title") or "artifact")
    kind = str(artifact.get("kind") or "text")
    content = str(artifact.get("content") or "")
    image = _IMAGE_DATA.match(content.strip())
    if image:
        media_type = image.group(1).lower().replace("image/jpg", "image/jpeg")
        body = base64.b64decode(re.sub(r"\s", "", image.group(2)))
    else:
        media_type = {
            "markdown": "text/markdown",
            "diff": "text/x-patch",
            "patch": "text/x-patch",
            "json": "application/json",
        }.get(kind.lower(), "text/plain")
        body = content.encode("utf-8")
    extension = _EXT.get(media_type, "txt")
    safe_title = re.sub(r'[\\/:*?"<>|]', "_", title)
    if not safe_title.lower().endswith("." + extension):
        safe_title += "." + extension
    filename = quote(safe_title)
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "Cache-Control": "private, no-store",
        },
    )


@router.api_route(
    "/api/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def proxy(
    path: str,
    request: Request,
    response: Response,
    state: Annotated[dict[str, Any], Depends(get_proxy_auth)],
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_client)],
    codec: Annotated[SessionCodec, Depends(get_codec)],
) -> Response:
    if request.method not in SAFE_METHODS and not state.get("bearer"):
        require_csrf(request)
    target = _resolve_upstream(settings, path)
    if target is None:
        raise NotFoundError("bff.route_not_found", "BFF 未配置该路由")
    stream = (
        "text/event-stream" in request.headers.get("accept", "")
        or path.endswith("/events/stream")
        or path.endswith("/messages/stream")
    )
    if stream:
        return await _proxy_stream(
            request, target, state, settings, client, codec, response
        )
    upstream = await client.request(
        request.method,
        target,
        params=request.query_params,
        content=await request.body(),
        headers=_headers(request, state),
    )
    if upstream.status_code == 401:
        state = await _refresh(client, settings, codec, state, response)
        upstream = await client.request(
            request.method,
            target,
            params=request.query_params,
            content=await request.body(),
            headers=_headers(request, state),
        )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )


async def _proxy_stream(
    request: Request,
    target: str,
    state: dict[str, Any],
    settings: Settings,
    client: httpx.AsyncClient,
    codec: SessionCodec,
    response: Response,
) -> Response:
    headers = _headers(request, state)
    upstream_request = client.build_request(
        request.method,
        target,
        params=request.query_params,
        content=await request.body(),
        headers=headers,
    )
    try:
        upstream = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        raise UpstreamError("bff.stream_unreachable", "事件流上游不可达") from exc
    if upstream.status_code == 401:
        await upstream.aclose()
        state = await _refresh(client, settings, codec, state, response)
        upstream_request = client.build_request(
            request.method,
            target,
            params=request.query_params,
            headers=_headers(request, state),
        )
        upstream = await client.send(upstream_request, stream=True)
    if upstream.is_error:
        content = await upstream.aread()
        await upstream.aclose()
        return Response(content=content, status_code=upstream.status_code)

    async def frames() -> AsyncIterator[bytes]:
        async for chunk in upstream.aiter_bytes():
            yield chunk

    return StreamingResponse(
        frames(),
        status_code=upstream.status_code,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
        background=BackgroundTask(upstream.aclose),
    )
