"""dev IdP 桩:阶段 A 的本机登录替身(架构 §10/§11)。

架构终态的 IdP 是 Logto 自托管(§11);在它接入前,这个桩让
"登录 → 签发 access token → 各服务验签"的完整生产路径先跑起来:

- RS256 签发(进程启动时生成 RSA 密钥,JWT `kid` 头指向它),claims
  携带 `tenant_id`(JwtVerifier 的租户约定),`iss`/`aud`/`exp` 与
  真 IdP 的 access token 完全同构;
- 暴露 OIDC 发现文档 + JWKS 端点——兄弟服务用同一个 `JwtVerifier`
  经 HTTP 发现来验签,**没有任何桩专用后门**;将来换 Logto 只改
  `AUTH_ISSUER`,服务代码零改动;
- 无密码、无注册(声明 email 即登录):它验证的是**认证管道**,不是
  用户凭证;凭证本身归 IdP。`DEV_AUTH_ENABLED=false`(默认)时
  相关端点根本不挂载。
"""

from __future__ import annotations

import time
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm


class DevIdP:
    """进程内 RSA 密钥 + access token 签发(JWKS 可被 JwtVerifier 拉取)。"""

    def __init__(self, *, issuer: str, audience: str, ttl_seconds: int = 3600) -> None:
        self._private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
        self._kid = f"dev-{uuid4().hex[:12]}"
        self._issuer = issuer.rstrip("/")
        self._audience = audience
        self._ttl = ttl_seconds

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    def sign(self, *, subject: str, tenant_id: str) -> str:
        """签发 access token:claims 与 IdP 契约一致(§10 tenant claim)。"""
        now = int(time.time())
        claims = {
            "iss": self._issuer,
            "aud": self._audience,
            "sub": subject,
            "tenant_id": tenant_id,
            "iat": now,
            "exp": now + self._ttl,
        }
        return jwt.encode(
            claims,
            self._private_key,
            algorithm="RS256",
            headers={"kid": self._kid},
        )

    def jwks(self) -> dict:
        """JWKS(Json Web Key Set):JwtVerifier 拉这个验签。"""
        jwk = RSAAlgorithm.to_jwk(self._private_key.public_key(), as_dict=True)
        jwk.update({"kid": self._kid, "use": "sig", "alg": "RS256"})
        return {"keys": [jwk]}

    def discovery(self) -> dict:
        """OIDC 发现文档:JwtVerifier 不配 JWKS URL 时经它自动发现。"""
        return {
            "issuer": self._issuer,
            "jwks_uri": f"{self._issuer}/jwks",
            "id_token_signing_alg_values_supported": ["RS256"],
            "token_endpoint_auth_methods_supported": ["none"],
        }
