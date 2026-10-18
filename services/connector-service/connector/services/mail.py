"""邮件传输抽象与 IMAP/SMTP 实装(架构 §8)。

MailTransport 是唯一出口约定:任何提供商(QQ/163 授权码 IMAP+SMTP、Gmail
OAuth、M365 Graph)都实现同一协议,API 层零感知。传输工厂可注入——
生产 build_transport,测试 FakeTransport,零网络零真实账号。
IMAP/SMTP 用 stdlib(授权码即密码),同步调用包 asyncio.to_thread。
"""

from __future__ import annotations

import email
import imaplib
import smtplib
from asyncio import to_thread
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from email.utils import parseaddr
from typing import Protocol

import httpx
from common.errors import AppError, UpstreamError

from connector.core.crypto import decrypt_credentials
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
    id: str = ""
    thread_id: str = ""
    has_attachments: bool = False


class MailTransport(Protocol):
    """传输协议:探活 + 收件箱拉取(只读)+ 发信(写操作,调用方须先过确认闸)。"""

    async def test(self) -> TestResult: ...

    async def fetch(self, limit: int) -> list[EmailSummary]: ...

    async def send(self, to: str, subject: str, body: str) -> str: ...


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
    parts = msg.walk() if msg.is_multipart() else [msg]
    has_attachments = any(part.get_filename() for part in parts)
    message_id = str(msg.get("Message-ID") or "").strip()
    references = str(msg.get("References") or "").strip()
    return EmailSummary(
        subject=subject or "(无主题)",
        sender=parseaddr(sender_raw)[1] or sender_raw,
        sent_at=msg.get("Date", ""),
        snippet=" ".join(body.split())[:200],
        id=message_id.strip("<>") or str(msg.get("Date", "")),
        thread_id=references.split()[0].strip("<>") if references else message_id,
        has_attachments=has_attachments,
    )


class ImapTransport:
    """stdlib imaplib + smtplib 实装:收(IMAPS)与发(SMTPS)共用同一凭证。"""

    connect_timeout_seconds = 10.0  # 防真网黑洞挂死端点

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        smtp_host: str | None = None,
        smtp_port: int | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        # 不填则按 imap.<域> → smtp.<域> 推导(QQ/163 惯例),端口默认 465
        self.smtp_host = smtp_host or host.replace("imap.", "smtp.", 1)
        self.smtp_port = smtp_port or 465

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

    async def send(self, to: str, subject: str, body: str) -> str:
        """SMTP_SSL 发信(授权码同 IMAP);失败统一映射 502 connector.send_failed。"""
        message = EmailMessage()
        message["From"] = self.username
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)

        def push() -> None:
            client = smtplib.SMTP_SSL(
                self.smtp_host, self.smtp_port, timeout=self.connect_timeout_seconds
            )
            try:
                client.login(self.username, self.password)
                client.send_message(message)
            finally:
                client.quit()

        try:
            await to_thread(push)
        except Exception as exc:
            raise UpstreamError("connector.send_failed", f"邮件发送失败:{exc}") from exc
        return f"已发送至 {to}"


def build_transport(
    connection: Connection,
    *,
    credential_secret: str,
    google_client_id: str = "",
    google_client_secret: str = "",
    microsoft_client_id: str = "",
    microsoft_client_secret: str = "",
    oauth_transport: httpx.AsyncBaseTransport | None = None,
) -> MailTransport:
    """按 provider 装配传输:IMAP 授权码或 Gmail/M365 OAuth。"""
    from connector.services.oauth_mail import GmailTransport, OutlookTransport

    cred = decrypt_credentials(credential_secret, connection.credentials or {})
    if connection.provider == "imap":
        smtp_port = cred.get("smtp_port")
        return ImapTransport(
            host=str(cred.get("host", "")),
            port=int(cred.get("port", 993)),
            username=str(cred.get("username", "")),
            password=str(cred.get("password", "")),
            smtp_host=cred.get("smtp_host"),
            smtp_port=int(smtp_port) if smtp_port else None,
        )
    if connection.provider == "gmail":
        return GmailTransport(
            connection.account,
            cred,
            client_id=google_client_id,
            client_secret=google_client_secret,
            transport=oauth_transport,
        )
    if connection.provider == "outlook":
        return OutlookTransport(
            connection.account,
            cred,
            client_id=microsoft_client_id,
            client_secret=microsoft_client_secret,
            transport=oauth_transport,
        )
    raise AppError(
        "connector.provider_unsupported",
        f"提供商 {connection.provider} 暂不支持",
        details={"provider": connection.provider},
    )
