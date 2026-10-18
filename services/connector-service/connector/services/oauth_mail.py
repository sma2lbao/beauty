"""Gmail / Microsoft Graph OAuth 邮件传输(阶段 3A)。

独立模块避免与 IMAP 传输互相 import;由 build_transport 惰性导入。
"""

from __future__ import annotations

import base64
import time
from email.message import EmailMessage
from email.utils import parseaddr

import httpx
from common.errors import UpstreamError

from connector.services.mail import AttachmentInfo, EmailSummary, TestResult


class _OAuthTransport:
    provider = ""

    def __init__(
        self,
        email_address: str,
        credentials: dict,
        *,
        client_id: str,
        client_secret: str,
        token_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.email_address = email_address
        self._refresh_token = str(credentials.get("refresh_token") or "")
        self._access_token = str(credentials.get("access_token") or "")
        self._expires_at = int(credentials.get("expires_at") or 0)
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_url = token_url
        self._transport = transport

    async def _token(self) -> str:
        if self._access_token and self._expires_at - time.time() > 60:
            return self._access_token
        if not self._refresh_token:
            raise UpstreamError(
                "connector.oauth_missing_refresh",
                "OAuth 连接缺少 refresh token,请重新授权",
            )
        async with httpx.AsyncClient(
            timeout=15.0, transport=self._transport
        ) as client:
            response = await client.post(
                self._token_url,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": self._refresh_token,
                    "grant_type": "refresh_token",
                },
                headers={"Accept": "application/json"},
            )
        if response.status_code != 200:
            raise UpstreamError(
                "connector.oauth_refresh_failed",
                f"{self.provider} token 刷新失败 {response.status_code}",
            )
        body = response.json()
        self._access_token = str(body.get("access_token") or "")
        self._expires_at = int(time.time()) + int(body.get("expires_in") or 3600)
        return self._access_token

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        token = await self._token()
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(
            timeout=20.0, transport=self._transport
        ) as client:
            return await client.request(method, url, headers=headers, **kwargs)


class GmailTransport(_OAuthTransport):
    provider = "gmail"

    def __init__(self, email_address: str, credentials: dict, **kwargs) -> None:
        super().__init__(
            email_address,
            credentials,
            token_url="https://oauth2.googleapis.com/token",
            **kwargs,
        )

    async def test(self) -> TestResult:
        try:
            response = await self._request(
                "GET",
                "https://gmail.googleapis.com/gmail/v1/users/me/profile",
            )
            if response.status_code != 200:
                return TestResult(False, f"Gmail API {response.status_code}")
            return TestResult(True, "Gmail API 连接成功")
        except Exception as exc:  # noqa: BLE001
            return TestResult(False, f"{type(exc).__name__}: {exc}")

    async def fetch(self, limit: int) -> list[EmailSummary]:
        try:
            listing = await self._request(
                "GET",
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                params={"maxResults": limit},
            )
            if listing.status_code != 200:
                raise UpstreamError(
                    "connector.mail_unavailable",
                    f"Gmail 列表失败 {listing.status_code}",
                )
            out: list[EmailSummary] = []
            for item in listing.json().get("messages", [])[:limit]:
                message_id = str(item.get("id") or "")
                detail = await self._request(
                    "GET",
                    "https://gmail.googleapis.com/gmail/v1/users/me/messages/"
                    + message_id,
                    params={"format": "full"},
                )
                if detail.status_code == 200:
                    out.append(_gmail_summary(detail.json()))
            return out
        except UpstreamError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise UpstreamError(
                "connector.mail_unavailable", f"Gmail 收取失败:{exc}"
            ) from exc

    async def send(self, to: str, subject: str, body: str) -> str:
        message = EmailMessage()
        message["From"] = self.email_address
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        try:
            response = await self._request(
                "POST",
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                json={"raw": raw},
            )
        except Exception as exc:  # noqa: BLE001
            raise UpstreamError(
                "connector.send_failed", f"Gmail 发信失败:{exc}"
            ) from exc
        if response.status_code not in (200, 201):
            raise UpstreamError(
                "connector.send_failed", f"Gmail 发信失败 {response.status_code}"
            )
        return f"已发送至 {to}"


    async def list_attachments(self, message_id: str) -> list[AttachmentInfo]:
        response = await self._request(
            "GET",
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/" + message_id,
            params={"format": "full"},
        )
        if response.status_code != 200:
            raise UpstreamError("connector.mail_unavailable", "Gmail 读取邮件失败")
        payload = response.json().get("payload") or {}
        return _gmail_attachments(payload)

    async def download_attachment(
        self, message_id: str, attachment_id: str
    ) -> tuple[bytes, str, str]:
        attachments = await self.list_attachments(message_id)
        match = next((item for item in attachments if item.id == attachment_id), None)
        if match is None:
            raise UpstreamError("connector.attachment_not_found", "附件不存在")
        response = await self._request(
            "GET",
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/"
            + message_id
            + "/attachments/"
            + attachment_id,
        )
        if response.status_code != 200:
            raise UpstreamError(
                "connector.attachment_unavailable", "Gmail 附件下载失败"
            )
        data = response.json().get("data") or ""
        padded = data + "=" * (-len(data) % 4)
        return (
            base64.urlsafe_b64decode(padded),
            match.content_type,
            match.filename,
        )

