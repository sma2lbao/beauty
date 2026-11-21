"""交付物存储装配:把服务 Settings 映射成 common.artifacts 的构造参数。

只在这里读环境配置;common 侧不依赖 pydantic-settings,便于其他服务复用。
COS SDK 按需延迟导入:filesystem/database 后端不会加载腾讯云依赖。
"""

from __future__ import annotations

from common.artifacts import COS_BACKEND, ArtifactStore, build_artifact_store
from pydantic import SecretStr

from workspace.core.config import Settings


def build_store(settings: Settings) -> ArtifactStore | None:
    """按 settings.artifact_store 构造存储;未知后端/COS 缺配置启动即失败。"""
    cos = None
    if settings.artifact_store == COS_BACKEND:
        from common.cos_artifacts import CosArtifactConfig

        cos = CosArtifactConfig(
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
    return build_artifact_store(
        settings.artifact_store,
        root=settings.artifact_root,
        cos=cos,
    )


def _secret_value(value: SecretStr | None) -> str:
    return value.get_secret_value() if value is not None else ""
