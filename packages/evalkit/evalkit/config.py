"""平台配置 evals/evalkit.toml:套件清单、路径与门禁阈值。

配置里的所有相对路径都相对仓库根(root);root 默认是配置文件所在目录的
上一级,因此 evals/evalkit.toml 无需重复写仓库路径。
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ConfigError

_PLATFORM_KEYS = frozenset({"root", "report_dir", "baseline_dir"})
_SUITE_KEYS = frozenset(
    {"name", "dataset", "adapter", "baseline", "options", "fail_under", "tags"}
)


@dataclass(frozen=True)
class SuiteConfig:
    """一个评测套件的配置。"""

    name: str
    dataset: Path
    baseline: Path
    adapter: str | None = None
    options: Mapping[str, Any] = field(default_factory=dict)
    fail_under: float | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlatformConfig:
    """加载完成的平台配置。"""

    root: Path
    report_dir: Path
    baseline_dir: Path
    suites: tuple[SuiteConfig, ...]
    path: Path


def find_config(start: str | Path | None = None) -> Path:
    """从 cwd 向上找 evals/evalkit.toml。"""
    current = Path(start) if start is not None else Path.cwd()
    current = current.resolve()
    for directory in (current, *current.parents):
        candidate = directory / "evals" / "evalkit.toml"
        if candidate.is_file():
            return candidate
    raise ConfigError("找不到 evals/evalkit.toml;请用 --config 指定")


def load_config(path: str | Path | None = None) -> PlatformConfig:
    config_path = Path(path).resolve() if path else find_config()
    try:
        raw = config_path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"读取配置失败:{config_path}:{exc}") from exc
    try:
        payload = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"配置不是合法 TOML:{config_path}:{exc}") from exc
    if not isinstance(payload, Mapping):
        raise ConfigError(f"{config_path}:顶层必须是 TOML table")

    unknown_top = sorted(set(payload) - {"platform", "suite"})
    if unknown_top:
        raise ConfigError(f"{config_path}:未知顶层字段 {unknown_top}")

    platform = payload.get("platform", {})
    if not isinstance(platform, Mapping):
        raise ConfigError(f"{config_path}:[platform] 必须是 table")
    unknown_platform = sorted(set(platform) - _PLATFORM_KEYS)
    if unknown_platform:
        raise ConfigError(f"{config_path}:[platform] 未知字段 {unknown_platform}")
    root_value = platform.get("root", "..")
    if not isinstance(root_value, str):
        raise ConfigError(f"{config_path}:platform.root 必须是字符串")
    root = _resolve(config_path.parent, root_value).resolve()
    report_dir = _resolve(root, str(platform.get("report_dir", "reports/eval")))
    baseline_dir = _resolve(root, str(platform.get("baseline_dir", "evals/baselines")))

    raw_suites = payload.get("suite", [])
    if not isinstance(raw_suites, list) or not raw_suites:
        raise ConfigError(f"{config_path}:至少需要一个 [[suite]]")
    suites: list[SuiteConfig] = []
    seen: set[str] = set()
    for index, raw_suite in enumerate(raw_suites):
        suite = _parse_suite(
            raw_suite,
            where=f"{config_path}:suite[{index}]",
            root=root,
            baseline_dir=baseline_dir,
        )
        if suite.name in seen:
            raise ConfigError(f"{config_path}:suite 名称重复 {suite.name}")
        seen.add(suite.name)
        suites.append(suite)
    return PlatformConfig(
        root=root,
        report_dir=report_dir,
        baseline_dir=baseline_dir,
        suites=tuple(suites),
        path=config_path,
    )


def select_suites(
    config: PlatformConfig, names: list[str] | tuple[str, ...]
) -> tuple[SuiteConfig, ...]:
    """按名称筛选套件;未指定时返回全部,名称未知直接报错。"""
    if not names:
        return config.suites
    by_name = {suite.name: suite for suite in config.suites}
    missing = [name for name in names if name not in by_name]
    if missing:
        raise ConfigError(f"未知套件 {missing};可用 {sorted(by_name)}")
    return tuple(by_name[name] for name in names)


def _parse_suite(
    payload: Any,
    *,
    where: str,
    root: Path,
    baseline_dir: Path,
) -> SuiteConfig:
    if not isinstance(payload, Mapping):
        raise ConfigError(f"{where}:必须是 table")
    unknown = sorted(set(payload) - _SUITE_KEYS)
    if unknown:
        raise ConfigError(f"{where}:未知字段 {unknown},支持 {sorted(_SUITE_KEYS)}")
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"{where}:缺少非空 name")
    name = name.strip()
    dataset = payload.get("dataset")
    if not isinstance(dataset, str) or not dataset:
        raise ConfigError(f"{where} {name}:缺少 dataset")
    adapter = payload.get("adapter")
    if adapter is not None and (not isinstance(adapter, str) or not adapter):
        raise ConfigError(f"{where} {name}:adapter 必须是非空字符串")
    baseline_value = payload.get("baseline")
    if baseline_value is None:
        baseline = baseline_dir / f"{name}.json"
    elif isinstance(baseline_value, str) and baseline_value:
        baseline = _resolve(root, baseline_value)
    else:
        raise ConfigError(f"{where} {name}:baseline 必须是非空字符串")
    options = payload.get("options", {})
    if not isinstance(options, Mapping):
        raise ConfigError(f"{where} {name}:options 必须是 table")
    fail_under = payload.get("fail_under")
    if fail_under is not None:
        if isinstance(fail_under, bool) or not isinstance(fail_under, (int, float)):
            raise ConfigError(f"{where} {name}:fail_under 必须是 0~1 的数字")
        if not 0 <= float(fail_under) <= 1:
            raise ConfigError(f"{where} {name}:fail_under 必须在 0~1")
        fail_under = float(fail_under)
    tags = payload.get("tags", [])
    if not isinstance(tags, list) or not all(
        isinstance(tag, str) and tag for tag in tags
    ):
        raise ConfigError(f"{where} {name}:tags 必须是非空字符串列表")
    return SuiteConfig(
        name=name,
        dataset=_resolve(root, dataset),
        baseline=baseline,
        adapter=adapter,
        options=dict(options),
        fail_under=fail_under,
        tags=tuple(tags),
    )


def _resolve(root: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else (root / candidate)
