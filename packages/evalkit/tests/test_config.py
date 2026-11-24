"""平台配置加载:路径解析、未知字段与门禁阈值校验。"""

from __future__ import annotations

from pathlib import Path

import pytest

from evalkit.config import find_config, load_config, select_suites
from evalkit.errors import ConfigError


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "evals" / "evalkit.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def valid_config(tmp_path: Path, extra: str = "") -> Path:
    return write_config(
        tmp_path,
        f"""
[platform]
root = ".."
report_dir = "reports"
baseline_dir = "baselines"

[[suite]]
name = "smoke"
dataset = "dataset"
adapter = "adapter_fixture:RecordingAdapter"
fail_under = 1.0
tags = ["blocker"]
options = {{ variant = "bm25" }}
{extra}
""",
    )


def test_load_valid_and_defaults(tmp_path: Path) -> None:
    path = valid_config(tmp_path)
    config = load_config(path)
    assert config.root == tmp_path
    assert config.report_dir == tmp_path / "reports"
    assert config.baseline_dir == tmp_path / "baselines"
    assert config.path == path
    assert len(config.suites) == 1
    suite = config.suites[0]
    assert suite.name == "smoke"
    assert suite.dataset == tmp_path / "dataset"
    assert suite.baseline == tmp_path / "baselines" / "smoke.json"
    assert suite.adapter == "adapter_fixture:RecordingAdapter"
    assert suite.fail_under == 1.0
    assert suite.tags == ("blocker",)
    assert suite.options == {"variant": "bm25"}


def test_explicit_baseline_and_defaults(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
[platform]
root = ".."

[[suite]]
name = "smoke"
dataset = "dataset"
baseline = "custom/base.json"
""",
    )
    config = load_config(path)
    suite = config.suites[0]
    assert suite.baseline == tmp_path / "custom" / "base.json"
    assert suite.adapter is None
    assert suite.fail_under is None
    assert suite.tags == ()
    assert config.report_dir == tmp_path / "reports" / "eval"


def test_find_config_walks_up(tmp_path: Path) -> None:
    valid_config(tmp_path)
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_config(nested) == tmp_path / "evals" / "evalkit.toml"
    with pytest.raises(ConfigError, match="找不到"):
        find_config(Path("/"))


def test_select_suites(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
[platform]
root = "."

[[suite]]
name = "a"
dataset = "a"
[[suite]]
name = "b"
dataset = "b"
""",
    )
    config = load_config(path)
    assert [s.name for s in select_suites(config, ())] == ["a", "b"]
    assert [s.name for s in select_suites(config, ("b",))] == ["b"]
    with pytest.raises(ConfigError, match="未知套件"):
        select_suites(config, ("c",))


_BASE = "[platform]\nroot='.'\n[[suite]]\nname='a'\ndataset='a'\n"


@pytest.mark.parametrize(
    "text, message",
    [
        (
            "[platform]\nroot = 1\n[[suite]]\nname='a'\ndataset='a'\n",
            "root 必须是字符串",
        ),
        (
            "[platform]\nunknown = 1\n[[suite]]\nname='a'\ndataset='a'\n",
            "未知字段",
        ),
        ("[platform]\n[other]\n", "未知顶层字段"),
        ("[platform]\nroot='.'\n", "至少需要一个"),
        (
            "[platform]\nroot='.'\n[[suite]]\nname=''\ndataset='a'\n",
            "缺少非空 name",
        ),
        ("[platform]\nroot='.'\n[[suite]]\nname='a'\n", "缺少 dataset"),
        (_BASE + "unknown=1\n", "未知字段"),
        (_BASE + "adapter=''\n", "adapter"),
        (_BASE + "baseline=''\n", "baseline"),
        (_BASE + "options=[]\n", "options"),
        (_BASE + "fail_under='x'\n", "fail_under"),
        (_BASE + "fail_under=1.2\n", "0~1"),
        (_BASE + "tags=[1]\n", "tags"),
        (
            _BASE + "[[suite]]\nname='a'\ndataset='b'\n",
            "名称重复",
        ),
    ],
)
def test_invalid_configs(tmp_path: Path, text: str, message: str) -> None:
    path = write_config(tmp_path, text)
    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_platform_not_table(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        "[platform]\nroot = '.'\n[[suite]]\nname='a'\ndataset='a'\n",
    )
    path.write_text("platform = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="必须是 table"):
        load_config(path)


def test_bad_toml_and_missing_file(tmp_path: Path) -> None:
    path = write_config(tmp_path, "[")
    with pytest.raises(ConfigError, match="不是合法 TOML"):
        load_config(path)
    with pytest.raises(ConfigError, match="读取配置失败"):
        load_config(tmp_path / "missing.toml")
