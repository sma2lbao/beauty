"""JwtVerifier:签名/iss/aud/exp 校验、JWKS 缓存与轮换、租户 claim 提取。"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from common.auth import JwtVerifier
from common.errors import UnauthorizedError, UpstreamError

ISSUER = "https://idp.example.com/oidc"
AUDIENCE = "beauty-api"
KID = "key-1"


def _jwk(private_key: rsa.RSAPrivateKey, kid: str) -> dict[str, Any]:
    document = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    document.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return document


@pytest.fixture(scope="module")
def private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def other_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _token(
    key: rsa.RSAPrivateKey,
    *,
    kid: str = KID,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    tenant: str | None = "t-1",
    expires_in: int = 300,
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": "user-1",
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
        "scope": "runs:write runs:read",
    }
    if tenant is not None:
        claims["tenant_id"] = tenant
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


def _verifier(
    keys: list[dict[str, Any]],
    *,
    calls: list[str] | None = None,
    jwks_url: str | None = None,
    **kwargs: Any,
) -> JwtVerifier:
    log = calls if calls is not None else []

    def handler(request: httpx.Request) -> httpx.Response:
        log.append(str(request.url))
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={"jwks_uri": f"{ISSUER}/jwks"})
        if request.url.path.endswith("/jwks"):
            return httpx.Response(200, json={"keys": keys})
        return httpx.Response(404)

    return JwtVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks_url=jwks_url,
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


async def test_valid_token_yields_principal(private_key) -> None:
    calls: list[str] = []
    verifier = _verifier([_jwk(private_key, KID)], calls=calls)
    try:
        principal = await verifier.verify(_token(private_key))
    finally:
        await verifier.aclose()

    assert principal.tenant_id == "t-1"
    assert principal.subject == "user-1"
    assert principal.scopes == ("runs:write", "runs:read")
    # 首次校验:发现文档 + JWKS 各一次
    assert len(calls) == 2


async def test_jwks_is_cached_across_verifications(private_key) -> None:
    calls: list[str] = []
    verifier = _verifier([_jwk(private_key, KID)], calls=calls)
    try:
        await verifier.verify(_token(private_key))
        await verifier.verify(_token(private_key))
    finally:
        await verifier.aclose()

    assert len(calls) == 2  # 第二次命中缓存,不再拉取


async def test_explicit_jwks_url_skips_discovery(private_key) -> None:
    calls: list[str] = []
    verifier = _verifier(
        [_jwk(private_key, KID)], calls=calls, jwks_url=f"{ISSUER}/jwks"
    )
    try:
        await verifier.verify(_token(private_key))
    finally:
        await verifier.aclose()

    assert len(calls) == 1
    assert calls[0].endswith("/jwks")


async def test_unknown_kid_triggers_refresh_key_rotation(private_key) -> None:
    """IdP 轮换密钥后:未知 kid 会强制刷新 JWKS 并成功。"""
    served: list[dict[str, Any]] = [_jwk(private_key, "key-old")]
    calls: list[str] = []
    verifier = _verifier(served, calls=calls)
    try:
        with pytest.raises(UnauthorizedError) as first:
            await verifier.verify(_token(private_key, kid="key-new"))
        assert first.value.code == "auth.key_unknown"

        served[:] = [_jwk(private_key, "key-new")]  # 轮换后重新签发
        principal = await verifier.verify(_token(private_key, kid="key-new"))
    finally:
        await verifier.aclose()

    assert principal.tenant_id == "t-1"


async def test_token_without_kid_works_with_single_key(private_key) -> None:
    verifier = _verifier([_jwk(private_key, KID)])
    try:
        principal = await verifier.verify(_token(private_key, kid=""))
    finally:
        await verifier.aclose()

    assert principal.tenant_id == "t-1"


async def test_expired_token_rejected(private_key) -> None:
    verifier = _verifier([_jwk(private_key, KID)])
    try:
        with pytest.raises(UnauthorizedError) as exc_info:
            await verifier.verify(_token(private_key, expires_in=-120))
    finally:
        await verifier.aclose()

    assert exc_info.value.code == "auth.token_invalid"
    assert exc_info.value.status == 401


async def test_wrong_issuer_and_audience_rejected(private_key) -> None:
    verifier = _verifier([_jwk(private_key, KID)])
    try:
        with pytest.raises(UnauthorizedError):
            await verifier.verify(_token(private_key, issuer="https://evil.example"))
        with pytest.raises(UnauthorizedError):
            await verifier.verify(_token(private_key, audience="other-api"))
    finally:
        await verifier.aclose()


async def test_signature_from_other_key_rejected(private_key, other_key) -> None:
    """用别的私钥签名(即使 kid 对得上)也必须拒绝。"""
    verifier = _verifier([_jwk(private_key, KID)])
    try:
        with pytest.raises(UnauthorizedError) as exc_info:
            await verifier.verify(_token(other_key))
    finally:
        await verifier.aclose()

    assert exc_info.value.code == "auth.token_invalid"


async def test_missing_tenant_claim_rejected(private_key) -> None:
    """租户 claim 缺失即拒绝——绝不回退到请求头或其他猜测。"""
    verifier = _verifier([_jwk(private_key, KID)])
    try:
        with pytest.raises(UnauthorizedError) as exc_info:
            await verifier.verify(_token(private_key, tenant=None))
    finally:
        await verifier.aclose()

    assert exc_info.value.code == "auth.tenant_missing"


async def test_custom_tenant_claim(private_key) -> None:
    token = jwt.encode(
        {
            "sub": "u",
            "iss": ISSUER,
            "aud": AUDIENCE,
            "exp": datetime.now(UTC) + timedelta(seconds=60),
            "org_id": "org-9",
        },
        private_key,
        algorithm="RS256",
        headers={"kid": KID},
    )
    verifier = _verifier([_jwk(private_key, KID)], tenant_claim="org_id")
    try:
        principal = await verifier.verify(token)
    finally:
        await verifier.aclose()

    assert principal.tenant_id == "org-9"


async def test_jwks_unreachable_is_502(private_key) -> None:
    """IdP 不可达是上游故障(502),不能报成调用方凭证问题(401)。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("idp down")

    verifier = JwtVerifier(
        issuer=ISSUER, audience=AUDIENCE, transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(UpstreamError) as exc_info:
            await verifier.verify(_token(private_key))
    finally:
        await verifier.aclose()

    assert exc_info.value.code == "auth.jwks_unavailable"
    assert exc_info.value.status == 502


async def test_garbage_token_rejected_before_network(private_key) -> None:
    calls: list[str] = []
    verifier = _verifier([_jwk(private_key, KID)], calls=calls)
    try:
        with pytest.raises(UnauthorizedError) as exc_info:
            await verifier.verify("not-a-jwt")
    finally:
        await verifier.aclose()

    assert exc_info.value.code == "auth.token_invalid"
    assert calls == []  # 连不上的 token 不该触发 JWKS 拉取
