"""测试辅助(仅测试使用):本地签名密钥 + JWKS stub + 签发测试 token。

各服务测试装配"开鉴权 + 真签 token"(架构 §10)时共用这一份密钥材料:
签名私钥留在进程内,JWKS 经 MockTransport 喂给 JwtVerifier,零网络——
不再复制 30 行密钥/JWKS 样板,也不再依赖"关鉴权 + X-Tenant-Id 临时头"。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from common.auth import JwtVerifier

ISSUER = "https://idp.test/oidc"
KID = "test-key-1"


def generate_signing_key() -> rsa.RSAPrivateKey:
    """生成一把 RS256 测试签名私钥(调用方持有,同一实例配套使用)。"""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwks_document(private_key: rsa.RSAPrivateKey, kid: str = KID) -> dict:
    """私钥对应的 JWKS 文档(喂给 JwtVerifier 的 MockTransport)。"""
    document = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    document.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return {"keys": [document]}


def sign_token(
    private_key: rsa.RSAPrivateKey,
    tenant_id: str,
    *,
    subject: str = "u1",
    issuer: str = ISSUER,
    audience: str | None = None,
    expires_in: int = 3600,
    kid: str = KID,
) -> str:
    """签发以 tenant_id 为 claim 的测试 access token。"""
    claims: dict = {
        "sub": subject,
        "iss": issuer,
        "exp": datetime.now(UTC) + timedelta(seconds=expires_in),
        "tenant_id": tenant_id,
    }
    if audience is not None:
        claims["aud"] = audience
    return jwt.encode(
        claims, private_key, algorithm="RS256", headers={"kid": kid}
    )


def jwks_transport(document: dict) -> httpx.MockTransport:
    """JWKS stub:GET 以 /jwks 结尾的路径返回文档,其余 404。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jwks"):
            return httpx.Response(200, json=document)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


class TestIdP:
    """测试 IdP:一把密钥 + JWKS stub + 签发函数,conftest 持有单例。

    verifier() 注入服务的 create_app(token_verifier=...),bearer(tenant)
    生成请求头——两者必须来自同一实例(同一把私钥)。
    """

    def __init__(self) -> None:
        self.private_key = generate_signing_key()
        self.jwks = jwks_document(self.private_key)

    def verifier(self) -> JwtVerifier:
        """与本地密钥配套的校验器(注入 create_app)。"""
        return JwtVerifier(
            issuer=ISSUER,
            jwks_url=f"{ISSUER}/jwks",
            transport=jwks_transport(self.jwks),
        )

    def bearer(self, tenant_id: str, **claims) -> dict[str, str]:
        """以 tenant_id 签 token 并包成请求头(多租户隔离测试各签各的)。"""
        token = sign_token(self.private_key, tenant_id, **claims)
        return {"Authorization": f"Bearer {token}"}
