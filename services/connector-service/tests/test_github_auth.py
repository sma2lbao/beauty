"""GitHub provider 的授权/刷新/身份/令牌校验(全 MockTransport,零网络)。"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from connector.core.clock import utcnow
from connector.core.config import Settings
from connector.providers.base import Credentials
from connector.providers.errors import ProviderError
from connector.providers.github import REFRESH_TOKEN_TTL_SECONDS, GithubProvider
from tests.github_mock import GithubMock, token_payload

CID = "gh-client-id"
SECRET = "gh-client-secret"


def _provider(**overrides: object) -> GithubProvider:
    values: dict[str, object] = {
        "_env_file": None,
        "env": "dev",
        "github_client_id": CID,
        "github_client_secret": SECRET,
    }
    values.update(overrides)
    return GithubProvider(Settings(**values))


def _credentials(refresh_token: str | None = "ghr_refresh_1") -> Credentials:
    return Credentials(
        access_token="ghu_access_1",
        refresh_token=refresh_token,
        token_type="bearer",
        scope="",
    )


def _client(mock: GithubMock) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(mock.handler))


def test_configured_requires_both_client_fields() -> None:
    assert _provider().configured is True
    assert _provider(github_client_id="").configured is False
    assert _provider(github_client_secret="").configured is False


def test_authorize_url_has_pkce_state_and_offline_scope() -> None:
    url = _provider().authorize_url(
        state="state-1",
        code_challenge="challenge-1",
        redirect_uri="https://api.test/v1/oauth/github/callback",
    )
    assert url.startswith("https://github.com/login/oauth/authorize?")
    assert "client_secret" not in url
    query = httpx.URL(url).params
    assert query["client_id"] == CID
    assert query["state"] == "state-1"
    assert query["code_challenge"] == "challenge-1"
    assert query["code_challenge_method"] == "S256"
    assert query["scope"] == "offline_access"
    assert query["redirect_uri"] == ("https://api.test/v1/oauth/github/callback")


async def test_exchange_code_posts_secret_in_body_and_sets_expiry() -> None:
    mock = GithubMock()
    async with _client(mock) as client:
        credentials = await _provider().exchange_code(
            code="code-1",
            code_verifier="verifier-1",
            redirect_uri="https://api.test/callback",
            client=client,
        )
    body = mock.token_calls[0]
    assert body == {
        "client_id": CID,
        "client_secret": SECRET,
        "code": "code-1",
        "redirect_uri": "https://api.test/callback",
        "code_verifier": "verifier-1",
    }
    # secret 只在表单 body,不出现在 token URL 的 query 里
    assert "client_secret" not in mock.token_urls[0]
    assert "?" not in mock.token_urls[0]
    assert credentials.access_token == "ghu_access_1"
    assert credentials.refresh_token == "ghr_refresh_1"
    assert credentials.scope == ""
    assert credentials.token_type == "bearer"
    after = utcnow()
    assert after + timedelta(seconds=28_700) <= credentials.expires_at
    assert credentials.expires_at <= after + timedelta(seconds=28_800)
    assert (
        after + timedelta(seconds=REFRESH_TOKEN_TTL_SECONDS - 10)
        <= credentials.refresh_expires_at
    )
    assert credentials.refresh_expires_at <= after + timedelta(
        seconds=REFRESH_TOKEN_TTL_SECONDS
    )


@pytest.mark.parametrize(
    ("overrides", "hint"),
    [
        ({"refresh_token": None}, "Expire user authorization tokens"),
        ({"expires_in": None}, "Expire user authorization tokens"),
    ],
)
async def test_exchange_code_rejects_non_expiring_app(
    overrides: dict[str, object], hint: str
) -> None:
    mock = GithubMock()
    mock.token_response = token_payload(**overrides)
    async with _client(mock) as client:
        with pytest.raises(ProviderError) as raised:
            await _provider().exchange_code(
                code="code-1",
                code_verifier="verifier-1",
                redirect_uri="https://api.test/callback",
                client=client,
            )
    assert raised.value.code == "connector.oauth_failed"
    assert raised.value.retryable is False
    assert hint in (raised.value.hint or "")


async def test_refresh_rotates_refresh_token() -> None:
    mock = GithubMock()
    async with _client(mock) as client:
        refreshed = await _provider().refresh(_credentials(), client)
    assert refreshed.access_token == "ghu_access_2"
    assert refreshed.refresh_token == "ghr_refresh_2"
    assert refreshed.refresh_expires_at is not None
    body = mock.token_calls[0]
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == "ghr_refresh_1"
    assert body["client_secret"] == SECRET


async def test_refresh_without_refresh_token_is_noop_for_pat() -> None:
    mock = GithubMock()
    credentials = _credentials(refresh_token=None)
    async with _client(mock) as client:
        refreshed = await _provider().refresh(credentials, client)
    assert refreshed is credentials
    assert mock.token_calls == []


async def test_refresh_bad_token_marks_auth_expired() -> None:
    mock = GithubMock()
    async with _client(mock) as client:
        with pytest.raises(ProviderError) as raised:
            await _provider().refresh(_credentials(refresh_token="bad-refresh"), client)
    assert raised.value.code == "connector.auth_expired"
    assert raised.value.retryable is False


async def test_token_endpoint_5xx_and_timeout_are_retryable() -> None:
    mock = GithubMock()
    mock.token_status = 503
    async with _client(mock) as client:
        with pytest.raises(ProviderError) as raised:
            await _provider().exchange_code(
                code="code-1",
                code_verifier="verifier-1",
                redirect_uri="https://api.test/callback",
                client=client,
            )
    assert raised.value.code == "connector.upstream_unavailable"
    assert raised.value.retryable is True

    timeout_mock = GithubMock()
    timeout_mock.token_raise = httpx.ReadTimeout("slow")
    async with _client(timeout_mock) as client:
        with pytest.raises(ProviderError) as timed_out:
            await _provider().exchange_code(
                code="code-1",
                code_verifier="verifier-1",
                redirect_uri="https://api.test/callback",
                client=client,
            )
    assert timed_out.value.code == "connector.upstream_timeout"
    assert timed_out.value.retryable is True


async def test_exchange_code_bad_client_credentials() -> None:
    mock = GithubMock()
    mock.token_response = {"error": "incorrect_client_credentials"}
    async with _client(mock) as client:
        with pytest.raises(ProviderError) as raised:
            await _provider().exchange_code(
                code="code-1",
                code_verifier="verifier-1",
                redirect_uri="https://api.test/callback",
                client=client,
            )
    assert raised.value.code == "connector.oauth_failed"
    assert "client_id/client_secret" in (raised.value.hint or "")


async def test_fetch_identity_reads_user_and_installations() -> None:
    mock = GithubMock()
    async with _client(mock) as client:
        identity = await _provider().fetch_identity(_credentials(), client)
    assert identity.account == "octocat"
    assert identity.display_name == "Octo Cat"
    assert identity.provider_account_id == "42"
    assert identity.metadata["has_installation"] is True
    assert identity.metadata["installations_total"] == 1
    assert identity.metadata["installations"] == [{"login": "octo", "id": 11}]
    assert mock.api_calls[0]["auth"] == "Bearer ghu_access_1"
    assert mock.api_calls[0]["path"] == "/user"
    assert mock.api_calls[1]["path"] == "/user/installations"
    assert mock.api_calls[1]["query"]["per_page"] == "100"


async def test_fetch_identity_zero_installations() -> None:
    mock = GithubMock()
    mock.installations = []
    async with _client(mock) as client:
        identity = await _provider().fetch_identity(_credentials(), client)
    assert identity.metadata == {
        "installations": [],
        "has_installation": False,
        "installations_total": 0,
    }


async def test_fetch_identity_pat_skips_installations() -> None:
    mock = GithubMock()
    async with _client(mock) as client:
        identity = await _provider().fetch_identity(
            _credentials(refresh_token=None), client
        )
    assert identity.metadata == {}
    assert [call["path"] for call in mock.api_calls] == ["/user"]


async def test_fetch_identity_installation_error_is_soft() -> None:
    mock = GithubMock()
    mock.fail["/user/installations"] = httpx.Response(
        403, json={"message": "Resource not accessible"}
    )
    async with _client(mock) as client:
        identity = await _provider().fetch_identity(_credentials(), client)
    assert identity.metadata["has_installation"] is None
    assert identity.metadata["installations"] == []
    assert identity.metadata["installations_error"] == ("connector.upstream_error")


async def test_fetch_identity_rejects_incomplete_user() -> None:
    mock = GithubMock()
    mock.fail["/user"] = httpx.Response(200, json={"login": "octocat"})
    async with _client(mock) as client:
        with pytest.raises(ProviderError) as raised:
            await _provider().fetch_identity(_credentials(), client)
    assert raised.value.code == "connector.upstream_error"


async def test_connect_with_token_returns_static_credentials() -> None:
    mock = GithubMock()
    async with _client(mock) as client:
        credentials, identity = await _provider().connect_with_token("pat-1", client)
    assert credentials.access_token == "pat-1"
    assert credentials.refresh_token is None
    assert credentials.expires_at is None
    assert credentials.token_type == "Bearer"
    assert identity.account == "octocat"
    assert identity.display_name == "Octo Cat"
    assert identity.provider_account_id == "42"
    assert identity.metadata == {"connect": "token"}
    assert mock.api_calls[0]["auth"] == "Bearer pat-1"


async def test_connect_with_token_rejects_empty_and_bad_tokens() -> None:
    mock = GithubMock()
    async with _client(mock) as client:
        with pytest.raises(ProviderError) as empty:
            await _provider().connect_with_token("  ", client)
        assert empty.value.code == "connector.invalid_credentials"
        assert mock.api_calls == []

        with pytest.raises(ProviderError) as bad:
            await _provider().connect_with_token("bad-token", client)
        assert bad.value.code == "connector.invalid_credentials"
        assert bad.value.hint is not None
        assert "PAT" in bad.value.hint


async def test_connect_with_token_propagates_non_auth_errors() -> None:
    mock = GithubMock()
    mock.fail["/user"] = httpx.Response(403, json={"message": "forbidden"})
    async with _client(mock) as client:
        with pytest.raises(ProviderError) as raised:
            await _provider().connect_with_token("pat-1", client)
    assert raised.value.code == "connector.upstream_error"
