"""Gmail / Microsoft 365 日历 CRUD(阶段 3B)。

复用 OAuth 邮件传输的 token 刷新;事件统一为 UTC ISO 字符串。
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from common.errors import AppError, UpstreamError

from connector.core.crypto import decrypt_credentials
from connector.models.connections import Connection
from connector.services.oauth_mail import _OAuthTransport


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    title: str
    start: str
    end: str
    description: str
    html_link: str


class CalendarClient(_OAuthTransport):
    provider = "calendar"

    def __init__(
        self,
        provider: str,
        email_address: str,
        credentials: dict,
        *,
        client_id: str,
        client_secret: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._provider = provider
        token_url = (
            "https://oauth2.googleapis.com/token"
            if provider == "gmail"
            else "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        )
        super().__init__(
            email_address,
            credentials,
            client_id=client_id,
            client_secret=client_secret,
            token_url=token_url,
            transport=transport,
        )

    async def list_events(
        self, time_min: str | None = None, time_max: str | None = None
    ) -> list[CalendarEvent]:
        if self._provider == "gmail":
            params = {"singleEvents": "true", "orderBy": "startTime"}
            if time_min:
                params["timeMin"] = time_min
            if time_max:
                params["timeMax"] = time_max
            response = await self._request(
                "GET",
                "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                params=params,
            )
            if response.status_code != 200:
                raise UpstreamError(
                    "connector.calendar_unavailable",
                    f"Google Calendar 列表失败 {response.status_code}",
                )
            return [_google_event(item) for item in response.json().get("items", [])]
        response = await self._request(
            "GET",
            "https://graph.microsoft.com/v1.0/me/events",
            params={
                "$top": 20,
                "$orderby": "start/dateTime",
                "$select": "id,subject,start,end,bodyPreview,webLink",
            },
        )
        if response.status_code != 200:
            raise UpstreamError(
                "connector.calendar_unavailable",
                f"Graph 日历列表失败 {response.status_code}",
            )
        return [_graph_event(item) for item in response.json().get("value", [])]

    async def create_event(
        self, title: str, start: str, end: str, description: str = ""
    ) -> CalendarEvent:
        if self._provider == "gmail":
            response = await self._request(
                "POST",
                "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                json={
                    "summary": title,
                    "description": description,
                    "start": {"dateTime": start},
                    "end": {"dateTime": end},
                },
            )
            if response.status_code not in (200, 201):
                raise UpstreamError(
                    "connector.calendar_create_failed", "创建日历事件失败"
                )
            return _google_event(response.json())
        response = await self._request(
            "POST",
            "https://graph.microsoft.com/v1.0/me/events",
            json={
                "subject": title,
                "body": {"contentType": "Text", "content": description},
                "start": {"dateTime": start, "timeZone": "UTC"},
                "end": {"dateTime": end, "timeZone": "UTC"},
            },
        )
        if response.status_code not in (200, 201):
            raise UpstreamError("connector.calendar_create_failed", "创建日历事件失败")
        return _graph_event(response.json())

    async def update_event(
        self, event_id: str, patch: dict
    ) -> CalendarEvent:
        if self._provider == "gmail":
            body: dict = {}
            if "title" in patch:
                body["summary"] = patch["title"]
            if "description" in patch:
                body["description"] = patch["description"]
            if "start" in patch:
                body["start"] = {"dateTime": patch["start"]}
            if "end" in patch:
                body["end"] = {"dateTime": patch["end"]}
            response = await self._request(
                "PATCH",
                "https://www.googleapis.com/calendar/v3/calendars/primary/events/"
                + event_id,
                json=body,
            )
            if response.status_code != 200:
                raise UpstreamError(
                    "connector.calendar_update_failed", "更新日历事件失败"
                )
            return _google_event(response.json())
        body = {}
        if "title" in patch:
            body["subject"] = patch["title"]
        if "description" in patch:
            body["body"] = {"contentType": "Text", "content": patch["description"]}
        if "start" in patch:
            body["start"] = {"dateTime": patch["start"], "timeZone": "UTC"}
        if "end" in patch:
            body["end"] = {"dateTime": patch["end"], "timeZone": "UTC"}
        response = await self._request(
            "PATCH",
            "https://graph.microsoft.com/v1.0/me/events/" + event_id,
            json=body,
        )
        if response.status_code != 200:
            raise UpstreamError("connector.calendar_update_failed", "更新日历事件失败")
        return _graph_event(response.json())

    async def delete_event(self, event_id: str) -> None:
        url = (
            "https://www.googleapis.com/calendar/v3/calendars/primary/events/"
            if self._provider == "gmail"
            else "https://graph.microsoft.com/v1.0/me/events/"
        )
        response = await self._request("DELETE", url + event_id)
        if response.status_code not in (200, 204):
            raise UpstreamError("connector.calendar_delete_failed", "删除日历事件失败")


def _google_event(item: dict) -> CalendarEvent:
    start = (item.get("start") or {}).get("dateTime") or (
        item.get("start") or {}
    ).get("date", "")
    end = (item.get("end") or {}).get("dateTime") or (item.get("end") or {}).get(
        "date", ""
    )
    return CalendarEvent(
        id=str(item.get("id") or ""),
        title=str(item.get("summary") or "(无标题)"),
        start=str(start),
        end=str(end),
        description=str(item.get("description") or ""),
        html_link=str(item.get("htmlLink") or ""),
    )


def _graph_event(item: dict) -> CalendarEvent:
    return CalendarEvent(
        id=str(item.get("id") or ""),
        title=str(item.get("subject") or "(无标题)"),
        start=str((item.get("start") or {}).get("dateTime") or ""),
        end=str((item.get("end") or {}).get("dateTime") or ""),
        description=str(item.get("bodyPreview") or ""),
        html_link=str(item.get("webLink") or ""),
    )


def build_calendar(
    connection: Connection,
    *,
    credential_secret: str,
    google_client_id: str = "",
    google_client_secret: str = "",
    microsoft_client_id: str = "",
    microsoft_client_secret: str = "",
    transport: httpx.AsyncBaseTransport | None = None,
) -> CalendarClient:
    if connection.provider not in ("gmail", "outlook"):
        raise AppError(
            "connector.calendar_unsupported",
            "该连接不支持日历(仅 Gmail/M365 OAuth)",
        )
    credentials = decrypt_credentials(
        credential_secret, connection.credentials or {}
    )
    if connection.provider == "gmail":
        return CalendarClient(
            "gmail",
            connection.account,
            credentials,
            client_id=google_client_id,
            client_secret=google_client_secret,
            transport=transport,
        )
    return CalendarClient(
        "outlook",
        connection.account,
        credentials,
        client_id=microsoft_client_id,
        client_secret=microsoft_client_secret,
        transport=transport,
    )
