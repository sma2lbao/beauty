"""连接与邮件 DTO:凭证只进(ConnectionCreate)不出(读端无凭证字段)。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ConnectionStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"  # 探活发现凭证失效时置(阶段 B 自动流转)


class ImapCredentials(BaseModel):
    # IMAP LOGIN 命令按 ASCII 编码,非 ASCII 凭证注册时就拦下(422),
    # 免得探活时炸出难懂的 UnicodeEncodeError;QQ/163 授权码本就全是可打印 ASCII
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=993, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=320, pattern=r"^[ -~]+$")
    password: str = Field(
        min_length=1, max_length=200, pattern=r"^[ -~]+$"
    )  # QQ/163 授权码


class ConnectionCreate(BaseModel):
    # 阶段 A 仅 imap(授权码双轨);gmail/outlook 走 OAuth 为阶段 B(§8)
    provider: str = Field(default="imap", pattern=r"^imap$")
    account: str = Field(min_length=3, max_length=320)
    display_name: str | None = Field(default=None, max_length=200)
    credentials: ImapCredentials


class ConnectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    provider: str
    account: str
    display_name: str | None
    status: ConnectionStatus
    last_checked_at: datetime | None
    created_at: datetime


class EmailRead(BaseModel):
    subject: str
    sender: str
    sent_at: str  # RFC 2822 原文(规范化解析为阶段 B)
    snippet: str


class TestResultRead(BaseModel):
    ok: bool
    detail: str
