"""服务配置基类:收拢各服务逐字重复的数据库开关与鉴权字段(R-22)。

七个服务原本各自抄一份 database_url/auto_create_tables/auth_* 默认值;
这里集中定义并加启动期校验,服务只保留自己的 env_prefix、model_config
与差异化字段。
"""

from __future__ import annotations

from typing import Self

from pydantic import model_validator

from common.config import BaseAppSettings


class ServiceSettings(BaseAppSettings):
    """服务共享字段;子类必须声明自己的 model_config(env_prefix)。"""

    # 数据库:不提供默认值,强制每个服务显式声明自己的库,避免误连。
    database_url: str
    auto_create_tables: bool = False  # dev/test 直建表;生产走 alembic
    # 鉴权(架构 §10):默认开启,与全平台同一套 token→租户约定(自建 IdP)
    auth_enabled: bool = True
    auth_issuer: str = "http://127.0.0.1:8001"  # 自建 IdP:account-service
    auth_audience: str | None = "beauty-api"  # 与 account 的 TOKEN_AUDIENCE 一致
    auth_jwks_url: str | None = None  # 不填走 OIDC 发现文档
    auth_tenant_claim: str = "tenant_id"

    @model_validator(mode="after")
    def _validate_service_settings(self) -> Self:
        """启动期快速失败:库地址必填;开鉴权时 issuer/tenant claim 不能为空。"""
        if not self.database_url.strip():
            raise ValueError("database_url 不能为空")
        if self.auth_enabled:
            if not self.auth_issuer.strip():
                raise ValueError("auth_enabled=true 时 auth_issuer 不能为空")
            if not self.auth_tenant_claim.strip():
                raise ValueError("auth_enabled=true 时 auth_tenant_claim 不能为空")
        return self
