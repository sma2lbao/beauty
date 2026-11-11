"""同源代理与文件下载(M0-06)。

- SPA 只访问 /api/*;API 网关注入 Bearer 后转发到内部服务;
- 下游 401 时用 Cookie 里的 refresh token 自动续期并重试一次;
- SSE 走流式转发(不缓冲),仍支持 Last-Event-ID;
- 所有业务路由都要求 API 网关会话;写请求要求 CSRF double-submit;
- 观测:每个上游调用打一条 `proxy_call`(upstream/route/status/duration_ms/stream)
  并记 `http_upstream_*` 指标;路由模板经 `request.state.route_template` 交给
  访问日志中间件,span 名与 `http.route` 同步覆盖 catch-all 的模板
  (见 docs/observability.md §6/§9/§10)。
"""

from __future__ import annotations

import base64
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Any
from urllib.parse import quote

import httpx
from common import metrics, tracing
from common.errors import NotFoundError, UnauthorizedError, UpstreamError
from common.logging import bind_request_context, current_context, get_logger
from common.middleware import (
    REQUEST_ID_HEADER,
    TRACEPARENT_HEADER,
    current_traceparent,
    normalize_path_template,
)
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from api_gateway.api.deps import (
    get_client,
    get_codec,
    get_proxy_auth,
    get_session_state,
    get_settings,
    require_csrf,
)
from api_gateway.core.config import Settings
from api_gateway.core.security import SessionCodec
from api_gateway.schemas.auth import StreamTokenRequest, StreamTokenResponse
from api_gateway.services import account as account_service

router = APIRouter()
log = get_logger("proxy")

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


@dataclass(frozen=True, slots=True)
class Upstream:
    """一次代理的解析结果:低基数服务名 + 上游绝对 URL。"""

    service: str
    url: str


#: 前缀表(声明式):网关路径首段 → (低基数服务名, Settings 里的 base_url 字段)。
#: runtime 与 connector 有各自的拼接规则(见 `resolve_upstream`),不在这张表里。
_SERVICE_BASES: dict[str, tuple[str, str]] = {
    "account": ("account", "account_base_url"),
    "workspace": ("workspace", "workspace_base_url"),
    "knowledge": ("knowledge", "knowledge_base_url"),
}


def _base(url: str) -> str:
    return url.rstrip("/")


def _versioned(rest: str) -> str:
    """服务内路径:客户端显式写 v1/ 时按原样透传,否则补 /v1/。"""
    return "/" + rest if rest.startswith("v1/") else "/v1/" + rest


def resolve_upstream(settings: Settings, path: str) -> Upstream | None:
    """把网关内的相对路径解析成上游地址;未配置的前缀返回 None(404)。"""
    if path.startswith("v1/"):
        # 前端把 runtime 的 /api/v1/* 直接拼在 /api 后面(runtime 自身路由带 /api)。
        return Upstream("runtime", _base(settings.runtime_base_url) + "/api/" + path)
    if path == "models":
        return Upstream(
            "llm-gateway", _base(settings.llm_gateway_base_url) + "/v1/models"
        )
    if path.startswith(("usage/", "byok")):
        return Upstream("account", _base(settings.account_base_url) + "/v1/" + path)
    if path.startswith("connector/"):
        rest = path.removeprefix("connector/")
        # 前端只写服务内路径(/api/connector/connections);OAuth 回跳 URL 由
        # connector-service 拼成 .../api/connector/v1/oauth/... 再回本网关,
        # 带 v1/ 时按原样转发(与 account/workspace/knowledge 同口径)。
        return Upstream(
            "connector", _base(settings.connector_base_url) + _versioned(rest)
        )
    prefix, _, rest = path.partition("/")
    entry = _SERVICE_BASES.get(prefix)
    if entry is None:
        return None
    service, field = entry
    return Upstream(service, _base(getattr(settings, field)) + _versioned(rest))


