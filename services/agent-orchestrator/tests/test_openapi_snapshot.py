"""OpenAPI 契约快照(R-29):API 层拆分不得改变对外契约。

快照取自 R-29 之前的 HEAD(`create_app().openapi()` 全量 dump),之后任何
路由/参数/响应模型的差异都会在这里暴露——包括"看起来只是挪了文件"的拆分。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_orchestrator.main import create_app

SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "openapi.json"


def _normalized(spec: dict[str, Any]) -> dict[str, Any]:
    """按 JSON 往返归一化:只比较契约内容,不比较 dict 插入顺序。"""
    return json.loads(json.dumps(spec, sort_keys=True, ensure_ascii=False))


def test_openapi_schema_matches_snapshot() -> None:
    expected = _normalized(json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8")))
    actual = _normalized(create_app().openapi())
    assert actual == expected, "OpenAPI 契约发生变化(路径/参数/响应模型)"
