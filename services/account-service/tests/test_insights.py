"""用量聚合与配额快照(M0-11 本地版)。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tests.conftest import ACCOUNT, ACCOUNT_ID
from tests.insights_support import INTERNAL, INTERNAL_OTHER, _client

# ── 用量汇总与上报 ─────────────────────────────────────────────────────


def test_usage_summary_empty_and_recorded(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        empty = client.get("/v1/usage/summary", headers=ACCOUNT)
        assert empty.status_code == 200
        assert empty.json() == {
            "today_tokens": 0,
            "month_tokens": 0,
            "today_events": 0,
            "month_events": 0,
            "today_cost_usd": 0.0,
            "month_cost_usd": 0.0,
            "unpriced_events": 0,
            "by_model": [],
        }

        recorded = client.post(
            "/v1/usage/events",
            json={
                "run_id": "r1",
                "model": "deepseek/chat",
                "prompt_tokens": 10,
                "completion_tokens": 5,
            },
            headers=INTERNAL,
        )
        assert recorded.status_code == 201

        summary = client.get("/v1/usage/summary", headers=ACCOUNT).json()
        assert summary["today_tokens"] == 15
        assert summary["month_tokens"] == 15
        assert summary["today_events"] == 1
        assert summary["month_events"] == 1
        assert summary["today_cost_usd"] == 0.0
        assert summary["month_cost_usd"] == 0.0
        # 这条上报没带 cost_usd → 未计价,必须计数而不是冒充 0 元
        assert summary["unpriced_events"] == 1
        assert summary["by_model"] == [
            {
                "model": "deepseek/chat",
                "tokens": 15,
                "events": 1,
                "cost_usd": 0.0,
                "unpriced_events": 1,
            }
        ]


def test_usage_summary_aggregates_cost_and_billing_source(tmp_path: Path) -> None:
    """P0-1:金额按模型聚合,平台/BYOK 分别记账;未计价与 0 元不混淆。"""
    with _client(tmp_path) as client:
        for body in (
            {
                "run_id": "r1",
                "model": "deepseek/chat",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "cost_usd": 0.0012,
                "billing_source": "platform",
            },
            {
                "run_id": "r1",
                "model": "deepseek/chat",
                "prompt_tokens": 20,
                "completion_tokens": 10,
                "cost_usd": 0.0034,
                "billing_source": "platform",
            },
            {
                "run_id": "r2",
                "model": "openai/custom-model",
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "billing_source": "byok",
            },
        ):
            resp = client.post("/v1/usage/events", json=body, headers=INTERNAL)
            assert resp.status_code == 201

        assert (
            client.post(
                "/v1/usage/events",
                json={"model": "m", "cost_usd": -1},
                headers=INTERNAL,
            ).status_code
            == 422
        )

        summary = client.get("/v1/usage/summary", headers=ACCOUNT).json()

    assert summary["month_tokens"] == 47
    assert summary["month_cost_usd"] == pytest.approx(0.0046)
    assert summary["today_cost_usd"] == pytest.approx(0.0046)
    assert summary["unpriced_events"] == 1
    by_model = {entry["model"]: entry for entry in summary["by_model"]}
    assert by_model["deepseek/chat"] == {
        "model": "deepseek/chat",
        "tokens": 45,
        "events": 2,
        "cost_usd": pytest.approx(0.0046),
        "unpriced_events": 0,
    }
    assert by_model["openai/custom-model"] == {
        "model": "openai/custom-model",
        "tokens": 2,
        "events": 1,
        "cost_usd": 0.0,
        "unpriced_events": 1,
    }


def test_usage_event_quantizes_cost_to_stored_precision(tmp_path: Path) -> None:
    """网关浮点尾数(0.000240804)按 Numeric(12,6) 收敛,不能判 422。

    422 会让 outbox 把整条用量事件重试到 failed,已经花掉的 token 永久丢账;
    列本身就是 6 位小数,取整只改表示精度、不改金额口径。
    """
    with _client(tmp_path) as client:
        resp = client.post(
            "/v1/usage/events",
            json={"model": "m", "cost_usd": 0.000240804},
            headers=INTERNAL,
        )
        assert resp.status_code == 201
        summary = client.get("/v1/usage/summary", headers=ACCOUNT).json()
    assert summary["month_cost_usd"] == pytest.approx(0.000241)
    assert summary["unpriced_events"] == 0


def test_usage_event_id_is_idempotent(tmp_path: Path) -> None:
    """P0-3:outbox 至少一次投递 → 同一 event_id 只落一条、只计费一次。"""
    body = {
        "event_id": "b" * 32 + ":0",
        "run_id": "r1",
        "model": "deepseek/chat",
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "cost_usd": 0.0012,
    }
    with _client(tmp_path) as client:
        first = client.post("/v1/usage/events", json=body, headers=INTERNAL)
        second = client.post("/v1/usage/events", json=body, headers=INTERNAL)
        assert first.status_code == 201
        assert second.status_code == 201
        # 幂等命中返回已落库那条的 id,不新增行
        assert second.json()["id"] == first.json()["id"]
        # 账号隔离:另一个账号可以有自己的同 event_id
        other = client.post("/v1/usage/events", json=body, headers=INTERNAL_OTHER)
        assert other.status_code == 201
        assert other.json()["id"] != first.json()["id"]

        summary = client.get("/v1/usage/summary", headers=ACCOUNT).json()
    assert summary["month_events"] == 1
    assert summary["month_tokens"] == 15
    assert summary["month_cost_usd"] == pytest.approx(0.0012)


def test_usage_event_id_optional_and_bounded(tmp_path: Path) -> None:
    """旧直连调用不传 event_id 仍然能落库(空值不参与唯一约束)。"""
    with _client(tmp_path) as client:
        body = {"model": "m", "prompt_tokens": 1, "completion_tokens": 1}
        assert (
            client.post("/v1/usage/events", json=body, headers=INTERNAL).status_code
            == 201
        )
        assert (
            client.post("/v1/usage/events", json=body, headers=INTERNAL).status_code
            == 201
        )
        summary = client.get("/v1/usage/summary", headers=ACCOUNT).json()
        assert summary["month_events"] == 2

        too_long = client.post(
            "/v1/usage/events",
            json={**body, "event_id": "x" * 65},
            headers=INTERNAL,
        )
        assert too_long.status_code == 422


def test_usage_internal_secret_required(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(
            "/v1/usage/events",
            json={"model": "m", "prompt_tokens": 1, "completion_tokens": 1},
            headers=ACCOUNT,
        )
    assert response.status_code == 401
    assert response.json()["code"] == "auth.internal_forbidden"


# ── 配额快照 ───────────────────────────────────────────────────────────


def test_usage_quota_snapshot(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        # 账号不存在 / quota 为空:三个维度都是未限制(fail-open),已用量 0
        empty = client.get(
            "/v1/usage/quota",
            headers=INTERNAL,
        )
        assert empty.status_code == 200
        assert empty.json() == {
            "monthly_tokens": None,
            "max_concurrent_runs": None,
            "used_tokens": 0,
        }

        # 配额落在账号行(users.quota),由服务端维护,不再有组织端点可改
        with sqlite3.connect(tmp_path / "test.db") as conn:
            conn.execute(
                "INSERT INTO users (id, subject, email, display_name, status, quota)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    ACCOUNT_ID,
                    "acc-1@example.com",
                    "acc-1@example.com",
                    "acc-1",
                    "active",
                    json.dumps({"monthly_tokens": 100, "max_concurrent_runs": 2}),
                ),
            )
            conn.commit()

        client.post(
            "/v1/usage/events",
            json={"model": "m", "prompt_tokens": 30, "completion_tokens": 10},
            headers=INTERNAL,
        )
        snapshot = client.get(
            "/v1/usage/quota",
            headers=INTERNAL,
        )
    assert snapshot.status_code == 200
    assert snapshot.json() == {
        "monthly_tokens": 100,
        "max_concurrent_runs": 2,
        "used_tokens": 40,
    }


def test_usage_quota_requires_internal_secret(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/v1/usage/quota", headers=ACCOUNT)
    assert response.status_code == 401
    assert response.json()["code"] == "auth.internal_forbidden"


def test_usage_quota_self_read_with_session_auth(tmp_path: Path) -> None:
    """P0-2:前端配额条走会话鉴权读自己的快照,内部端点仍是共享密钥。"""
    with _client(tmp_path) as client:
        empty = client.get("/v1/usage/quota/me", headers=ACCOUNT)
        assert empty.status_code == 200
        assert empty.json() == {
            "monthly_tokens": None,
            "max_concurrent_runs": None,
            "used_tokens": 0,
        }

        with sqlite3.connect(tmp_path / "test.db") as conn:
            conn.execute(
                "INSERT INTO users (id, subject, email, display_name, status, quota)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    ACCOUNT_ID,
                    "acc-1@example.com",
                    "acc-1@example.com",
                    "acc-1",
                    "active",
                    json.dumps({"monthly_tokens": 100, "max_concurrent_runs": 2}),
                ),
            )
            conn.commit()

        client.post(
            "/v1/usage/events",
            json={"model": "m", "prompt_tokens": 30, "completion_tokens": 10},
            headers=INTERNAL,
        )

        snapshot = client.get("/v1/usage/quota/me", headers=ACCOUNT)
        internal = client.get("/v1/usage/quota", headers=INTERNAL)

    assert snapshot.status_code == 200
    assert snapshot.json() == internal.json()
    assert snapshot.json() == {
        "monthly_tokens": 100,
        "max_concurrent_runs": 2,
        "used_tokens": 40,
    }


def test_usage_quota_self_read_requires_session(tmp_path: Path) -> None:
    """没有会话/内部密钥就不给读配额(两端点都拒绝)。"""
    with _client(tmp_path) as client:
        anonymous = client.get("/v1/usage/quota/me")
        internal_only = client.get("/v1/usage/quota/me", headers=INTERNAL)
    assert anonymous.status_code == 401
    assert internal_only.status_code == 401
