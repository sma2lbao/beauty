"""维护命令:常驻清理循环与 COS 生命周期策略（不连库/不发网络）。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from eval_service import maintenance
from eval_service.core.config import Settings
from eval_service.services.lifecycle import apply_lifecycle, build_lifecycle_policy


def test_purge_loop_once_deletes_and_exits(monkeypatch: Any, capsys: Any) -> None:
    calls: list[tuple[int, int, bool]] = []

    async def fake_purge(
        settings: Settings, *, days: int, limit: int, dry_run: bool
    ) -> int:
        calls.append((days, limit, dry_run))
        return 3

    monkeypatch.setattr(maintenance, "_purge", fake_purge)
    code = asyncio.run(
        maintenance._purge_loop(Settings(), days=7, limit=50, interval=3600, once=True)
    )
    assert code == 0
    assert calls == [(7, 50, False)]
    assert "删除 3 个超过 7 天" in capsys.readouterr().out


def test_purge_loop_once_returns_failure(monkeypatch: Any) -> None:
    async def boom(settings: Settings, *, days: int, limit: int, dry_run: bool) -> int:
        raise RuntimeError("数据库暂不可用")

    monkeypatch.setattr(maintenance, "_purge", boom)
    code = asyncio.run(
        maintenance._purge_loop(Settings(), days=7, limit=50, interval=3600, once=True)
    )
    assert code == 1


def test_lifecycle_policy_shape() -> None:
    policy = build_lifecycle_policy(180, "eval-runs/")
    rule = policy["Rule"][0]
    assert rule["ID"] == "eval-report-expiry"
    assert rule["Filter"] == {"Prefix": "eval-runs/"}
    assert rule["Expiration"] == {"Days": 180}
    assert rule["AbortIncompleteMultipartUpload"] == {"Days": 1}
    with pytest.raises(ValueError):
        build_lifecycle_policy(0, "eval-runs/")
    with pytest.raises(ValueError):
        build_lifecycle_policy(30, " ")


def test_lifecycle_dry_run_does_not_touch_cos() -> None:
    settings = Settings()
    policy = asyncio.run(
        apply_lifecycle(
            settings,
            days=30,
            prefix="eval-runs/",
            apply=False,
            client=None,
        )
    )
    assert policy["Rule"][0]["Expiration"]["Days"] == 30


def test_lifecycle_apply_writes_bucket_rule() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        def put_bucket_lifecycle(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    settings = Settings(artifact_store="cos", artifact_cos_bucket="eval-bucket")
    client = FakeClient()
    policy = asyncio.run(
        apply_lifecycle(
            settings,
            days=180,
            prefix="eval-runs/",
            apply=True,
            client=client,
        )
    )
    assert client.kwargs["Bucket"] == "eval-bucket"
    assert client.kwargs["LifecycleConfiguration"] == policy


def test_lifecycle_apply_requires_cos_backend() -> None:
    settings = Settings(artifact_store="filesystem")
    with pytest.raises(RuntimeError, match="EVAL__ARTIFACT_STORE=cos"):
        asyncio.run(
            apply_lifecycle(
                settings,
                days=30,
                prefix="eval-runs/",
                apply=True,
            )
        )
