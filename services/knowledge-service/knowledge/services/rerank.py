"""重排提供方(架构 §5 唯一出口):none = 关闭;gateway = 经 llm-gateway。

检索先用向量 + 关键词 RRF 粗排,再把前 N 条候选交给重排模型逐条打分:
RRF 只看排名、不懂语义,重排能把真正回答问题的块顶上来。

**降级是设计的一部分**:重排是增益不是依赖,网关不可达/超时/返回异常时
本模块抛 `UpstreamError`,由 `knowledge.services.knowledge.search` 捕获后
退回 RRF 顺序(响应里 `rerank="skipped"`);不把检索拖成 5xx。
"""

from __future__ import annotations

from typing import Literal, Protocol

import httpx
from common.errors import UpstreamError

from knowledge.services.embeddings import _identity_headers_hook

NONE_PROVIDER = "none"
GATEWAY_PROVIDER = "gateway"

#: 检索响应里的重排状态:applied = 顺序与分数都来自重排;skipped = 降级回原顺序
RerankStatus = Literal["applied", "skipped"]


class Reranker(Protocol):
    """重排协议:查询 + 候选正文进,与候选**等长同序**的相关性分数出。"""

    name: str
    model: str

    async def rerank(self, query: str, documents: list[str]) -> list[float]: ...


class GatewayReranker:
    """经 llm-gateway 重排:`POST /v1/rerank`(litellm 路由,§5)。"""

    name = GATEWAY_PROVIDER

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
        internal_secret: str = "",
    ) -> None:
        self.model = model
        # 网关 BYOK:账号头 + 内部密钥头(与嵌入同一条出站钩子)
        self.internal_secret = internal_secret
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_seconds,
            transport=transport,
            event_hooks={"request": [_identity_headers_hook(internal_secret)]},
        )

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        """返回与 documents 等长同序的分数;网关按 index 回报,缺的补 0。"""
        if not documents:
            return []
        try:
            response = await self._client.post(
                "/v1/rerank",
                json={"model": self.model, "query": query, "documents": documents},
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(
                "knowledge.rerank_unavailable", f"模型网关不可达:{exc}"
            ) from exc
        if response.status_code != 200:
            detail = _error_detail(response)
            raise UpstreamError(detail["code"], detail["message"])
        try:
            body = response.json()
        except ValueError as exc:
            # 200 但不是 JSON(常见于网关前面挂的 HTML 错误页):同样走降级
            raise UpstreamError(
                "knowledge.rerank_mismatch", "模型网关返回的不是合法 JSON"
            ) from exc
        return _scores(body, len(documents))

    async def aclose(self) -> None:
        await self._client.aclose()


def _scores(body: dict, count: int) -> list[float]:
    """把网关的 results 摊成与输入等长的分数数组(顺序由调用方按原下标对齐)。

    网关只回 top_n 条时,未命中的候选补 0——它们排在命中之后,语义正确;
    下标越界属于契约破坏,必须报错而不是静默丢结果。
    """
    scores = [0.0] * count
    results = body.get("results") or []
    if not results:
        raise UpstreamError("knowledge.rerank_mismatch", "重排返回空结果")
    for item in results:
        try:
            index = int(item.get("index", -1))
            score = float(item.get("relevance_score") or 0.0)
        except (TypeError, ValueError) as exc:
            raise UpstreamError(
                "knowledge.rerank_mismatch", "重排返回的下标或分数不是数值"
            ) from exc
        if index < 0 or index >= count:
            raise UpstreamError(
                "knowledge.rerank_mismatch",
                "重排返回的下标超出候选范围",
                details={"index": index, "documents": count},
            )
        scores[index] = score
    return scores


def _error_detail(response: httpx.Response) -> dict[str, str]:
    try:
        body = response.json()
    except ValueError:
        body = {}
    return {
        "code": body.get("code") or "knowledge.rerank_failed",
        "message": body.get("message") or f"模型网关返回 {response.status_code}",
    }


def build_reranker(
    provider: str,
    *,
    model: str,
    gateway_url: str,
    timeout_seconds: float = 20.0,
    internal_secret: str = "",
) -> Reranker | None:
    """按配置装配重排提供方;none = 关闭(返回 None),未知值启动即报错。"""
    if provider == NONE_PROVIDER:
        return None
    if provider == GATEWAY_PROVIDER:
        return GatewayReranker(
            gateway_url,
            model,
            timeout_seconds=timeout_seconds,
            internal_secret=internal_secret,
        )
    raise ValueError(f"未知的重排提供方:{provider}(支持 none/gateway)")
