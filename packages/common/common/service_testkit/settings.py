"""测试用 Settings 构造:自足、零网络。

两道防线缺一不可(AGENTS.md「测试编排」):

1. _env_file=None:不读服务目录的 .env(开发机配置可能开着 LLM_ENABLED 等);
2. 显式钉住会连外部/拉起外部进程的开关:Nx 会把项目 .env 注入任务环境,
   而环境变量优先级高于 _env_file=None——只有显式入参是最高优先级。

只钉 Settings 里真实声明过的字段(用 model_fields 过滤),所以同一工厂
可以服务"有 DB / 无 DB""有开关 / 无开关"的不同服务。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

#: 会连外部服务或拉起外部进程的布尔开关;Settings 声明了才钉 False。
NETWORK_SWITCHES: tuple[str, ...] = (
    "llm_enabled",
    "tools_enabled",
    "outbox_enabled",
    "knowledge_ingest_enabled",
    "workspace_enabled",
    "sandbox_enabled",
    "mcp_stdio_enabled",
    "jobs_enabled",
)


def sqlite_url(tmp_path: Path, name: str = "test.db") -> str:
    """每用例独立 sqlite 文件库的 DSN(临时目录由 pytest tmp_path 提供)。"""
    return f"sqlite+aiosqlite:///{tmp_path}/{name}"


def build_settings(
    settings_cls: type[Any],
    *,
    tmp_path: Path | None = None,
    name: str = "test.db",
    zero_network: bool = True,
    **overrides: Any,
) -> Any:
    """构造一份测试配置:env=dev、临时 sqlite、不读 .env、外部开关默认关。

    settings_cls 没有的字段(如无 DB 服务的 database_url)自动跳过;
    overrides 优先级最高,测试要开某能力时显式传入即可。
    """
    fields = set(settings_cls.model_fields)
    values: dict[str, Any] = {"_env_file": None, "env": "dev"}
    if tmp_path is not None and "database_url" in fields:
        values["database_url"] = sqlite_url(tmp_path, name)
    if "auto_create_tables" in fields:
        values["auto_create_tables"] = True
    if zero_network:
        values.update(
            {switch: False for switch in NETWORK_SWITCHES if switch in fields}
        )
    values.update(overrides)
    return settings_cls(**values)


def settings_factory(
    settings_cls: type[Any],
    *,
    zero_network: bool = True,
    defaults: Mapping[str, Any] | None = None,
) -> Callable[..., Any]:
    """生成 conftest 用的 _settings(tmp_path, name="test.db", **overrides) 工厂。

    defaults 是服务级固定覆盖(如 workspace 的 artifact_root);value 可以是
    callable,调用时传入 tmp_path——用于"路径依赖临时目录"的字段。
    保留这个位置参数签名,是因为多个服务的测试会直接 from tests.conftest
    import _settings 复用。
    """
    fixed = dict(defaults or {})

    def _settings(tmp_path: Path, name: str = "test.db", **overrides: Any) -> Any:
        values = {
            key: (value(tmp_path) if callable(value) else value)
            for key, value in fixed.items()
        }
        values.update(overrides)
        return build_settings(
            settings_cls,
            tmp_path=tmp_path,
            name=name,
            zero_network=zero_network,
            **values,
        )

    return _settings
