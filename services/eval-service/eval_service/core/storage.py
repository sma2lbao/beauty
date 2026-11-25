"""报告对象存储装配:把 Settings 映射成 common.artifacts 的构造参数。

只在这里读环境配置;common 侧不依赖 pydantic-settings,便于其他服务复用。
COS SDK 按需延迟导入:filesystem/database 后端不会加载腾讯云依赖。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from common.artifacts import COS_BACKEND, ArtifactStore, build_artifact_store
from pydantic import SecretStr

from eval_service.core.config import Settings

if TYPE_CHECKING:
    from common.cos_artifacts import CosArtifactConfig


def build_store(settings: Settings) -> ArtifactStore | None:
    """按 settings.artifact_store 构造存储;未知后端/COS 缺配置启动即失败。"""

    cos = build_cos_config(settings) if settings.artifact_store == COS_BACKEND else None
    return build_artifact_store(
        settings.artifact_store,
        root=settings.artifact_root,
        cos=cos,
    )


def build_cos_config(settings: Settings) -> CosArtifactConfig:
    """把 Settings 映射成 COS 配置;延迟导入，filesystem/database 后端不加载 SDK。"""

    from common.cos_artifacts import CosArtifactConfig

    return CosArtifactConfig(
        bucket=settings.artifact_cos_bucket,
        region=settings.artifact_cos_region,
        secret_id=_secret_value(settings.artifact_cos_secret_id),
        secret_key=_secret_value(settings.artifact_cos_secret_key),
        token=_secret_value(settings.artifact_cos_token),
        scheme=settings.artifact_cos_scheme,
        endpoint=settings.artifact_cos_endpoint,
        timeout_seconds=settings.artifact_cos_timeout_seconds,
        retry_times=settings.artifact_cos_retry_times,
        pool_max_size=settings.artifact_cos_pool_max_size,
    )


def build_cos_client(settings: Settings) -> Any:
    """构造桶级操作用的 COS 客户端;调用方只在 artifact_store=cos 时使用。"""

    from common.cos_artifacts import build_cos_client as _build

    return _build(build_cos_config(settings))


def _secret_value(value: SecretStr | None) -> str:
    return value.get_secret_value() if value is not None else ""
