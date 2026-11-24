"""测试用 adapter:覆盖三种构造签名与常见失败形态。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evalkit.graders import Observation
from evalkit.schema import Case


class RecordingAdapter:
    """记录调用参数,按用例 input 返回可控观测。"""

    name = "recording"
    instances: list[RecordingAdapter] = []

    def __init__(self, options: Mapping[str, Any] | None = None) -> None:
        self.options = dict(options or {})
        self.calls: list[Case] = []
        self.closed = False
        RecordingAdapter.instances.append(self)

    def run(self, case: Case) -> Observation:
        self.calls.append(case)
        if case.input.get("raise"):
            raise RuntimeError(str(case.input["raise"]))
        if case.input.get("wrong_type"):
            return "not-an-observation"  # type: ignore[return-value]
        return Observation(
            output=str(case.input.get("output", "ok")),
            fields=dict(case.input.get("fields", {})),
            events=tuple(case.input.get("events", ())),
            tool_calls=tuple(case.input.get("tool_calls", ())),
        )

    def close(self) -> None:
        self.closed = True


class PositionalAdapter:
    name = "positional"

    def __init__(self, options: Mapping[str, Any]) -> None:
        self.options = dict(options)

    def run(self, case: Case) -> Observation:
        return Observation()

    def close(self) -> None:
        return None


class NoOptionsAdapter:
    name = "no-options"

    def __init__(self) -> None:
        self.created = True

    def run(self, case: Case) -> Observation:
        return Observation(output="ok")

    def close(self) -> None:
        return None


class EmptyNameAdapter:
    """name 为空,用于名称校验失败。"""

    name = ""

    def run(self, case: Case) -> Observation:
        return Observation()

    def close(self) -> None:
        return None


class BadAdapter:
    """缺少 name,用于协议校验失败。"""

    def run(self, case: Case) -> Observation:
        return Observation()

    def close(self) -> None:
        return None


def make_adapter(options: Mapping[str, Any] | None = None) -> RecordingAdapter:
    return RecordingAdapter(options)
