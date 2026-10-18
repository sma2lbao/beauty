"""日历 CRUD 端点(阶段 3B,注入 FakeCalendar 零网络)。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from connector.main import create_app
from connector.services.oauth_calendar import CalendarEvent
from tests.conftest import TENANT, FakeTransport, _settings, verifier


class FakeCalendar:
    def __init__(self) -> None:
        self.events: list[CalendarEvent] = []
        self.deleted: list[str] = []

    async def list_events(self, time_min=None, time_max=None):
        return list(self.events)

    async def create_event(self, title, start, end, description=""):
        event = CalendarEvent(
            id="e1",
            title=title,
            start=start,
            end=end,
            description=description,
            html_link="",
        )
        self.events.append(event)
        return event

    async def update_event(self, event_id, patch):
        base = next(item for item in self.events if item.id == event_id)
        event = CalendarEvent(
            id=base.id,
            title=patch.get("title", base.title),
            start=patch.get("start", base.start),
            end=patch.get("end", base.end),
            description=patch.get("description", base.description),
            html_link=base.html_link,
        )
        self.events = [event if item.id == event_id else item for item in self.events]
        return event

    async def delete_event(self, event_id):
        self.deleted.append(event_id)


def test_calendar_crud(tmp_path: Path) -> None:
    fake = FakeCalendar()
    app = create_app(
        _settings(tmp_path),
        transport_factory=lambda conn: FakeTransport(),
        token_verifier=verifier(),
        calendar_factory=lambda conn: fake,
    )
    with TestClient(app) as client:
        created = client.post(
            "/v1/connections",
            json={
                "provider": "imap",
                "account": "me@qq.com",
                "credentials": {
                    "host": "imap.qq.com",
                    "port": 993,
                    "username": "me@qq.com",
                    "password": "fake-auth-code-16chars",
                },
            },
            headers=TENANT,
        )
        cid = created.json()["id"]

        event = client.post(
            f"/v1/connections/{cid}/events",
            json={
                "title": "周会",
                "start": "2026-10-20T10:00:00Z",
                "end": "2026-10-20T11:00:00Z",
            },
            headers=TENANT,
        )
        assert event.status_code == 201
        assert event.json()["title"] == "周会"

        listing = client.get(f"/v1/connections/{cid}/events", headers=TENANT)
        assert listing.status_code == 200
        assert listing.json()[0]["id"] == "e1"

        updated = client.patch(
            f"/v1/connections/{cid}/events/e1",
            json={"title": "周会(改)"},
            headers=TENANT,
        )
        assert updated.status_code == 200
        assert updated.json()["title"] == "周会(改)"

        deleted = client.delete(
            f"/v1/connections/{cid}/events/e1", headers=TENANT
        )
        assert deleted.status_code == 204
        assert fake.deleted == ["e1"]
