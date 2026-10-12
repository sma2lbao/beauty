import pytest
from pydantic import ValidationError

from common.config import BaseAppSettings


class TestAppSettings(BaseAppSettings):
    model_config = {**BaseAppSettings.model_config, "env_prefix": "TEST_APP__"}

    service_name: str = "test-app"


def test_reads_prefixed_env(monkeypatch) -> None:
    monkeypatch.setenv("TEST_APP__LOG_LEVEL", "DEBUG")
    settings = TestAppSettings()
    assert settings.log_level == "DEBUG"
    assert settings.service_name == "test-app"


def test_env_restricted_to_known_values() -> None:
    with pytest.raises(ValidationError):
        TestAppSettings(env="bad")


def test_is_prod_flag() -> None:
    assert TestAppSettings(env="prod").is_prod
    assert not TestAppSettings(env="dev").is_prod
