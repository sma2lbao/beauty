"""自建 IdP 签发器(由 dev 桩转正,架构 §10/§11):

- RS256 签发(进程启动时生成 RSA 密钥,JWT `kid` 头指向它),claims
  携带 `tenant_id`(JwtVerifier 的租户约定),`iss`/`aud`/`exp` 与
  托管 IdP 的 access token 完全同构;
- 暴露 OIDC 发现文档 + JWKS 端点——兄弟服务用同一个 `JwtVerifier`
  经 HTTP 发现来验签,**没有任何专用后门**;将来要换 Logto 等托管 IdP
  只改各服务 `AUTH_ISSUER`,业务代码零改动;
- 凭证校验(密码哈希)在 services/auth_service.py,这里只管"签"。

密钥持久化:`signing_key_path` 设置后从 PEM 加载(不存在则生成并写
0600 权限),跨重启不轮换 kid;留空 = 每次进程内新生成(dev/test 用)。
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

_PRIVATE_KEY_BYTES = 2048


class IdPSigner:
    """RSA 密钥 + access token 签发(JWKS 可被 JwtVerifier 拉取)。"""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        ttl_seconds: int = 3600,
        private_key_pem: str | None = None,
    ) -> None:
        if private_key_pem is not None:
            self._private_key = serialization.load_pem_private_key(
                private_key_pem.encode(), password=None
            )
        else:
            self._private_key = rsa.generate_private_key(
                public_exponent=65537, key_size=_PRIVATE_KEY_BYTES
            )
        # kid 恒从公钥指纹派生:持久化密钥跨重启 kid 稳定(JWKS 不轮换),
        # 进程内随机密钥则天然每次不同
        public_der = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self._kid = f"acct-{hashlib.sha256(public_der).hexdigest()[:12]}"
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


def load_or_create_signer(
    path: str, *, issuer: str, audience: str, ttl_seconds: int
) -> IdPSigner:
    """按路径加载持久签名密钥;文件不存在则生成并落盘(0600)。

    生产(多实例/频繁重启)必须持久,否则每次重启所有未过期 access
    token 全部失效;dev/test 不传路径即纯内存密钥。
    """
    pem_path = Path(path)
    if pem_path.exists():
        return IdPSigner(
            issuer=issuer,
            audience=audience,
            ttl_seconds=ttl_seconds,
            private_key_pem=pem_path.read_text(encoding="utf-8"),
        )
    signer = IdPSigner(issuer=issuer, audience=audience, ttl_seconds=ttl_seconds)
    pem = signer._private_key.private_bytes(  # noqa: SLF001 落盘自家私钥
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pem_path.parent.mkdir(parents=True, exist_ok=True)
    pem_path.write_bytes(pem)
    os.chmod(pem_path, 0o600)
    return signer