class OutlookTransport(_OAuthTransport):
    provider = "outlook"

    def __init__(self, email_address: str, credentials: dict, **kwargs) -> None:
        super().__init__(
            email_address,
            credentials,
            token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
            **kwargs,
        )

    async def test(self) -> TestResult:
        try:
            response = await self._request(
                "GET", "https://graph.microsoft.com/v1.0/me?$select=mail"
            )
            if response.status_code != 200:
                return TestResult(False, f"Graph API {response.status_code}")
            return TestResult(True, "Microsoft Graph 连接成功")
        except Exception as exc:  # noqa: BLE001
            return TestResult(False, f"{type(exc).__name__}: {exc}")

    async def fetch(self, limit: int) -> list[EmailSummary]:
        try:
            response = await self._request(
                "GET",
                "https://graph.microsoft.com/v1.0/me/messages",
                params={
                    "$top": limit,
                    "$select": "id,conversationId,subject,from,"
                    "receivedDateTime,bodyPreview,hasAttachments",
                },
            )
            if response.status_code != 200:
                raise UpstreamError(
                    "connector.mail_unavailable",
                    f"Graph 列表失败 {response.status_code}",
                )
            out: list[EmailSummary] = []
            for item in response.json().get("value", [])[:limit]:
                sender = (item.get("from") or {}).get("emailAddress") or {}
                out.append(
                    EmailSummary(
                        subject=item.get("subject") or "(无主题)",
                        sender=sender.get("address") or "",
                        sent_at=item.get("receivedDateTime") or "",
                        snippet=item.get("bodyPreview") or "",
                        id=item.get("id") or "",
                        thread_id=item.get("conversationId") or "",
                        has_attachments=bool(item.get("hasAttachments")),
                    )
                )
            return out
        except UpstreamError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise UpstreamError(
                "connector.mail_unavailable", f"Graph 收取失败:{exc}"
            ) from exc

    async def send(self, to: str, subject: str, body: str) -> str:
        try:
            response = await self._request(
                "POST",
                "https://graph.microsoft.com/v1.0/me/sendMail",
                json={
                    "message": {
                        "subject": subject,
                        "body": {"contentType": "Text", "content": body},
                        "toRecipients": [{"emailAddress": {"address": to}}],
                    },
                    "saveToSentItems": True,
                },
            )
        except Exception as exc:  # noqa: BLE001
            raise UpstreamError(
                "connector.send_failed", f"Outlook 发信失败:{exc}"
            ) from exc
        if response.status_code not in (200, 202):
            raise UpstreamError(
                "connector.send_failed", f"Outlook 发信失败 {response.status_code}"
            )
        return f"已发送至 {to}"


    async def list_attachments(self, message_id: str) -> list[AttachmentInfo]:
        response = await self._request(
            "GET",
            "https://graph.microsoft.com/v1.0/me/messages/"
            + message_id
            + "/attachments",
            params={"$select": "id,name,contentType,size"},
        )
        if response.status_code != 200:
            raise UpstreamError("connector.mail_unavailable", "Graph 读取附件失败")
        return [
            AttachmentInfo(
                id=str(item.get("id") or ""),
                filename=str(item.get("name") or "attachment"),
                content_type=str(item.get("contentType") or "application/octet-stream"),
                size_bytes=int(item.get("size") or 0),
            )
            for item in response.json().get("value", [])
        ]

    async def download_attachment(
        self, message_id: str, attachment_id: str
    ) -> tuple[bytes, str, str]:
        attachments = await self.list_attachments(message_id)
        match = next((item for item in attachments if item.id == attachment_id), None)
        if match is None:
            raise UpstreamError("connector.attachment_not_found", "附件不存在")
        response = await self._request(
            "GET",
            "https://graph.microsoft.com/v1.0/me/messages/"
            + message_id
            + "/attachments/"
            + attachment_id
            + "/$value",
        )
        if response.status_code != 200:
            raise UpstreamError(
                "connector.attachment_unavailable", "Graph 附件下载失败"
            )
        return response.content, match.content_type, match.filename


def _gmail_attachments(payload: dict) -> list[AttachmentInfo]:
    out: list[AttachmentInfo] = []
    for part in payload.get("parts") or []:
        filename = str(part.get("filename") or "")
        body = part.get("body") or {}
        if filename and body.get("attachmentId"):
            out.append(
                AttachmentInfo(
                    id=str(body["attachmentId"]),
                    filename=filename,
                    content_type=str(
                        part.get("mimeType") or "application/octet-stream"
                    ),
                    size_bytes=int(body.get("size") or 0),
                )
            )
        if part.get("parts"):
            out.extend(_gmail_attachments(part))
    return out


def _gmail_summary(message: dict) -> EmailSummary:
    payload = message.get("payload") or {}
    headers = {
        str(item.get("name", "")).lower(): str(item.get("value", ""))
        for item in payload.get("headers", [])
    }

    def has_attachment(parts: list[dict]) -> bool:
        for part in parts:
            if part.get("filename"):
                return True
            if part.get("parts") and has_attachment(part["parts"]):
                return True
        return False

    return EmailSummary(
        subject=headers.get("subject") or "(无主题)",
        sender=parseaddr(headers.get("from", ""))[1] or headers.get("from", ""),
        sent_at=headers.get("date") or "",
        snippet=message.get("snippet") or "",
        id=message.get("id") or "",
        thread_id=message.get("threadId") or "",
        has_attachments=has_attachment(payload.get("parts") or []),
    )
