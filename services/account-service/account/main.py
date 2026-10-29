"""服务入口:create_app 工厂 + lifespan(装配统一走 common.service_kit)。

启动(项目目录内):uv run uvicorn account.main:app --port 8001
Nx(仓库根):npx nx run account-service:serve
"""

from __future__ import annotations

from common.auth import JwtVerifier
from common.security import SlidingWindowLimiter
from common.service_kit import build_lifespan, create_service_app, install_database
from fastapi import FastAPI

from account.api.health import health_router
from account.api.oauth_metadata import router as oauth_metadata_router
from account.api.v1 import auth as auth_api
from account.api.v1 import oauth as oauth_api
from account.api.v1 import oidc as oidc_api
from account.api.v1.router import api_router
from account.core.byok_kms import BYOKKms, build_byok_kms
from account.core.config import Settings
from account.core.idp import IdPSigner, load_or_create_signer
from account.models import account as account_models  # noqa: F401  注册 ORM 元数据


async def _startup(app: FastAPI) -> None:
    """启动:建 session 工厂(dev/test 可直建表)+ 装配 BYOK KMS。

    BYOK KMS 测试可经 create_app(byok_kms=…) 注入,否则按配置装配。
    """
    await install_database(app)
    if app.state.byok_kms is None:
        app.state.byok_kms = build_byok_kms(app.state.settings)


async def _shutdown_before_verifier(app: FastAPI) -> None:
    """关闭:先回收 KMS,再交给 kit 关 token 校验器(顺序与原 lifespan 一致)。"""
    kms: BYOKKms | None = app.state.byok_kms
    if kms is not None:
        await kms.aclose()


_lifespan = build_lifespan(
    startup=_startup, shutdown_before_verifier=_shutdown_before_verifier
)


def create_app(
    settings: Settings | None = None,
    token_verifier: JwtVerifier | None = None,
    oauth_transport=None,
    byok_kms: BYOKKms | None = None,
) -> FastAPI:
    """测试注入:token_verifier(假 JWKS)、oauth_transport(假 OAuth HTTP)、
    byok_kms(假 KMS);未注入时 lifespan 按 Settings 装配。"""
    settings = settings or Settings()
    app = create_service_app(
        name="account-service",
        settings=settings,
        lifespan=_lifespan,
        routers=[
            (health_router, ""),
            (api_router, "/v1"),
            (auth_api.router, "/v1"),
            (oauth_api.router, "/v1"),
            (oidc_api.router, ""),
            (oauth_metadata_router, ""),
        ],
        state={
            "token_verifier": token_verifier,
            "oauth_transport": oauth_transport,
            "byok_kms": byok_kms,
            "oauth_register_limiter": SlidingWindowLimiter(
                settings.oauth21_register_limit_per_hour, window_seconds=3600.0
            ),
        },
    )
    # 自建认证(由 dev 桩转正):注册/登录/刷新/登出/内部签发 + OIDC 发现/JWKS
    # 始终挂载——本服务就是签发方,兄弟服务的 JwtVerifier 靠这些端点验签
    if settings.signing_key_path:
        app.state.signer = load_or_create_signer(
            settings.signing_key_path,
            issuer=settings.auth_issuer,
            audience=settings.token_audience,
            ttl_seconds=settings.token_ttl_seconds,
        )
    else:
        app.state.signer = IdPSigner(
            issuer=settings.auth_issuer,
            audience=settings.token_audience,
            ttl_seconds=settings.token_ttl_seconds,
        )
    return app


app = create_app()
