"""语义嵌入 Provider(长期记忆 P2):本地确定性哈希 + llm-gateway /v1/embeddings。

分层约定(架构 §5):模型能力唯一出口是 llm-gateway,orchestrator 自己不发
模型请求;Provider 只负责"文本 → 向量"这件事,失败统一抛
:class:`EmbeddingUnavailable`,由调用方决定降级(记忆检索回退本地哈希,
run 不因嵌入服务抖动而失败)。开关/模型名由 core/config.py 提供,container
装配时 build_embedding_provider + configure_embedding 固定一次。
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

import httpx
from common.logging import current_context, get_logger
from common.security import SECRET_HEADER, TENANT_HEADER

from agent_orchestrator.core.config import Settings

logger = get_logger(__name__)

#: 本地哈希模型的稳定标识:与 Memory.embedding_model 的默认值保持一致。
EMBEDDING_MODEL_LOCAL = "local-hash-256"
#: 本地向量维度:256 维在召回质量与存储/计算成本之间取平衡。
EMBEDDING_DIMENSIONS_LOCAL = 256
#: 网关单次请求的输入上限:llm-gateway 的 /v1/embeddings 契约是 1..64 条。
GATEWAY_BATCH_SIZE = 64

_WORD_RE = re.compile(r"[a-z0-9_]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def tokenize(text: str) -> list[str]:
    """切词:拉丁词整体保留,中文按相邻 bigram(单字段落保留单字)。

    向量召回与词面打分共用同一套词元,保证两者看到的是同一份内容;不引
    分词库,中文 bigram 对近义表述已足够,且切词结果可直接缓存比较。
    """
    lowered = text.lower()
    tokens = [f"w:{word}" for word in _WORD_RE.findall(lowered)]
    for run in _CJK_RE.findall(lowered):
        if len(run) == 1:
            tokens.append(f"c:{run}")
            continue
        tokens.extend(f"c:{run[index : index + 2]}" for index in range(len(run) - 1))
    return tokens


def local_embedding(text: str) -> list[float]:
    """本地哈希嵌入:同步纯函数,零网络、确定性、单位范数。

    每个词元经 blake2b 落到固定维度、按哈希位决定符号,等价于随机特征映射:
    相同/相近的 n-gram 落在相同维度,余弦能反映词面重合;空文本给零向量,
    让余弦/召回逻辑不需要额外的 None 分支。
    """
    vector = [0.0] * EMBEDDING_DIMENSIONS_LOCAL
    for token in tokenize(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSIONS_LOCAL
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


@runtime_checkable
class EmbeddingProvider(Protocol):
    """嵌入提供方扩展点:只做文本→向量,不碰事务与存储。"""

    model: str

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class EmbeddingUnavailable(Exception):
    """嵌入提供方不可用(网络/非 200/响应不合约);调用方决定是否回退。"""


class LocalHashProvider:
    """进程内确定性哈希嵌入(零网络):默认实现,也是所有降级路径的终点。"""

    model = EMBEDDING_MODEL_LOCAL

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [local_embedding(text) for text in texts]


class GatewayEmbeddingProvider:
    """llm-gateway /v1/embeddings 客户端:失败一律抛 EmbeddingUnavailable。

    不重试:嵌入只是检索的增强项,一次失败直接回退本地比等待重试更划算;
    超时由 AsyncClient 统一控制,向量归属用返回里的 model 字段记录。
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
        internal_secret: str = "",
    ) -> None:
        self.model = model
        self._base_url = base_url.rstrip("/")
        # 注入的 client 归调用方关闭(MockTransport/连接池复用);自建的在这里关。
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None
        self.internal_secret = internal_secret

    def _identity_headers(self) -> dict[str, str]:
        """BYOK 身份头:租户 + 内部密钥(密钥留空只带租户)。"""
        headers: dict[str, str] = {}
        tenant_id = current_context().get("tenant_id")
        if tenant_id:
            headers[TENANT_HEADER] = str(tenant_id)
        if self.internal_secret:
            headers[SECRET_HEADER] = self.internal_secret
        return headers

    async def aclose(self) -> None:
        """关闭自建的 HTTP 客户端;注入的 client 不越权关闭。"""
        if self._owns_client:
            await self._client.aclose()

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """分批嵌入:网关单请求最多 64 条,超过时按顺序切块拼接。"""
        items = list(texts)
        vectors: list[list[float]] = []
        for start in range(0, len(items), GATEWAY_BATCH_SIZE):
            batch = items[start : start + GATEWAY_BATCH_SIZE]
            vectors.extend(await self._embed_batch(batch))
        return vectors

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        try:
            response = await self._client.post(
                f"{self._base_url}/v1/embeddings",
                json={"model": self.model, "input": texts},
                headers=self._identity_headers(),
            )
        except httpx.HTTPError as exc:
            raise EmbeddingUnavailable(f"嵌入网关不可达: {exc}") from exc
        if response.status_code != 200:
            raise EmbeddingUnavailable(
                f"嵌入网关返回 {response.status_code}: {response.text[:200]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise EmbeddingUnavailable("嵌入网关响应不是合法 JSON") from exc
        raw = payload.get("embeddings") if isinstance(payload, dict) else None
        if not isinstance(raw, list) or len(raw) != len(texts):
            raise EmbeddingUnavailable("嵌入网关返回的条数与输入不一致")
        vectors: list[list[float]] = []
        for item in raw:
            if not isinstance(item, list) or not item:
                raise EmbeddingUnavailable("嵌入网关返回的向量不是非空数组")
            vector: list[float] = []
            for value in item:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise EmbeddingUnavailable("嵌入网关返回的向量含非数值元素")
                vector.append(float(value))
            vectors.append(vector)
        return vectors


#: 本地默认提供方单例:未配置时所有路径共用(无状态,可安全复用)。
_DEFAULT_LOCAL = LocalHashProvider()

#: 进程内当前提供方;None 表示未配置(按本地哈希),reset 后回到该状态。
_provider: EmbeddingProvider | None = None


def configure_embedding(provider: EmbeddingProvider | None) -> None:
    """固定本进程的嵌入提供方(container 装配时调用一次;None=本地)。"""
    global _provider
    _provider = provider


def current_provider() -> EmbeddingProvider:
    """当前提供方;未配置时返回本地哈希单例(零网络兜底)。"""
    return _provider if _provider is not None else _DEFAULT_LOCAL


def reset_embedding_provider() -> None:
    """复位到默认本地实现(测试隔离用,生产不做热切换)。"""
    global _provider
    _provider = None


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """按配置构造提供方:只有 gateway 且配了网关地址才出网,其余用本地。

    未知 provider 名/空网关地址都静默回退本地:配置写错不应让记忆能力整体
    失效,本地哈希仍能撑住词面召回。
    """
    name = settings.memory_embedding_provider.strip().lower()
    gateway_url = settings.llm_gateway_url.strip()
    if name == "gateway" and gateway_url:
        return GatewayEmbeddingProvider(
            base_url=gateway_url,
            model=settings.memory_embedding_model,
            timeout_seconds=settings.memory_embedding_timeout_seconds,
            internal_secret=settings.internal_auth_secret,
        )
    return LocalHashProvider()


async def embed_many(
    texts: Sequence[str], *, fallback_local: bool = True
) -> tuple[list[list[float]], str]:
    """批量嵌入:返回 (向量列表, 实际使用的模型名)。

    提供方失败时按 fallback_local 决定:默认回退本地哈希并 warning——记忆
    检索不该被嵌入服务抖动拖垮;显式关闭时原样抛 EmbeddingUnavailable,
    供写路径判定"这次没拿到语义向量"。
    """
    items = list(texts)
    if not items:
        return [], current_provider().model
    provider = current_provider()
    try:
        vectors = await provider.embed(items)
        if len(vectors) != len(items):
            raise EmbeddingUnavailable(
                f"嵌入条数不合约: 期望 {len(items)} 实际 {len(vectors)}"
            )
    except EmbeddingUnavailable as exc:
        if not fallback_local:
            raise
        logger.warning(
            "embedding provider unavailable, fallback to local hash",
            provider=provider.model,
            error=str(exc),
        )
        return [local_embedding(text) for text in items], EMBEDDING_MODEL_LOCAL
    return vectors, provider.model


async def embed_one(
    text: str, *, fallback_local: bool = True
) -> tuple[list[float], str]:
    """单条嵌入:返回 (向量, 模型名);记忆写入/查询都走这里。"""
    vectors, model = await embed_many([text], fallback_local=fallback_local)
    return vectors[0], model
