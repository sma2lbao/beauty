"""OIDC 端点:发现文档 + JWKS,供各服务的 JwtVerifier 自动发现。

挂在应用根路径(不在 /v1 下):JwtVerifier 按约定访问
`{issuer}/.well-known/openid-configuration` 与 `{issuer}/jwks`。
"""

from __future__ import annotations

from fastapi import APIRouter

from account.api.v1.auth import SignerDep

router = APIRouter(tags=["oidc"])


@router.get("/.well-known/openid-configuration")
async def discovery(signer: SignerDep) -> dict:
    return signer.discovery()


@router.get("/jwks")
async def jwks(signer: SignerDep) -> dict:
    return signer.jwks()
