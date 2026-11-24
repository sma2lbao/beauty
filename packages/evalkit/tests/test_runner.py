"""runner:数据集 -> adapter -> grader -> CaseRun 的串联。"""

from __future__ import annotations

from pathlib import Path

import pytest
from adapter_fixture import RecordingAdapter
from conftest import simple_case, write_dataset

from evalkit.config import PlatformConfig, SuiteConfig
from evalkit.errors import ConfigError, RunnerError
from evalkit.runner import CaseRun, SuiteRun, run_suite, run_suites


def suite(tmp_path: Path, dataset: Path) -> SuiteConfig:
    return SuiteConfig(
        name="demo",
        dataset=dataset,
        baseline=tmp_path / "baselines" / "demo.json",
        adapter="adapter_fixture:RecordingAdapter",
    )


def test_run_suite_with_adapter_instance(tmp_path: Path) -> None:
    dataset = write_dataset(
        tmp_path,
        cases=[
            simple_case(
                "demo/pass",
                output="ok",
                fields={"tokens": 5, "metrics": {"recall": 1.0}},
            ),
            simple_case(
                "demo/fail",
                output="bad",
                graders=[{"type": "equals", "value": "ok"}],
            ),
        ],
    )
    adapter = RecordingAdapter()
    run = run_suite(suite(tmp_path, dataset), adapter=adapter)
    assert len(run.cases) == 2
    assert run.cases[0].ok is True
    assert run.cases[0].tokens == 5
    assert run.cases[0].metrics == {"recall": 1.0}
    assert run.cases[1].ok is False
    assert run.cases[1].failures()
    assert run.totals["passed"] == 1
    assert run.totals["failed"] == 1
    assert run.totals["tokens"] == 5
    assert adapter.closed is False


def test_run_suite_owned_adapter_is_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).parent))
    dataset = write_dataset(tmp_path, cases=[simple_case()])
    RecordingAdapter.instances.clear()
    run_suite(suite(tmp_path, dataset))
    assert RecordingAdapter.instances
    assert RecordingAdapter.instances[-1].closed is True


def test_run_suite_execution_error(tmp_path: Path) -> None:
    dataset = write_dataset(
        tmp_path,
        cases=[simple_case("demo/boom") | {"input": {"raise": "boom"}}],
    )
    run = run_suite(suite(tmp_path, dataset), adapter=RecordingAdapter())
    case = run.cases[0]
    assert case.ok is False
    assert "RuntimeError: boom" in (case.error or "")
    assert case.failures()[0].startswith("执行错误")


def test_run_suite_wrong_observation_type(tmp_path: Path) -> None:
    dataset = write_dataset(
        tmp_path,
        cases=[simple_case("demo/wrong") | {"input": {"wrong_type": True}}],
    )
    run = run_suite(suite(tmp_path, dataset), adapter=RecordingAdapter())
    assert "需要 Observation" in (run.cases[0].error or "")


def test_run_suite_filters(tmp_path: Path) -> None:
    dataset = write_dataset(
        tmp_path,
        cases=[
            simple_case("demo/a", tags=["fast"]),
            simple_case("demo/b", tags=["slow"]),
            simple_case("demo/c", tags=["fast"]),
        ],
    )
    config = suite(tmp_path, dataset)
    run = run_suite(config, adapter=RecordingAdapter(), tags=["fast"])
    assert [case.case_id for case in run.cases] == ["demo/a", "demo/c"]
    run = run_suite(config, adapter=RecordingAdapter(), case_ids=["demo/b"])
    assert [case.case_id for case in run.cases] == ["demo/b"]
    run = run_suite(
        config,
        adapter=RecordingAdapter(),
        tags=["fast"],
        case_ids=["demo/c"],
    )
    assert [case.case_id for case in run.cases] == ["demo/c"]


def test_run_suite_unknown_case_and_no_match(tmp_path: Path) -> None:
    dataset = write_dataset(tmp_path, cases=[simple_case("demo/a")])
    config = suite(tmp_path, dataset)
    with pytest.raises(ConfigError, match="没有用例"):
        run_suite(config, adapter=RecordingAdapter(), case_ids=["demo/nope"])
    with pytest.raises(RunnerError, match="没有匹配"):
        run_suite(config, adapter=RecordingAdapter(), tags=["nope"])


def test_case_run_tokens_and_metrics_guards() -> None:
    base = dict(
        suite="s",
        case_id="c",
        group="g",
        tags=(),
        ok=True,
        score=1.0,
        duration_ms=1.0,
        output="",
        error=None,
        outcomes=(),
        fields={},
        events=(),
        tool_calls=(),
    )
    case = CaseRun(**base)
    assert case.tokens == 0
    assert case.metrics == {}
    assert case.failures() == ()
    case = CaseRun(
        **{
            **base,
            "fields": {"tokens": True, "metrics": {"a": True, "b": 2}},
        }
    )
    assert case.tokens == 0
    assert case.metrics == {"b": 2.0}


def test_suite_run_to_dict() -> None:
    case = CaseRun(
        suite="s",
        case_id="c",
        group="g",
        tags=("t",),
        ok=False,
        score=0.5,
        duration_ms=12.345,
        output="out",
        error=None,
        outcomes=(
            {
                "id": "g1",
                "type": "equals",
                "ok": False,
                "required": True,
                "weight": 1.0,
                "message": "bad",
            },
        ),
        fields={"tokens": 3},
        events=("e",),
        tool_calls=("t",),
    )
    run = SuiteRun(
        name="s",
        dataset="d",
        dataset_version=1,
        cases=(case,),
        duration_ms=20.0,
        generated_at="2026-01-01T00:00:00+00:00",
    )
    payload = run.to_dict()
    assert payload["totals"]["score"] == 0.5
    assert payload["cases"][0]["failures"] == ["bad"]
    assert payload["cases"][0]["duration_ms"] == 12.35


def test_run_suites(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dataset = write_dataset(tmp_path, cases=[simple_case()])
    config = PlatformConfig(
        root=tmp_path,
        report_dir=tmp_path / "reports",
        baseline_dir=tmp_path / "baselines",
        suites=(suite(tmp_path, dataset),),
        path=tmp_path / "evals" / "evalkit.toml",
    )
    monkeypatch.setattr(
        "evalkit.runner.load_adapter", lambda target, options: RecordingAdapter()
    )
    runs = run_suites(config)
    assert len(runs) == 1
    assert runs[0].name == "demo"
    with pytest.raises(ConfigError, match="未知套件"):
        run_suites(config, suite_names=("nope",))
