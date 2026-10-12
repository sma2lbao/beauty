"""事件信封:所有跨服务事件的统一外壳。

约定:

- 事件类型命名 ``<域>.<实体>.<动作>.v<版本>``,如 ``email.received.v1``;
- 版本只增不改:破坏性变更必须发布 v2,消费方按版本各自迁移;
- 信封字段是路由与兼容层契约,payload 只是数据,互不做兼容承诺。

事件总线(Redis Streams 适配)与 outbox 助手随后续阶段补充。
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)*\.v[0-9]+$")


class EventTypeError(ValueError):
    """事件类型不符合命名约定。"""


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """跨服务事件信封。

    id:全局唯一事件 ID(消费幂等的去重键);
    type:形如 ``email.received.v1``,含版本;
    occurred_at:ISO 8601 时间戳(UTC);
    payload:事件数据,与信封解耦;
    tenant_id:租户标识——跨服务消费者要按租户写自己的库,必须能从事件本身拿到
    (None 表示平台级/无租户事件)。
    """

    id: str
    type: str
    occurred_at: str
    payload: dict[str, Any]
    tenant_id: str | None = None

    def __post_init__(self) -> None:
        if not _TYPE_PATTERN.match(self.type):
            raise EventTypeError(
                f"事件类型必须形如 email.received.v1,得到: {self.type!r}"
            )

    @classmethod
    def create(
        cls,
        type_: str,
        payload: dict[str, Any],
        *,
        tenant_id: str | None = None,
    ) -> EventEnvelope:
        """创建新事件信封。"""
        return cls(
            id=uuid.uuid4().hex,
            type=type_,
            occurred_at=datetime.now(UTC).isoformat(),
            payload=payload,
            tenant_id=tenant_id,
        )

    def to_json(self) -> str:
        """序列化为传输格式(进入 Redis Streams / SSE 的形态)。"""
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> EventEnvelope:
        """从传输格式还原。"""
        return cls(**json.loads(raw))
