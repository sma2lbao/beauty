"""CLI 契约:退出码 0/1/2/3、报告落盘、基线更新与漂移。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import simple_case, write_dataset

from evalkit.cli import (
    EXIT_DRIFT,
    EXIT_ERROR,
    EXIT_FAIL,
    EXIT_OK,
    main,
)


def write_config(
    tmp_path: Path,
    *,
    fail_under: float | None = None,
    extra_suites: str = "",
) -> Path:
    lines = [
        "[platform]",
        'root = ".."',
        "",
        "[[suite]]",
        'name = "demo"',
        'dataset = "dataset"',
        'adapter = "adapter_fixture:RecordingAdapter"',
        'baseline = "baselines/demo.json"',
    ]
    if fail_under is not None:
        lines.append(f"fail_under = {fail_under}")
    path = tmp_path / "evals" / "evalkit.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n" + extra_suites, encoding="utf-8")
    return path


def setup(
    tmp_path: Path,
    cases=None,
    *,
    fail_under: float | None = None,
) -> Path:
    write_dataset(tmp_path, cases=cases or [simple_case()])
    return write_config(tmp_path, fail_under=fail_under)


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == EXIT_OK
    assert "evalkit 0.1.0" in capsys.readouterr().out


def test_validate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = setup(tmp_path)
    assert main(["validate", "-c", str(path)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "OK demo: 1 条用例" in out
    assert "1 个套件,1 条用例" in out


def test_list_with_cases(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = setup(
        tmp_path,
        [simple_case("demo/a", tags=["fast"]), simple_case("demo/b")],
    )
    assert main(["list", "-c", str(path), "--cases"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "demo: dataset=demo v1" in out
    assert "demo/a tags=fast" in out
    assert "demo/b" in out


def test_run_writes_all_reports(tmp_path: Path) -> None:
    path = setup(tmp_path, [simple_case(fields={"tokens": 10})])
    json_path = tmp_path / "out" / "report.json"
    assert (
        main(
            [
                "run",
                "-c",
                str(path),
                "--no-baseline",
                "--quiet",
                "--json",
                str(json_path),
            ]
        )
        == EXIT_OK
    )
    assert json_path.is_file()
    assert (tmp_path / "out" / "report.junit.xml").is_file()
    assert (tmp_path / "out" / "report.md").is_file()
    assert (tmp_path / "out" / "report.html").is_file()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["totals"]["tokens"] == 10


def test_run_console_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = setup(tmp_path)
    args = [
        "run",
        "-c",
        str(path),
        "--no-baseline",
        "--json",
        str(tmp_path / "r.json"),
    ]
    assert main(args) == EXIT_OK
    out = capsys.readouterr().out
    assert "套件" in out
    assert "JSON:" in out


def test_run_failure_returns_fail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = setup(
        tmp_path,
        [
            simple_case(
                "demo/bad",
                output="bad",
                graders=[{"type": "equals", "value": "ok"}],
            )
        ],
    )
    code = main(
        [
            "run",
            "-c",
            str(path),
            "--no-baseline",
            "--quiet",
            "--json",
            str(tmp_path / "r.json"),
        ]
    )
    assert code == EXIT_FAIL
    assert "失败:demo/demo/bad" in capsys.readouterr().err


def test_run_fail_under(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = setup(
        tmp_path,
        [
            simple_case(
                "demo/bad",
                output="bad",
                graders=[{"type": "equals", "value": "ok"}],
            )
        ],
        fail_under=1.0,
    )
    code = main(
        [
            "run",
            "-c",
            str(path),
            "--no-baseline",
            "--quiet",
            "--json",
            str(tmp_path / "r.json"),
        ]
    )
    assert code == EXIT_FAIL
    assert "通过率" in capsys.readouterr().err


def test_run_global_fail_under_override(tmp_path: Path) -> None:
    path = setup(tmp_path)
    assert (
        main(
            [
                "run",
                "-c",
                str(path),
                "--no-baseline",
                "--quiet",
                "--fail-under",
                "1.0",
                "--json",
                str(tmp_path / "r.json"),
            ]
        )
        == EXIT_OK
    )


def test_run_token_limit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = setup(tmp_path, [simple_case(fields={"tokens": 100})])
    code = main(
        [
            "run",
            "-c",
            str(path),
            "--no-baseline",
            "--quiet",
            "--max-total-tokens",
            "50",
            "--json",
            str(tmp_path / "r.json"),
        ]
    )
    assert code == EXIT_FAIL
    assert "总 token 100 超过上限 50" in capsys.readouterr().err


def test_update_baseline_and_clean_rerun(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = setup(tmp_path)
    assert (
        main(
            [
                "run",
                "-c",
                str(path),
                "--update-baseline",
                "--quiet",
                "--json",
                str(tmp_path / "r.json"),
            ]
        )
        == EXIT_OK
    )
    assert (tmp_path / "baselines" / "demo.json").is_file()
    assert "基线已更新" in capsys.readouterr().out
    assert (
        main(
            [
                "run",
                "-c",
                str(path),
                "--quiet",
                "--json",
                str(tmp_path / "r2.json"),
            ]
        )
        == EXIT_OK
    )


def test_update_baseline_refuses_failures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = setup(
        tmp_path,
        [
            simple_case(
                "demo/bad",
                output="bad",
                graders=[{"type": "equals", "value": "ok"}],
            )
        ],
    )
    code = main(
        [
            "run",
            "-c",
            str(path),
            "--update-baseline",
            "--quiet",
            "--json",
            str(tmp_path / "r.json"),
        ]
    )
    assert code == EXIT_FAIL
    assert "拒绝更新基线" in capsys.readouterr().err
    assert not (tmp_path / "baselines" / "demo.json").exists()


def test_drift_returns_two(tmp_path: Path) -> None:
    path = setup(tmp_path, [simple_case(fields={"tokens": 10})])
    assert (
        main(
            [
                "run",
                "-c",
                str(path),
                "--update-baseline",
                "--quiet",
                "--json",
                str(tmp_path / "r.json"),
            ]
        )
        == EXIT_OK
    )
    write_dataset(tmp_path, cases=[simple_case(fields={"tokens": 100})])
    assert (
        main(
            [
                "run",
                "-c",
                str(path),
                "--quiet",
                "--json",
                str(tmp_path / "r2.json"),
            ]
        )
        == EXIT_DRIFT
    )


def test_missing_baseline_is_hint_not_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = setup(tmp_path)
    code = main(
        [
            "run",
            "-c",
            str(path),
            "--quiet",
            "--json",
            str(tmp_path / "r.json"),
        ]
    )
    assert code == EXIT_OK
    assert "基线不存在" in capsys.readouterr().err


def test_single_suite_filter(tmp_path: Path) -> None:
    path = setup(tmp_path)
    assert (
        main(
            [
                "run",
                "-c",
                str(path),
                "--suite",
                "demo",
                "--no-baseline",
                "--quiet",
                "--json",
                str(tmp_path / "r.json"),
            ]
        )
        == EXIT_OK
    )


def test_tag_filter(tmp_path: Path) -> None:
    path = setup(
        tmp_path,
        [simple_case("demo/a", tags=["fast"]), simple_case("demo/b")],
    )
    assert (
        main(
            [
                "run",
                "-c",
                str(path),
                "--tag",
                "fast",
                "--no-baseline",
                "--quiet",
                "--json",
                str(tmp_path / "r.json"),
            ]
        )
        == EXIT_OK
    )
    payload = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert payload["totals"]["cases"] == 1


def test_unknown_suite_is_config_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = setup(tmp_path)
    code = main(
        [
            "run",
            "-c",
            str(path),
            "--suite",
            "nope",
            "--quiet",
            "--json",
            str(tmp_path / "r.json"),
        ]
    )
    assert code == EXIT_ERROR
    assert "错误:" in capsys.readouterr().err


def test_baseline_override_requires_single_suite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    extra = (
        "[[suite]]\n"
        'name = "other"\n'
        'dataset = "other"\n'
        'baseline = "baselines/other.json"\n'
    )
    path = write_config(tmp_path, extra_suites=extra)
    write_dataset(tmp_path, cases=[simple_case()])
    code = main(
        [
            "run",
            "-c",
            str(path),
            "--suite",
            "demo",
            "--suite",
            "other",
            "--baseline",
            str(tmp_path / "x.json"),
        ]
    )
    assert code == EXIT_ERROR
    assert "--baseline" in capsys.readouterr().err


def test_bad_fail_under_is_error(tmp_path: Path) -> None:
    path = setup(tmp_path)
    code = main(["run", "-c", str(path), "--fail-under", "2"])
    assert code == EXIT_ERROR
