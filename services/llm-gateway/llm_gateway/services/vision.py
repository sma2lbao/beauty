"""模型视觉能力:litellm 静态能力表落后于提供商时的补齐与显式拒绝。

litellm 的 DeepSeek 适配层在 ``supports_vision(model)`` 为 False 时,会走
``_forward_or_collapse_content`` → ``convert_content_list_to_str`` 把
``content`` 里的 ``image_url`` 片段折叠成纯文本:图片既不报错也不进上游,
模型于是凭空作答(用户看到的是"我没收到图片")。而 litellm 的能力表是随
版本发布的静态数据,平台新接入的视觉模型(本平台默认的 DeepSeek V4
``deepseek-chat`` 别名就是)常常没被标注。

所以网关在这里做两件事,缺一不可:

- ``register_vision_models``:把 ``VISION_MODELS`` 声明的模型写进 litellm
  能力表(``litellm.register_model``,跨成本表 reload 保持),服务启动时调一次;
- ``require_image_support``:请求带图但模型仍然不支持时返回 400
  ``llm.model_no_vision``——**绝不静默丢图**(丢图的代价是模型答非所问,
  比明确报错更难排查,见 docs/design/model-routing.md §7)。

只覆盖 ``supports_vision`` 一个字段:价格、上下文长度等沿用 litellm 原值,
不复制价目表(那会随版本漂移)。
"""

from __future__ import annotations

from collections.abc import Iterable

import litellm
from common.errors import AppError
from common.logging import get_logger

#: 带图但模型不支持时的错误码(调用方/前端按 code 提示换模型)
MODEL_NO_VISION = "llm.model_no_vision"

_log = get_logger("llm_gateway.vision")


def _provider_of(model: str) -> str | None:
    """litellm 路由格式的 provider 前缀(没有前缀返回 None,不猜)。"""
    return model.split("/", 1)[0] if "/" in model else None


def supports_images(model: str) -> bool:
    """模型能否接收图片输入(litellm 能力表,含本模块补写的声明)。"""
    return bool(litellm.supports_vision(model))


def register_vision_models(models: Iterable[str]) -> list[str]:
    """把配置声明的视觉模型补进 litellm 能力表,返回本次真正写入的模型。

    已经标注过的模型跳过(不改 litellm 自带的数据);幂等,重复调用只会在
    第一次写入,之后都返回空列表。
    """
    registered: list[str] = []
    for raw in models:
        model = raw.strip()
        if not model or supports_images(model):
            continue
        entry: dict[str, object] = dict(litellm.model_cost.get(model) or {})
        entry["supports_vision"] = True
        provider = _provider_of(model)
        if provider is not None:
            entry.setdefault("litellm_provider", provider)
        litellm.register_model({model: entry})
        registered.append(model)
    if registered:
        _log.info("llm_vision_models", models=registered)
    return registered


def require_image_support(model: str, *, declared: Iterable[str] = ()) -> None:
    """请求带图时校验模型视觉能力;不支持则 400(早于开流)。"""
    if supports_images(model):
        return
    raise AppError(
        MODEL_NO_VISION,
        f"模型 {model} 不支持图片输入:请换用支持图片的模型,或去掉图片后重试",
        details={"model": model, "vision_models": list(declared)},
    )
