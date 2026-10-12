"""嵌入提供方(架构 §5 唯一出口):local 词法近似 / gateway 经模型网关。

- `local`(默认):确定性分词哈希向量,零依赖、零网络——让骨架跑通全链路;
  **它不是语义嵌入**(近义词不会靠近),生产必须换 `gateway`;
- `gateway`:POST llm-gateway `/v1/embeddings`(litellm 路由),失败统一 502。
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

import httpx
from common.errors import UpstreamError

LOCAL_PROVIDER = "local"
GATEWAY_PROVIDER = "gateway"
LOCAL_DIMENSION = 256

_WORD = re.compile(r"[a-z0-9]+")
_CJK = re.compile(r"[\u4e00-\u9fff]")


class EmbeddingProvider(Protocol):
    """向量化协议:文本批量进,向量批量出(顺序一致、维度一致)。"""

    name: str
    model: str

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


def tokenize(text: str) -> list[str]:
    """分词:拉丁词/数字整体,中文按字(阶段 A 的朴素切分)。"""
    lowered = text.lower()
    return _WORD.findall(lowered) + _CJK.findall(lowered)


class LocalHashingEmbeddingProvider:
    """确定性词法近似向量:token 哈希到固定维度并 L2 归一化。"""

    name = LOCAL_PROVIDER

    def __init__(self, model: str = "hashing-256", dimension: int = LOCAL_DIMENSION):
        self.model = model
        self.dimension = dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token in tokenize(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest()
            vector[int.from_bytes(digest, "big") % self.dimension] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class GatewayEmbeddingProvider:
    """经 llm-gateway 取嵌入:平台内不直连模型提供商(§5)。"""

    name = GATEWAY_PROVIDER

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=timeout_seconds, transport=transport
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = await self._client.post(
                "/v1/embeddings", json={"model": self.model, "input": texts}
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(
                "knowledge.embedding_unavailable", f"模型网关不可达:{exc}"
            ) from exc
        if response.status_code != 200:
            detail = _error_detail(response)
            raise UpstreamError(detail["code"], detail["message"])
        vectors = response.json().get("embeddings") or []
        if len(vectors) != len(texts):
            raise UpstreamError(
                "knowledge.embedding_mismatch",
                "嵌入返回条数与输入不一致",
                details={"expected": len(texts), "got": len(vectors)},
            )
        return [[float(value) for value in vector] for vector in vectors]

    async def aclose(self) -> None:
        await self._client.aclose()


def _error_detail(response: httpx.Response) -> dict[str, str]:
    try:
        body = response.json()
    except ValueError:
        body = {}
    return {
        "code": body.get("code") or "knowledge.embedding_failed",
        "message": body.get("message") or f"模型网关返回 {response.status_code}",
    }


def build_embedder(
    provider: str, *, model: str, gateway_url: str, timeout_seconds: float = 60.0
) -> EmbeddingProvider:
    """按配置装配嵌入提供方;未知配置在启动时就报错(不带病上线)。"""
    if provider == LOCAL_PROVIDER:
        return LocalHashingEmbeddingProvider()
    if provider == GATEWAY_PROVIDER:
        return GatewayEmbeddingProvider(
            gateway_url, model, timeout_seconds=timeout_seconds
        )
    raise ValueError(f"未知的嵌入提供方:{provider}(支持 local/gateway)")


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """余弦相似度;维度不一致或含零向量时返回 0(不抛错,排序更稳)。"""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
