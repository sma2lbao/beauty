"""用量上报:每次模型调用的 token 用量落 account 的 usage_events。

分层(与 graph/delta 同款):`GatewayClient` 只把网关返回的 usage 记进
"当前采集器"(contextvar),节点与图调用方零改动;真正的上报在
services 层图调用边界(runs.py)统一做——节点保持纯函数,出口都在边界。

失败语义:用量是观测数据,不是执行路径——上报失败只打 warning + 计数,
**绝不阻塞 run**(宁可少一条用量,也不能让用户的 run 因为这个挂掉)。
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Any

import httpx
from common.logging import get_logger
from common.metrics import observe_job_failure
from common.middleware import inject_request_headers
from common.security import SECRET_HEADER


@dataclass(frozen=True)
class UsageSample:
    """一次模型调用的用量(网关归一化后的三件套)。"""

    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


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


class UsageReporter:
    """经 account 内部端点写 usage_events(X-Internal-Secret 保护)。"""

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

    async def report(
        self,
        *,
        tenant_id: str,
        run_id: str | None,
        samples: list[UsageSample],
    ) -> None:
        """逐条上报;任何一条失败就停(避免失败时刷屏),只留 warning。"""
        log = get_logger("agent_orchestrator.usage")
        for sample in samples:
            body = {
                "run_id": run_id,
                "model": sample.model,
                "prompt_tokens": sample.prompt_tokens,
                "completion_tokens": sample.completion_tokens,
            }
            try:
                resp = await self._client.post(
                    "/v1/usage/events",
                    json=body,
                    headers={SECRET_HEADER: self._secret},
                )
            except Exception as exc:  # noqa: BLE001 - 上报是观测数据,任何异常都不能连带打死 run
                # 不只是 httpx.HTTPError:异步传输层异常(事件循环关闭、
                # 任务组包装的 ExceptionGroup)同样会在这里冒出,一旦上抛就会
                # 把 run 判成 failed(契约见 run_quota._usage_boundary)。
                self._failed(log, tenant_id, run_id, sample, str(exc))
                return
            if resp.status_code >= 400:
                self._failed(
                    log,
                    tenant_id,
                    run_id,
                    sample,
                    f"http {resp.status_code}: {resp.text[:200]}",
                )
                return

    @staticmethod
    def _failed(
        log: Any,
        tenant_id: str,
        run_id: str | None,
        sample: UsageSample,
        detail: str,
    ) -> None:
        observe_job_failure(kind="usage_report")
        log.warning(
            "usage_report_failed",
            tenant_id=tenant_id,
            run_id=run_id,
            model=sample.model,
            detail=detail,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


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
    *, tenant_id: str, run_id: str | None, samples: list[UsageSample]
) -> None:
    """在图调用边界调用:没有上报器或没有样本时直接返回。"""
    if _reporter is None or not samples:
        return
    await _reporter.report(tenant_id=tenant_id, run_id=run_id, samples=samples)