def _headers(request: Request, state: dict[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {"Authorization": f"Bearer {state['access_token']}"}
    for name in ("content-type", "accept", "last-event-id"):
        value = request.headers.get(name)
        if value:
            headers[name] = value
    # 观测链路透传(docs/observability.md §5):request_id 与 traceparent
    context = current_context()
    request_id = context.get("request_id")
    if request_id:
        headers[REQUEST_ID_HEADER] = str(request_id)
    traceparent = current_traceparent()
    if traceparent:
        # 开追踪时 httpx 插桩会把这里换成下游 client span 的 traceparent
        # (trace-id 不变、span-id 换新);这里保留的是追踪关闭时的透传兜底。
        headers[TRACEPARENT_HEADER] = str(traceparent)
    return headers


def _record_proxy(
    settings: Settings,
    *,
    upstream: str,
    route: str,
    method: str,
    status: int,
    began: float,
    stream: bool,
) -> None:
    """记一次上游调用的指标与 `proxy_call` 日志(401 重试会各记一次)。"""
    duration = max(time.perf_counter() - began, 0.0)
    metrics.observe_upstream(
        service=settings.service_name,
        upstream=upstream,
        route=route,
        status=status,
        duration_seconds=duration,
    )
    fields: dict[str, Any] = {
        "upstream": upstream,
        "route": route,
        "method": method,
        "status": status,
        # 流式请求只记到响应头(TTFB),整条流的时长由中间件的 sse_closed 记录。
        "duration_ms": round(duration * 1000, 1),
        "stream": stream,
    }
    if status >= 500:
        log.error("proxy_call", **fields)
    else:
        log.info("proxy_call", **fields)


async def _forward(
    client: httpx.AsyncClient,
    request: Request,
    state: dict[str, Any],
    url: str,
) -> httpx.Response:
    return await client.request(
        request.method,
        url,
        params=request.query_params,
        content=await request.body(),
        headers=_headers(request, state),
    )


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
        account_id=str(state["user"]["account_id"]),
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
            else {
                "code": "api_gateway.download_failed",
                "message": "下载失败",
                "details": {},
            },
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
    upstream = resolve_upstream(settings, path)
    if upstream is None:
        raise NotFoundError("api_gateway.route_not_found", "API 网关未配置该路由")
    # catch-all 路由下 FastAPI 只知道 /api/{path:path},这里补回真实业务模板:
    # 访问日志(经 request.state)、RED 指标、span 名与 http.route 共用同一个值。
    route = normalize_path_template("/api/" + path)
    request.state.route_template = route
    request.state.upstream = upstream.service
    bind_request_context(upstream=upstream.service)
    tracing.update_current_span(
        f"{request.method} {route}",
        {"http.route": route, "upstream": upstream.service},
    )
    # SSE 端点一律流式转发:客户端的 Accept 可能只是 application/json
    # (手写 fetch 的 SSE 客户端),只按 Accept 判断会把长连接缓冲到读超时。
    stream = "text/event-stream" in request.headers.get("accept", "") or path.endswith(
        "/stream"
    )
    if stream:
        return await _proxy_stream(
            request, upstream, route, state, settings, client, codec, response
        )
    began = time.perf_counter()
    proxied = await _forward(client, request, state, upstream.url)
    _record_proxy(
        settings,
        upstream=upstream.service,
        route=route,
        method=request.method,
        status=proxied.status_code,
        began=began,
        stream=False,
    )
    if proxied.status_code == 401:
        state = await _refresh(client, settings, codec, state, response)
        began = time.perf_counter()
        proxied = await _forward(client, request, state, upstream.url)
        _record_proxy(
            settings,
            upstream=upstream.service,
            route=route,
            method=request.method,
            status=proxied.status_code,
            began=began,
            stream=False,
        )
    passthrough = {}
    for header in ("content-disposition", "cache-control"):
        value = proxied.headers.get(header)
        if value:
            passthrough[header] = value
    return Response(
        content=proxied.content,
        status_code=proxied.status_code,
        media_type=proxied.headers.get("content-type"),
        headers=passthrough,
    )


async def _proxy_stream(
    request: Request,
    upstream: Upstream,
    route: str,
    state: dict[str, Any],
    settings: Settings,
    client: httpx.AsyncClient,
    codec: SessionCodec,
    response: Response,
) -> Response:
    headers = _headers(request, state)
    upstream_request = client.build_request(
        request.method,
        upstream.url,
        params=request.query_params,
        content=await request.body(),
        headers=headers,
    )
    began = time.perf_counter()
    try:
        proxied = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        raise UpstreamError(
            "api_gateway.stream_unreachable", "事件流上游不可达"
        ) from exc
    _record_proxy(
        settings,
        upstream=upstream.service,
        route=route,
        method=request.method,
        status=proxied.status_code,
        began=began,
        stream=True,
    )
    if proxied.status_code == 401:
        await proxied.aclose()
        state = await _refresh(client, settings, codec, state, response)
        upstream_request = client.build_request(
            request.method,
            upstream.url,
            params=request.query_params,
            headers=_headers(request, state),
        )
        began = time.perf_counter()
        proxied = await client.send(upstream_request, stream=True)
        _record_proxy(
            settings,
            upstream=upstream.service,
            route=route,
            method=request.method,
            status=proxied.status_code,
            began=began,
            stream=True,
        )
    if proxied.is_error:
        content = await proxied.aread()
        await proxied.aclose()
        return Response(content=content, status_code=proxied.status_code)

    async def frames() -> AsyncIterator[bytes]:
        async for chunk in proxied.aiter_bytes():
            yield chunk

    return StreamingResponse(
        frames(),
        status_code=proxied.status_code,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
        background=BackgroundTask(proxied.aclose),
    )
