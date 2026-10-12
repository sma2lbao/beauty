"""邮件传输抽象与 IMAP 实装(架构 §8)。

MailTransport 是唯一出口约定:任何提供商(QQ/163 授权码 IMAP、Gmail OAuth、
M365 Graph)都实现同一协议,API 层零感知。传输工厂可注入——
生产 build_transport,测试 FakeTransport,零网络零真实账号。
IMAP 用 stdlib imaplib(授权码即密码),同步调用包 asyncio.to_thread。
"""

from __future__ import annotations

import email
import imaplib
from asyncio import to_thread
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr
from typing import Protocol

from common.errors import AppError, UpstreamError

from connector.models.connections import Connection


@dataclass(frozen=True)
class TestResult:
    ok: bool
    detail: str


@dataclass(frozen=True)
class EmailSummary:
    subject: str
    sender: str
    sent_at: str
    snippet: str


class MailTransport(Protocol):
    """传输协议:连通性探活 + 收件箱拉取(只读)。"""

    async def test(self) -> TestResult: ...

    async def fetch(self, limit: int) -> list[EmailSummary]: ...


def _decode(value: str | None) -> str:
    """MIME 编码头域解码(=?utf-8?b?...?= 之类),失败退回原文。"""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _body_text(msg: Message) -> str:
    """取第一段 text/plain;解码失败以 replace 兜底。"""
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_type() != "text/plain":
            continue
        payload = part.get_payload(decode=True)
        charset = part.get_content_charset() or "utf-8"
        return (payload or b"").decode(charset, "replace")
    return ""


def _to_summary(msg: Message) -> EmailSummary:
    subject = _decode(msg.get("Subject"))
    sender_raw = _decode(msg.get("From"))
    body = _body_text(msg)
    return EmailSummary(
        subject=subject or "(无主题)",
        sender=parseaddr(sender_raw)[1] or sender_raw,
        sent_at=msg.get("Date", ""),
        snippet=" ".join(body.split())[:200],
    )


class ImapTransport:
    """stdlib imaplib 实装:IMAPS 登录 + INBOX 最近 N 封(只读)。"""

    connect_timeout_seconds = 10.0  # 防真网黑洞挂死端点

    def __init__(self, host: str, port: int, username: str, password: str) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password

    def _login(self) -> imaplib.IMAP4_SSL:
        client = imaplib.IMAP4_SSL(
            self.host, self.port, timeout=self.connect_timeout_seconds
        )
        client.login(self.username, self.password)
        return client

    async def test(self) -> TestResult:
        """登录 + 选 INBOX 探活;失败不抛,诚实返回 ok=False。"""

        def probe() -> None:
            client = self._login()
            try:
                client.select("INBOX", readonly=True)
            finally:
                client.logout()

        try:
            await to_thread(probe)
        except Exception as exc:
            return TestResult(ok=False, detail=f"{type(exc).__name__}: {exc}")
        return TestResult(ok=True, detail="IMAP 登录成功")

    async def fetch(self, limit: int) -> list[EmailSummary]:
        """拉最近 limit 封(倒序取尾部 id);传输异常统一映射 502。"""

        def pull() -> list[EmailSummary]:
            client = self._login()
            try:
                client.select("INBOX", readonly=True)
                _, items = client.search(None, "ALL")
                out: list[EmailSummary] = []
                for mid in items[0].split()[-limit:]:
                    _, msg_data = client.fetch(mid, "(RFC822)")
                    raw = next(
                        (part[1] for part in msg_data if isinstance(part, tuple)),
                        None,
                    )
                    if raw is None:
                        continue
                    out.append(_to_summary(email.message_from_bytes(raw)))
                return out
            finally:
                client.logout()

        try:
            return await to_thread(pull)
        except Exception as exc:
            raise UpstreamError(
                "connector.mail_unavailable", f"邮件收取失败:{exc}"
            ) from exc


def build_transport(connection: Connection) -> MailTransport:
    """按 provider 装配传输;阶段 A 仅 imap(gmail/outlook 见 §8 阶段 B)。"""
    if connection.provider != "imap":
        raise AppError(
            "connector.provider_unsupported",
            f"提供商 {connection.provider} 暂不支持(阶段 A 仅 imap)",
            details={"provider": connection.provider},
        )
    cred = connection.credentials or {}
    return ImapTransport(
        host=str(cred.get("host", "")),
        port=int(cred.get("port", 993)),
        username=str(cred.get("username", "")),
        password=str(cred.get("password", "")),
    )
