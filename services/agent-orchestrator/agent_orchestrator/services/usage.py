"""用量上报:每次模型调用的 token 用量落 account 的 usage_events。

分层(与 graph/delta 同款):`GatewayClient` 只把网关返回的 usage 记进
"当前采集器"(contextvar),节点与图调用方零改动;真正的上报在
services 层图调用边界(runs.py)统一做——节点保持纯函数,出口都在边界。

失败语义:用量是观测数据,不是执行路径——上报失败只打 warning + 计数,
**绝不阻塞 run**(宁可少一条用量,也不能让用户的 run 因为这个挂掉)。

可靠性(P0-3):装配了 session_factory 时,用量先写 **orchestrator 自己的
outbox**(`usage.report.v1`,独立短事务,不与 run 业务事务耦合——token 已经
花掉了,业务回滚也得记账),再由 dispatcher 至少一次投递;UsageDeliveryConsumer
逐条 POST account,用 `event_id=<信封 id>:<序号>` 做幂等,重投不会重复计费。
没装配 session_factory 时保持直连 POST(单机/测试路径不变)。
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Any

import httpx
from common.logging import get_logger
from common.middleware import inject_request_headers
from common.security import ACCOUNT_HEADER, SECRET_HEADER
from database.session import session_scope
from events.envelope import EventEnvelope
from events.outbox import OutboxRepository, is_processed, mark_processed
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.metrics import observe_job_failure

#: usage 上报事件(消费方按 event_id 幂等;payload 见 usage_report_payload)
USAGE_REPORT_EVENT = "usage.report.v1"
#: usage 投递消费者名(processed_events 的去重维度)
USAGE_DELIVERY_CONSUMER = "usage-delivery"


@dataclass(frozen=True)
class UsageSample:
    """一次模型调用的用量(网关归一化后的三件套)。"""

    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    #: 网关按价目表估算的美元成本;None = 该模型未计价(BYOK 自定义模型常见),
    #: 下游必须区分「0 元」和「不知道多少钱」,不能把 None 当 0。
    cost_usd: float | None = None
    #: 平台 key 还是用户自带 key(platform/byok),成本归因用。
    billing_source: str = "platform"


@dataclass
class UsageCollector:
    """一次 run(或一次图调用)内的用量累积器。"""

    samples: list[UsageSample] = field(default_factory=list)

    def record(self, sample: UsageSample) -> None:
        self.samples.append(sample)

    @property
    def total_tokens(self) -> int:
        return sum(sample.total_tokens for sample in self.samples)

    def __len__(self) -> int:
        return len(self.samples)


_collector: contextvars.ContextVar[UsageCollector | None] = contextvars.ContextVar(
    "usage_collector", default=None
)


def bind(collector: UsageCollector) -> contextvars.Token[UsageCollector | None]:
    """把采集器绑到当前上下文(图调用边界用)。"""
    return _collector.set(collector)


def unbind(token: contextvars.Token[UsageCollector | None]) -> None:
    _collector.reset(token)


def current() -> UsageCollector | None:
    return _collector.get()


def record(sample: UsageSample | None) -> None:
    """记一次模型调用;没有采集器时 no-op(单测/无 run 的路径不强求)。"""
    if sample is None:
        return
    collector = _collector.get()
    if collector is not None:
        collector.record(sample)


def _sample_payload(sample: UsageSample) -> dict[str, Any]:
    """一条样本在 HTTP body / outbox payload 里的统一形状。"""
    return {
        "model": sample.model,
        "prompt_tokens": sample.prompt_tokens,
        "completion_tokens": sample.completion_tokens,
        "cost_usd": sample.cost_usd,
        "billing_source": sample.billing_source,
    }


def usage_report_payload(
    run_id: str | None, samples: list[UsageSample]
) -> dict[str, Any]:
    """outbox 事件 payload:逐条保留样本(不聚合,成本/来源不能被抹平)。"""
    return {"run_id": run_id, "samples": [_sample_payload(s) for s in samples]}


class UsageDeliveryError(RuntimeError):
    """用量投递失败:dispatcher 据此退避重试,不能吞掉异常把它标成成功。"""


class UsageReporter:
    """经 account 内部端点写 usage_events(X-Internal-Secret 保护)。

    装配 session_factory 后改走 outbox(至少一次投递 + event_id 幂等);
    未装配时保持直连 POST。
    """

    def __init__(
        self,
        *,
        base_url: str,
        internal_secret: str,
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
            event_hooks={"request": [inject_request_headers]},
        )
        self._secret = internal_secret
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    @property
    def durable(self) -> bool:
        """是否已装配 session 工厂(report 走 outbox 而不是直连)。"""
        return self._session_factory is not None

    def attach_session_factory(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """由 container 在 session 工厂就绪后装配;此后 report 改走 outbox。"""
        self._session_factory = factory

    async def report(
        self,
        *,
        account_id: str,
        run_id: str | None,
        samples: list[UsageSample],
    ) -> None:
        """先写 outbox(已装配 session 工厂),否则直连;都不阻塞 run。"""
        if not samples:
            return
        factory = self._session_factory
        if factory is not None:
            try:
                await self._enqueue(
                    factory,
                    account_id=account_id,
                    run_id=run_id,
                    samples=samples,
                )
                return
            except Exception as exc:  # noqa: BLE001 - 入队失败回退直连,同样不能打死 run
                observe_job_failure(kind="usage_report")
                get_logger("agent_orchestrator.usage").warning(
                    "usage_enqueue_failed",
                    account_id=account_id,
                    run_id=run_id,
                    detail=f"{type(exc).__name__}: {exc}",
                )
        await self._post_each(account_id=account_id, run_id=run_id, samples=samples)

    async def _enqueue(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        account_id: str,
        run_id: str | None,
        samples: list[UsageSample],
    ) -> None:
        """独立短事务写发件箱:token 已花掉,不跟 run 业务事务一起回滚。"""
        envelope = EventEnvelope.create(
            USAGE_REPORT_EVENT,
            usage_report_payload(run_id, samples),
            account_id=account_id,
        )
        async with session_scope(factory) as session:
            await OutboxRepository(session).record(account_id, envelope)

    async def _post_each(
        self,
        *,
        account_id: str,
        run_id: str | None,
        samples: list[UsageSample],
    ) -> None:
        """直连兜底:逐条上报;任何一条失败就停(避免失败时刷屏),只留 warning。"""
        log = get_logger("agent_orchestrator.usage")
        for sample in samples:
            body = {"run_id": run_id, **_sample_payload(sample)}
            try:
                resp = await self._client.post(
                    "/v1/usage/events",
                    json=body,
                    headers=self._headers(account_id),
                )
            except Exception as exc:  # noqa: BLE001 - 上报是观测数据,任何异常都不能连带打死 run
                # 不只是 httpx.HTTPError:异步传输层异常(事件循环关闭、
                # 任务组包装的 ExceptionGroup)同样会在这里冒出,一旦上抛就会
                # 把 run 判成 failed(契约见 run_quota._usage_boundary)。
                self._failed(log, account_id, run_id, sample, str(exc))
                return
            if resp.status_code >= 400:
                self._failed(
                    log,
                    account_id,
                    run_id,
                    sample,
                    f"http {resp.status_code}: {resp.text[:200]}",
                )
                return

    async def deliver(self, *, account_id: str, body: dict[str, Any]) -> None:
        """严格投一条(dispatcher 消费者用):失败抛异常,交给 outbox 退避重试。"""
        try:
            resp = await self._client.post(
                "/v1/usage/events",
                json=body,
                headers=self._headers(account_id),
            )
        except Exception as exc:  # noqa: BLE001 - 转成投递错误,让 outbox 记失败重试
            observe_job_failure(kind="usage_delivery")
            raise UsageDeliveryError(
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if resp.status_code >= 400:
            observe_job_failure(kind="usage_delivery")
            raise UsageDeliveryError(
                f"http {resp.status_code}: {resp.text[:200]}"
            )

    def _headers(self, account_id: str) -> dict[str, str]:
        return {
            SECRET_HEADER: self._secret,
            # account 的内部账号解析 = 共享密钥 + 显式 X-Account-Id;
            # 缺这个头会 401 auth.account_missing,用量永远落库失败。
            ACCOUNT_HEADER: account_id,
        }

    @staticmethod
    def _failed(
        log: Any,
        account_id: str,
        run_id: str | None,
        sample: UsageSample,
        detail: str,
    ) -> None:
        observe_job_failure(kind="usage_report")
        log.warning(
            "usage_report_failed",
            account_id=account_id,
            run_id=run_id,
            model=sample.model,
            detail=detail,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class UsageDeliveryConsumer:
    """把 usage.report.v1 逐条投给 account(至少一次 + event_id 幂等)。

    dispatcher 保证失败会重投,所以这里**必须**严格:任何一条没落库就抛异常;
    重复投递由 account 侧的 event_id 唯一约束兜住(见 account insights)。
    """

    name = USAGE_DELIVERY_CONSUMER

    def __init__(
        self,
        reporter: UsageReporter,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._reporter = reporter
        self._factory = session_factory

    async def __call__(self, envelope: EventEnvelope) -> None:
        if envelope.type != USAGE_REPORT_EVENT:
            return
        account_id = envelope.account_id
        samples = envelope.payload.get("samples")
        if not account_id or not isinstance(samples, list) or not samples:
            # 坏事件无法自愈:直接抛给 dispatcher 记 failed 留痕,不无限重试。
            raise ValueError(f"{USAGE_REPORT_EVENT} 缺少 account_id 或 samples")
        async with session_scope(self._factory) as session:
            if await is_processed(
                session,
                account_id=account_id,
                consumer=self.name,
                event_id=envelope.id,
            ):
                return
        run_id = envelope.payload.get("run_id")
        for index, sample in enumerate(samples):
            if not isinstance(sample, dict):
                raise ValueError(f"{USAGE_REPORT_EVENT} 的 sample 必须是对象")
            await self._reporter.deliver(
                account_id=account_id,
                body={
                    **sample,
                    "run_id": run_id,
                    # 逐条子事件 id:一条 outbox 事件可能带多个样本,
                    # account 的唯一约束按样本去重,重投不会重复计费。
                    "event_id": f"{envelope.id}:{index}",
                },
            )
        async with session_scope(self._factory) as session:
            await mark_processed(
                session,
                account_id=account_id,
                consumer=self.name,
                event_id=envelope.id,
            )


class TokenBudgetExceeded(RuntimeError):
    """run 级 token 预算用尽:节点边界中止本次执行(终态 failed,重试=新起 run)。"""


# 进程级预算(0 = 不限制);由 create_app 从 settings.RUN_TOKEN_BUDGET 固定
_budget_tokens: int = 0


def configure_budget(tokens: int) -> None:
    """设置 run 级 token 预算;0/负数表示不限制。"""
    global _budget_tokens
    _budget_tokens = max(0, int(tokens or 0))


def budget_tokens() -> int:
    return _budget_tokens


def check_budget() -> None:
    """在每次模型调用前检查本次 run 已用 token(无采集器/未配置预算时跳过)。

    预算按「本次图调用」的采集器算:plan 与 approve 各自独立计数——
    这与 usage_events 的 run 维度上报一致(同 run 多段执行各记各的);
    跨段累计如需更强约束,可后续把已落库用量带回 state。
    """
    budget = _budget_tokens
    if budget <= 0:
        return
    collector = current()
    if collector is None:
        return
    if collector.total_tokens >= budget:
        raise TokenBudgetExceeded(
            f"本次执行模型用量已达预算上限:{collector.total_tokens}/{budget} token"
        )


_reporter: UsageReporter | None = None


def configure_reporter(
    *,
    base_url: str,
    internal_secret: str,
    timeout_seconds: float = 5.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> UsageReporter | None:
    """进程级上报器(base_url 或密钥缺失时关闭:不允许静默裸调)。"""
    global _reporter
    if not base_url or not internal_secret:
        _reporter = None
        return None
    _reporter = UsageReporter(
        base_url=base_url,
        internal_secret=internal_secret,
        timeout_seconds=timeout_seconds,
        transport=transport,
    )
    return _reporter


def reporter() -> UsageReporter | None:
    return _reporter


async def close_reporter() -> None:
    """关闭并清空进程级上报器(应用 shutdown 用)。"""
    global _reporter
    if _reporter is not None:
        reporter = _reporter
        _reporter = None
        await reporter.aclose()


async def report(
    *, account_id: str, run_id: str | None, samples: list[UsageSample]
) -> None:
    """在图调用边界调用:没有上报器或没有样本时直接返回。"""
    if _reporter is None or not samples:
        return
    await _reporter.report(account_id=account_id, run_id=run_id, samples=samples)
