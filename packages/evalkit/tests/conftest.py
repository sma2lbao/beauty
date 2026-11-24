"""evalkit 测试公共夹具。"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pytest


def write_dataset(
    root: Path,
    *,
    name: str = "demo",
    cases: Iterable[Mapping[str, Any]] | None = None,
    adapter: str = "adapter_fixture:RecordingAdapter",
    version: int = 1,
) -> Path:
    dataset = root / "dataset"
    dataset.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": name,
        "version": version,
        "adapter": adapter,
    }
    (dataset / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    rows = list(cases or [])
    (dataset / "cases.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return dataset


def simple_case(
    case_id: str = "demo/basic",
    *,
    output: str = "ok",
    fields: Mapping[str, Any] | None = None,
    group: str = "default",
    tags: Iterable[str] = (),
    graders: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "group": group,
        "tags": list(tags),
        "input": {
            "output": output,
            "fields": dict(fields or {}),
        },
        "graders": list(graders or [{"type": "equals", "value": output}]),
    }


@pytest.fixture()
def dataset_dir(tmp_path: Path) -> Path:
    return write_dataset(
        tmp_path,
        cases=[
            simple_case("demo/pass", output="ok"),
            simple_case(
                "demo/fail",
                output="bad",
                graders=[{"type": "equals", "value": "ok"}],
            ),
        ],
    )
