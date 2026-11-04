"""provider 抽象:授权、换 token、刷新、身份与工具调用(设计 §4)。

provider 只碰网络与 provider 语义;凭证加密/落库/租户隔离全部留在 service 层,
所以这里的方法都接收/返回明文凭证 payload,但绝不写库、不读库。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

import httpx

from connector.core.clock import as_utc
from connector.core.config import Settings


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """一个能力(工具)的静态描述:名称、参数 schema 与说明。"""

    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class Identity:
    """授权用户身份:account 写稳定标识(如 provider 侧用户 id)。"""

    account: str
    display_name: str | None = None
    provider_account_id: str | None = None
    external_workspace: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Credentials:
    """provider 无关的凭证 payload(加密后原样存 connections.credentials)。"""

    access_token: str
    refresh_token: str | None = None
    token_type: str = "Bearer"
    scope: str = ""
    expires_at: datetime | None = None
    refresh_expires_at: datetime | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_type": self.token_type,
            "scope": self.scope,
            "expires_at": _iso(self.expires_at),
            "refresh_expires_at": _iso(self.refresh_expires_at),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Credentials:
        access_token = str(payload.get("access_token") or "")
        if not access_token:
            raise ValueError("凭证缺少 access_token")
        return cls(
            access_token=access_token,
            refresh_token=_opt_str(payload.get("refresh_token")),
            token_type=str(payload.get("token_type") or "Bearer"),
            scope=str(payload.get("scope") or ""),
            expires_at=_parse(payload.get("expires_at")),
            refresh_expires_at=_parse(payload.get("refresh_expires_at")),
        )


def _iso(value: datetime | None) -> str | None:
    return as_utc(value).isoformat() if value is not None else None


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return as_utc(value)
    return as_utc(datetime.fromisoformat(str(value)))


def _opt_str(value: Any) -> str | None:
    return str(value) if value else None


class BaseProvider(ABC):
    """一个 SaaS 接入的代码 manifest + 网络行为。"""

    #: 配置/库里的标识(小写)
    name: ClassVar[str]
    #: 工具名前缀(如 Acme -> Acme__read_document)
    prefix: ClassVar[str]
    #: 界面展示名(中文)
    display_name: ClassVar[str]
    description: ClassVar[str]
    auth_kind: ClassVar[str] = "user"
    docs_url: ClassVar[str] = ""
    tools: ClassVar[tuple[ToolSpec, ...]] = ()

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # ---- 租户应用凭证 ----

    def with_tenant_app(self, app_id: str, app_secret: str) -> BaseProvider:
        """返回使用租户自带应用凭证的 provider;默认无租户概念,原样返回。"""
        return self

    # ---- 静态描述 ----

    @property
    def configured(self) -> bool:
        """应用凭证是否齐备;未配置仍可出现在目录里(前端提示去配置)。"""
        return True

    def scopes(self) -> tuple[str, ...]:
        return ()

    def tool_spec(self, full_name: str) -> ToolSpec:
        """按完整工具名取 spec;未知工具抛 KeyError 由调用方转 400。"""
        for spec in self.tools:
            if self.full_tool_name(spec.name) == full_name:
                return spec
        raise KeyError(full_name)

    def full_tool_name(self, tool_name: str) -> str:
        return f"{self.prefix}__{tool_name}"

    # ---- 授权 ----

    @abstractmethod
    def authorize_url(
        self, *, state: str, code_challenge: str, redirect_uri: str
    ) -> str:
        """拼用户授权页 URL(含 PKCE S256 与 scope)。"""

    @abstractmethod
    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        client: httpx.AsyncClient,
    ) -> Credentials:
        """授权码换 token。"""

    @abstractmethod
    async def refresh(
        self, credentials: Credentials, client: httpx.AsyncClient
    ) -> Credentials:
        """refresh token 换新 token(部分 provider 的 refresh 是一次性的)。"""

    @abstractmethod
    async def fetch_identity(
        self, credentials: Credentials, client: httpx.AsyncClient
    ) -> Identity:
        """拉授权用户身份(用于 account/display_name 与连通性探测)。"""

    # ---- 工具 ----

    @abstractmethod
    async def call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        credentials: dict[str, Any],
        client: httpx.AsyncClient,
    ) -> dict[str, Any]:
        """执行一次只读工具;失败抛 ProviderError。"""
