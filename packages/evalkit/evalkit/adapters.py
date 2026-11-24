"""adapter 注册与动态加载:平台不反向依赖任何被测服务。

配置里的 adapter 有两种写法:

- module:ClassName:按导入路径加载(生产 adapter 用这种,避免 evalkit
  反过来依赖 agent-orchestrator);
- 注册名:测试或内嵌场景用 register_adapter 注册工厂。

工厂/类可以接受一个 options 映射;没有参数时直接无参实例化。
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable, Mapping
from typing import Any, Protocol, runtime_checkable

from .errors import AdapterError
from .graders import Observation
from .schema import Case


@runtime_checkable
class Adapter(Protocol):
    """被测目标适配器协议。"""

    name: str

    def run(self, case: Case) -> Observation: ...

    def close(self) -> None: ...


AdapterFactory = Callable[..., Adapter]

_REGISTRY: dict[str, AdapterFactory] = {}


def register_adapter(name: str, factory: AdapterFactory) -> None:
    """按短名注册 adapter;重复注册覆盖旧值(测试里可反复替换)。"""
    if not name or not callable(factory):
        raise AdapterError("register_adapter 需要非空 name 与可调用 factory")
    _REGISTRY[name] = factory


def unregister_adapter(name: str) -> None:
    _REGISTRY.pop(name, None)


def load_adapter(target: str, options: Mapping[str, Any] | None = None) -> Adapter:
    """按 module:Class 或注册名加载并实例化 adapter。"""
    if not isinstance(target, str) or not target.strip():
        raise AdapterError("adapter 目标不能为空")
    target = target.strip()
    factory = _REGISTRY.get(target) or _import_factory(target)
    adapter = _instantiate(factory, options or {})
    if not isinstance(adapter, Adapter):
        raise AdapterError(
            f"adapter {target!r} 不符合协议:需要 name 属性与 run(case) 方法"
        )
    if not getattr(adapter, "name", ""):
        raise AdapterError(f"adapter {target!r} 缺少非空 name")
    return adapter


def _import_factory(target: str) -> AdapterFactory:
    if ":" not in target:
        raise AdapterError(
            f"adapter {target!r} 未注册;请写成 module:Class 或先 register_adapter"
        )
    module_name, _, attribute = target.partition(":")
    if not module_name or not attribute:
        raise AdapterError(f"adapter 目标格式应为 module:Class,实际 {target!r}")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise AdapterError(f"导入 adapter 模块失败:{module_name}:{exc}") from exc
    try:
        factory = getattr(module, attribute)
    except AttributeError as exc:
        raise AdapterError(f"adapter 模块 {module_name} 没有属性 {attribute}") from exc
    if not callable(factory):
        raise AdapterError(f"adapter 目标不可调用:{target}")
    return factory


def _instantiate(factory: AdapterFactory, options: Mapping[str, Any]) -> Adapter:
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        return factory()
    parameters = signature.parameters
    if "options" in parameters:
        return factory(options=options)
    positional = [
        parameter
        for parameter in parameters.values()
        if parameter.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
    ]
    if positional:
        return factory(options)
    return factory()
