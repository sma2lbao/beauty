"""adapter 动态加载与协议校验。"""

from __future__ import annotations

from pathlib import Path

import pytest
from adapter_fixture import (
    BadAdapter,
    EmptyNameAdapter,
    NoOptionsAdapter,
    PositionalAdapter,
    RecordingAdapter,
    make_adapter,
)

from evalkit.adapters import (
    load_adapter,
    register_adapter,
    unregister_adapter,
)
from evalkit.errors import AdapterError


@pytest.fixture(autouse=True)
def _clean_registry():
    yield
    for name in ("mem", "pos", "bare", "none", "bad", "empty"):
        unregister_adapter(name)


def test_register_and_load_with_options() -> None:
    register_adapter("mem", make_adapter)
    adapter = load_adapter("mem", {"x": 1})
    assert isinstance(adapter, RecordingAdapter)
    assert adapter.options == {"x": 1}


def test_positional_factory_without_options_name() -> None:
    def bare(opts):
        return RecordingAdapter(opts)

    register_adapter("bare", bare)
    adapter = load_adapter("bare", {"x": 1})
    assert isinstance(adapter, RecordingAdapter)
    assert adapter.options == {"x": 1}


def test_class_with_positional_options() -> None:
    register_adapter("pos", PositionalAdapter)
    adapter = load_adapter("pos", {"x": 1})
    assert isinstance(adapter, PositionalAdapter)
    assert adapter.options == {"x": 1}


def test_class_without_constructor_args() -> None:
    register_adapter("none", NoOptionsAdapter)
    adapter = load_adapter("none")
    assert isinstance(adapter, NoOptionsAdapter)
    assert adapter.created is True


def test_unregister_and_unknown_name() -> None:
    register_adapter("mem", make_adapter)
    unregister_adapter("mem")
    with pytest.raises(AdapterError, match="未注册"):
        load_adapter("mem")


def test_register_validation() -> None:
    with pytest.raises(AdapterError, match="非空 name"):
        register_adapter("", make_adapter)
    with pytest.raises(AdapterError, match="可调用"):
        register_adapter("x", 1)  # type: ignore[arg-type]


def test_load_dynamic_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).parent))
    adapter = load_adapter("adapter_fixture:RecordingAdapter", {"from": "module"})
    assert isinstance(adapter, RecordingAdapter)
    assert adapter.options == {"from": "module"}


def test_load_errors() -> None:
    with pytest.raises(AdapterError, match="不能为空"):
        load_adapter("")
    with pytest.raises(AdapterError, match="未注册"):
        load_adapter("no-such-adapter")
    with pytest.raises(AdapterError, match="格式应为"):
        load_adapter(":Class")
    with pytest.raises(AdapterError, match="导入 adapter 模块失败"):
        load_adapter("no_such_module:Class")
    with pytest.raises(AdapterError, match="没有属性"):
        load_adapter("evalkit.adapters:NoSuchThing")
    with pytest.raises(AdapterError, match="不可调用"):
        load_adapter("evalkit.schema:GRADER_TYPES")


def test_protocol_and_name_validation() -> None:
    register_adapter("bad", BadAdapter)
    with pytest.raises(AdapterError, match="不符合协议"):
        load_adapter("bad")
    register_adapter("empty", EmptyNameAdapter)
    with pytest.raises(AdapterError, match="缺少非空 name"):
        load_adapter("empty")


def test_adapter_observation_round_trip() -> None:
    from evalkit.schema import Case

    register_adapter("mem", make_adapter)
    adapter = load_adapter("mem")
    case = Case(id="a/b", input={"output": "hi"}, graders=())
    observation = adapter.run(case)
    assert observation.output == "hi"
    adapter.close()
    assert adapter.closed is True
