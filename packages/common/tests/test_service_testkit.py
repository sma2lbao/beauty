"""service_testkit 自测:配置两道防线 + TestClient lifespan 装配。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from pydantic_settings import BaseSettings, SettingsConfigDict

from common.service_testkit import (
    NETWORK_SWITCHES,
    app_client,
    build_settings,
    client_fixture,
    settings_factory,
    sqlite_url,
)


class _Settings(BaseSettings):
    """最小的"带 DB + 外部开关"服务配置替身,避免依赖 database 包。"""

    model_config = SettingsConfigDict(env_prefix="TESTKIT__", extra="ignore")

    env: str = "prod"
    database_url: str = "postgresql+asyncpg://prod/db"
    auto_create_tables: bool = False
    artifact_root: str = "/prod/objects"
    llm_enabled: bool = True
    tools_enabled: bool = True
    outbox_enabled: bool = True
    # 不在 NETWORK_SWITCHES 里的普通开关:测试工厂不应改动它
    feature_flag: bool = True


class _NoDbSettings(BaseSettings):
    """没有 DB 字段的服务配置替身(如 llm-gateway / bff)。"""

    model_config = SettingsConfigDict(env_prefix="TESTKIT_NODB__", extra="ignore")

    env: str = "prod"
    llm_enabled: bool = True


def test_build_settings_pins_env_and_temp_sqlite(tmp_path: Path) -> None:
    settings = build_settings(_Settings, tmp_path=tmp_path)

    assert settings.env == "dev"
    assert settings.database_url == sqlite_url(tmp_path)
    assert settings.auto_create_tables is True


def test_build_settings_disables_all_declared_network_switches(
    tmp_path: Path,
) -> None:
    settings = build_settings(_Settings, tmp_path=tmp_path)

    # 声明过的开关全部被钉成 False;未声明的字段在拼装时已被跳过
    enabled = [name for name in NETWORK_SWITCHES if getattr(settings, name, False)]
    assert enabled == []
    assert settings.llm_enabled is False
    assert settings.tools_enabled is False
    assert settings.outbox_enabled is False
    assert settings.feature_flag is True


def test_build_settings_skips_fields_the_service_does_not_declare(
    tmp_path: Path,
) -> None:
    settings = build_settings(_NoDbSettings, tmp_path=tmp_path)

    assert settings.env == "dev"
    assert settings.llm_enabled is False
    dumped = settings.model_dump()
    assert "database_url" not in dumped
    assert "auto_create_tables" not in dumped


def test_build_settings_overrides_win(tmp_path: Path) -> None:
    settings = build_settings(
        _Settings,
        tmp_path=tmp_path,
        name="custom.db",
        env="ci",
        llm_enabled=True,
        database_url="sqlite+aiosqlite:///:memory:",
    )

    assert settings.env == "ci"
    assert settings.llm_enabled is True
    assert settings.database_url == "sqlite+aiosqlite:///:memory:"


def test_build_settings_zero_network_false_keeps_switches(tmp_path: Path) -> None:
    settings = build_settings(_Settings, tmp_path=tmp_path, zero_network=False)

    assert settings.llm_enabled is True
    assert settings.tools_enabled is True


def test_settings_factory_supports_tmp_path_defaults(tmp_path: Path) -> None:
    make_settings = settings_factory(
        _Settings,
        defaults={"artifact_root": lambda root: str(root / "objects")},
    )

    settings = make_settings(tmp_path)

    assert settings.artifact_root == str(tmp_path / "objects")
    assert settings.database_url == sqlite_url(tmp_path)


def test_settings_factory_passes_name_and_overrides(tmp_path: Path) -> None:
    make_settings = settings_factory(_Settings)

    settings = make_settings(tmp_path, "custom.db", llm_enabled=True)

    assert settings.database_url == sqlite_url(tmp_path, "custom.db")
    assert settings.llm_enabled is True


def test_app_client_enters_and_exits_lifespan() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        events.append("startup")
        yield
        events.append("shutdown")

    app = FastAPI(lifespan=lifespan)

    @app.get("/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    with app_client(app) as test_client:
        assert events == ["startup"]
        response = test_client.get("/ping")
        assert response.status_code == 200
        assert response.json() == {"ok": True}

    assert events == ["startup", "shutdown"]


def test_client_fixture_registers_named_fixture() -> None:
    def app_factory(settings: object, **kwargs: object) -> FastAPI:
        return FastAPI()

    fixture = client_fixture(
        app_factory,
        settings_factory(_Settings),
        name="api_client",
    )

    assert fixture.name == "api_client"
    assert type(fixture).__name__ == "FixtureFunctionDefinition"
