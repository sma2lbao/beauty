"""用量与 BYOK 契约(M0-11 本地版)。"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, Field, field_validator

#: usage_events.cost_usd 存 Numeric(12,6):平台金额口径是微美元(6 位小数)。
_USD_PLACES = 6
_USD_QUANTUM = Decimal("0.000001")


class UsageByModel(BaseModel):
    model: str
    tokens: int
    events: int
    #: 该模型本月估算成本(USD);未计价调用按 0 计入,另行计数
    cost_usd: float = 0.0
    unpriced_events: int = 0


class UsageSummary(BaseModel):
    today_tokens: int
    month_tokens: int
    today_events: int
    month_events: int
    today_cost_usd: float = 0.0
    month_cost_usd: float = 0.0
    #: 本月未计价调用条数(价目表缺项/BYOK 自定义模型),用于前端提示口径
    unpriced_events: int = 0
    by_model: list[UsageByModel]


class UsageEventCreate(BaseModel):
    """内部上报事件(orchestrator/seed 用;X-Internal-Secret 保护)。"""

    #: 投递幂等键(<outbox 信封 id>:<样本序号>);缺省 = 不做去重(直连/旧调用)
    event_id: str | None = Field(default=None, max_length=64)
    run_id: str | None = Field(default=None, max_length=32)
    model: str = Field(default="unknown", max_length=128)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    #: 网关按价目表估算的美元成本;None = 未计价,不要补 0
    cost_usd: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=_USD_PLACES
    )
    billing_source: Literal["platform", "byok"] = "platform"

    @field_validator("cost_usd", mode="before")
    @classmethod
    def _quantize_cost(cls, value: object) -> object:
        """超精度金额按存储口径收敛,而不是整条投递判 422。

        网关计价是 tokens × 单价,天然会出现 0.000240804 这种 9 位尾数,
        而列是 Numeric(12,6)——数据库本来也会四舍五入到 6 位。在这里先取整
        只改表示精度、不改金额口径;若直接拒绝,outbox 会把整条事件重试到
        failed,已经花掉的 token 就永久丢账(P0-3 的意义正是「一定记上」)。
        """
        if value is None or isinstance(value, bool):
            return value
        try:
            decimal = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return value  # 交给 pydantic 报「不是合法小数」
        if not decimal.is_finite() or decimal.as_tuple().exponent >= -_USD_PLACES:
            return decimal
        try:
            return decimal.quantize(_USD_QUANTUM, rounding=ROUND_HALF_UP)
        except InvalidOperation:  # 超出 Decimal 上下文精度:让后续约束去拒绝
            return decimal


class UsageQuota(BaseModel):
    """账号配额快照(内部读取用):未配置的维度为 None,表示不限制。"""

    monthly_tokens: int | None = None
    max_concurrent_runs: int | None = None
    used_tokens: int = 0


class BYOKUpsert(BaseModel):
    """BYOK 写入:api_key 省略/null/空串 = 沿用原密钥(仅更新模型清单)。"""

    api_key: str | None = Field(default=None, min_length=8, max_length=4096)
    models: list[str] = Field(default_factory=list)

    @field_validator("api_key", mode="before")
    @classmethod
    def _blank_key_means_keep(cls, value: object) -> object:
        """空串/纯空白等价于「不换 key」:契约允许省略、null 与空串三种写法。"""
        if isinstance(value, str) and not value.strip():
            return None
        return value


class BYOKRead(BaseModel):
    model_config = {"from_attributes": True}

    provider: str
    masked_key: str
    key_hint: str
    updated_at: datetime
    models: list[str]


class BYOKCatalogRead(BaseModel):
    """内部目录:该账号全部 BYOK 自定义模型并集(去重保序)。"""

    models: list[str]


class BYOKSecretRead(BaseModel):
    """内部解析结果(**明文**):仅服务间调用,响应必须 no-store。"""

    provider: str
    api_key: str
    key_hint: str
    key_id: str


class BYOKRotateResult(BaseModel):
    """批量轮换结果:逐条容错,失败计数不回滚已成功的条目。"""

    rotated: int
    failed: int
