"""outbox 替身(RecordingBus):记录投递事件,可注入失败以验证重试。"""

from __future__ import annotations

from typing import Any


class RecordingBus:
    """记录投递事件的进程内总线替身(可注入失败以验证 outbox 重试)。"""

    def __init__(self, fail_times: int = 0) -> None:
        self.received: list[Any] = []
        self.fail_times = fail_times
        self.calls = 0

    async def publish(self, envelope: Any) -> int:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("消费者不可用")
        self.received.append(envelope)
        return 1

    def subscribe(self, pattern: str, handler: Any) -> None:  # pragma: no cover
        raise NotImplementedError

    @property
    def types(self) -> list[str]:
        return [envelope.type for envelope in self.received]
