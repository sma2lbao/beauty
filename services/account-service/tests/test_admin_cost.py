"""平台成本汇总 API 验收(P1-K/M2,eval-and-cost-dashboard §3)。

装配同 test_admin.py(本 app 真签 token + 本 app JWKS 验签),用量行直接写
sqlite:需要跨天/跨账号/未计价/BYOK 这类「内部投递不会产生」的形态。
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from common.auth import JwtVerifier
from fastapi.testclient import TestClient

from account.core.config import Settings
from account.main import create_app
from tests.conftest import _register_account

ADMIN_EMAIL = "root@example.com"
INTERNAL_SECRET = "test-internal"
PASSWORD = "hunter2ok"
GHOST_ID = "f" * 32


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """真签发链路(本 app 签名 + 本 app JWKS 验签),与 test_admin.py 一致。"""
    settings = Settings(
        _env_file=None,
        env="dev",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        auto_create_tables=True,
        admin_emails=[ADMIN_EMAIL],
        internal_secret=INTERNAL_SECRET,
    )
    app = create_app(settings)
    verifier = JwtVerifier(
        issuer=settings.auth_issuer,
        audience=settings.auth_audience,
        transport=httpx.ASGITransport(app=app),
    )
    app.state.token_verifier = verifier
    with TestClient(app) as test_client:
        yield test_client
    asyncio.run(verifier.aclose())


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register(client: TestClient, email: str) -> dict:
    return _register_account(client, email=email, password=PASSWORD)


def _admin_headers(client: TestClient) -> dict[str, str]:
    return _auth(_register(client, ADMIN_EMAIL)["access_token"])


def _start_of_today() -> datetime:
    now = datetime.now(UTC)
    return datetime(now.year, now.month, now.day, tzinfo=UTC)


def _seed_usage(
    tmp_path: Path,
    *,
    account_id: str,
    created_at: datetime,
    model: str = "gpt-x",
    tokens: int = 10,
    cost: float | None = None,
    source: str = "platform",
) -> None:
    """直接写库:跨天/跨账号/未计价/BYOK 的用量事件。"""
    stamp = (
        created_at.astimezone(UTC)
        .replace(tzinfo=None)
        .isoformat(sep=" ", timespec="microseconds")
    )
    with sqlite3.connect(tmp_path / "test.db") as conn:
        conn.execute(
            "INSERT INTO usage_events"
            " (id, account_id, model, prompt_tokens, completion_tokens,"
            " total_tokens, cost_usd, billing_source, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                uuid4().hex,
                account_id,
                model,
                tokens,
                0,
                tokens,
                cost,
                source,
                stamp,
                stamp,
            ),
        )


def _summary(
    client: TestClient, headers: dict[str, str], days: int | None = None
) -> dict:
    params = {} if days is None else {"days": days}
    response = client.get("/v1/admin/cost/summary", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_cost_summary_requires_admin(client: TestClient) -> None:
    assert client.get("/v1/admin/cost/summary").status_code == 401

    alice = _register(client, "alice@example.com")
    denied = client.get("/v1/admin/cost/summary", headers=_auth(alice["access_token"]))
    assert denied.status_code == 403
    assert denied.json()["code"] == "admin.required"


def test_cost_summary_empty_window_is_zero_filled(client: TestClient) -> None:
    root = _admin_headers(client)
    body = _summary(client, root)

    today = _start_of_today()
    since = today - timedelta(days=29)
    assert body["days"] == 30
    assert body["since"] == since.strftime("%Y-%m-%dT00:00:00Z")
    assert body["totals"] == {
        "events": 0,
        "tokens": 0,
        "cost_usd": 0.0,
        "unpriced_events": 0,
        "platform_events": 0,
        "byok_events": 0,
    }
    assert body["cost_by_source"] == [
        {"source": "platform", "events": 0, "cost_usd": 0.0},
        {"source": "byok", "events": 0, "cost_usd": 0.0},
    ]
    assert [row["date"] for row in body["by_day"]] == [
        (since + timedelta(days=offset)).date().isoformat() for offset in range(30)
    ]
    assert all(
        row
        == {
            "date": row["date"],
            "events": 0,
            "tokens": 0,
            "cost_usd": 0.0,
            "unpriced_events": 0,
        }
        for row in body["by_day"]
    )
    assert body["by_model"] == []
    assert body["top_accounts"] == []


def test_cost_summary_aggregates_days_models_sources_and_accounts(
    client: TestClient, tmp_path: Path
) -> None:
    alice = _register(client, "alice@example.com")
    bob = _register(client, "bob@example.com")
    today = _start_of_today()
    yesterday = today - timedelta(hours=5)

    _seed_usage(
        tmp_path,
        account_id=alice["account_id"],
        created_at=today + timedelta(hours=2),
        model="gpt-x",
        tokens=100,
        cost=0.5,
    )
    _seed_usage(
        tmp_path,
        account_id=alice["account_id"],
        created_at=today + timedelta(hours=3),
        model="gpt-x",
        tokens=50,
        cost=0.25,
        source="byok",
    )
    _seed_usage(
        tmp_path,
        account_id=bob["account_id"],
        created_at=yesterday,
        model="gpt-y",
        tokens=30,
        cost=None,
    )
    # 窗口外(40 天前):默认 30 天窗口不能计入
    _seed_usage(
        tmp_path,
        account_id=alice["account_id"],
        created_at=today - timedelta(days=40),
        model="gpt-x",
        tokens=999,
        cost=9.99,
    )

    root = _admin_headers(client)
    body = _summary(client, root)

    assert body["totals"] == {
        "events": 3,
        "tokens": 180,
        "cost_usd": 0.75,
        "unpriced_events": 1,
        "platform_events": 2,
        "byok_events": 1,
    }
    assert body["cost_by_source"] == [
        {"source": "platform", "events": 2, "cost_usd": 0.5},
        {"source": "byok", "events": 1, "cost_usd": 0.25},
    ]

    by_day = {row["date"]: row for row in body["by_day"]}
    assert by_day[today.date().isoformat()] == {
        "date": today.date().isoformat(),
        "events": 2,
        "tokens": 150,
        "cost_usd": 0.75,
        "unpriced_events": 0,
    }
    assert by_day[yesterday.date().isoformat()] == {
        "date": yesterday.date().isoformat(),
        "events": 1,
        "tokens": 30,
        "cost_usd": 0.0,
        "unpriced_events": 1,
    }
    assert len(body["by_day"]) == 30

    assert body["by_model"] == [
        {
            "model": "gpt-x",
            "events": 2,
            "tokens": 150,
            "cost_usd": 0.75,
            "unpriced_events": 0,
        },
        {
            "model": "gpt-y",
            "events": 1,
            "tokens": 30,
            "cost_usd": 0.0,
            "unpriced_events": 1,
        },
    ]

    accounts = {row["account_id"]: row for row in body["top_accounts"]}
    assert accounts[alice["account_id"]] == {
        "account_id": alice["account_id"],
        "email": "alice@example.com",
        "events": 2,
        "tokens": 150,
        "cost_usd": 0.75,
    }
    assert accounts[bob["account_id"]]["email"] == "bob@example.com"
    assert accounts[bob["account_id"]]["cost_usd"] == 0.0

    # days=1:只看今天(含今天 00:00)
    one_day = _summary(client, root, days=1)
    assert one_day["since"] == today.strftime("%Y-%m-%dT00:00:00Z")
    assert len(one_day["by_day"]) == 1
    assert one_day["totals"]["events"] == 2
    assert one_day["totals"]["tokens"] == 150

    # days=90:40 天前的事件回到窗口内
    long_window = _summary(client, root, days=90)
    assert long_window["days"] == 90
    assert long_window["totals"]["events"] == 4
    assert long_window["totals"]["tokens"] == 1179
    assert long_window["totals"]["cost_usd"] == 10.74


def test_cost_summary_truncates_top_models_and_accounts(
    client: TestClient, tmp_path: Path
) -> None:
    today = _start_of_today()
    owner = _register(client, "owner@example.com")
    for index in range(1, 23):
        _seed_usage(
            tmp_path,
            account_id=owner["account_id"],
            created_at=today + timedelta(hours=1, minutes=index),
            model=f"model-{index:02d}",
            tokens=index,
            cost=index * 0.01,
        )

    accounts = [_register(client, f"user{index}@example.com") for index in range(11)]
    for index, account in enumerate(accounts, start=1):
        _seed_usage(
            tmp_path,
            account_id=account["account_id"],
            created_at=today + timedelta(hours=2, minutes=index),
            model="gpt-account",
            tokens=100 * index,
            cost=index * 0.1,
        )
    # 已删/查不到的账号:能进 topN,email 回 null
    _seed_usage(
        tmp_path,
        account_id=GHOST_ID,
        created_at=today + timedelta(hours=3),
        model="gpt-ghost",
        tokens=5,
        cost=5.0,
    )

    root = _admin_headers(client)
    body = _summary(client, root)

    by_model = body["by_model"]
    assert len(by_model) == 20
    costs = [row["cost_usd"] for row in by_model]
    assert costs == sorted(costs, reverse=True)
    assert [row["model"] for row in by_model[:3]] == [
        "gpt-account",
        "gpt-ghost",
        "model-22",
    ]
    # 22 个 model-XX 里只能留下最贵的 18 个(另有 2 个更大的分组)
    assert by_model[-1]["model"] == "model-05"
    assert "model-04" not in {row["model"] for row in by_model}

    top = body["top_accounts"]
    assert len(top) == 10
    assert top[0] == {
        "account_id": GHOST_ID,
        "email": None,
        "events": 1,
        "tokens": 5,
        "cost_usd": 5.0,
    }
    # owner 的 22 条 model-XX 合计 0.01+…+0.22 = 2.53,排第二
    assert top[1]["account_id"] == owner["account_id"]
    assert top[1]["email"] == "owner@example.com"
    assert top[1]["cost_usd"] == pytest.approx(2.53)
    assert [row["account_id"] for row in top[2:5]] == [
        accounts[10]["account_id"],
        accounts[9]["account_id"],
        accounts[8]["account_id"],
    ]
    assert top[2]["email"] == "user10@example.com"
    assert top[-1]["account_id"] == accounts[3]["account_id"]  # user4: 10 个里最便宜的


def test_cost_summary_rejects_out_of_range_days(client: TestClient) -> None:
    root = _admin_headers(client)
    for days in (0, 91, -1):
        response = client.get(
            "/v1/admin/cost/summary", params={"days": days}, headers=root
        )
        assert response.status_code == 422
    assert (
        client.get(
            "/v1/admin/cost/summary", params={"days": "abc"}, headers=root
        ).status_code
        == 422
    )


def test_cost_summary_read_is_not_audited(client: TestClient, tmp_path: Path) -> None:
    root = _admin_headers(client)
    alice = _register(client, "alice@example.com")
    _seed_usage(
        tmp_path,
        account_id=alice["account_id"],
        created_at=_start_of_today(),
        cost=0.5,
    )

    baseline = client.get("/v1/admin/audit-logs", headers=root).json()["total"]
    _summary(client, root, days=7)
    after = client.get("/v1/admin/audit-logs", headers=root).json()
    assert after["total"] == baseline
