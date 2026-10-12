"""ImapTransport 单元:monkeypatch imaplib,零网络;验证探活/解析/错误映射。"""

import imaplib

import pytest
from common.errors import AppError, UpstreamError

from connector.services import mail

RAW_MSG = (
    b"From: Alice <alice@example.com>\r\n"
    b"Subject: Weekly Report\r\n"
    b"Date: Mon, 12 Oct 2026 10:00:00 +0800\r\n"
    b'Content-Type: text/plain; charset="utf-8"\r\n'
    b"\r\n"
    b"Please find the summary attached.\r\n"
)


class FakeIMAP:
    def __init__(self, host: str, port: int, timeout: float | None = None):
        if port == 0:
            raise ConnectionRefusedError("no route to host")

    def login(self, username: str, password: str) -> None:
        if password == "bad":
            raise imaplib.IMAP4.error("authentication failed")

    def select(self, mailbox: str, readonly: bool = False):
        return ("OK", [b"3"])

    def search(self, charset, criteria):
        return ("OK", [b"1 2"])

    def fetch(self, mid, spec):
        return ("OK", [(b"1 (RFC822 {123}", RAW_MSG)])

    def logout(self) -> None:
        pass


def _patch(monkeypatch) -> None:
    monkeypatch.setattr(mail.imaplib, "IMAP4_SSL", FakeIMAP)


async def test_imap_test_ok(monkeypatch) -> None:
    _patch(monkeypatch)
    transport = mail.ImapTransport("imap.qq.com", 993, "me@qq.com", "auth-code")
    result = await transport.test()
    assert result.ok is True
    assert "成功" in result.detail


async def test_imap_test_auth_failure_is_honest(monkeypatch) -> None:
    _patch(monkeypatch)
    transport = mail.ImapTransport("imap.qq.com", 993, "me@qq.com", "bad")
    result = await transport.test()
    assert result.ok is False
    assert "authentication failed" in result.detail


async def test_imap_test_unreachable(monkeypatch) -> None:
    _patch(monkeypatch)
    transport = mail.ImapTransport("imap.qq.com", 0, "me@qq.com", "auth-code")
    result = await transport.test()
    assert result.ok is False
    assert "ConnectionRefusedError" in result.detail


async def test_imap_fetch_parses_headers(monkeypatch) -> None:
    _patch(monkeypatch)
    transport = mail.ImapTransport("imap.qq.com", 993, "me@qq.com", "auth-code")
    emails = await transport.fetch(limit=1)
    assert len(emails) == 1
    assert emails[0].subject == "Weekly Report"
    assert emails[0].sender == "alice@example.com"
    assert "summary attached" in emails[0].snippet
    assert emails[0].sent_at.startswith("Mon, 12 Oct 2026")


async def test_imap_fetch_maps_transport_error_to_502(monkeypatch) -> None:
    class BoomIMAP(FakeIMAP):
        def login(self, username: str, password: str) -> None:
            raise RuntimeError("network gone")

    monkeypatch.setattr(mail.imaplib, "IMAP4_SSL", BoomIMAP)
    transport = mail.ImapTransport("imap.qq.com", 993, "me@qq.com", "auth-code")
    with pytest.raises(UpstreamError) as exc_info:
        await transport.fetch(limit=10)
    assert exc_info.value.code == "connector.mail_unavailable"


def test_build_transport_rejects_unsupported_provider() -> None:
    class FakeConn:
        provider = "gmail"
        credentials = {}

    with pytest.raises(AppError) as exc_info:
        mail.build_transport(FakeConn())  # type: ignore[arg-type]
    assert exc_info.value.code == "connector.provider_unsupported"
