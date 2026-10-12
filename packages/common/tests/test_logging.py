from structlog.testing import capture_logs

from common.config import BaseAppSettings
from common.logging import get_logger, setup_logging


class TestLogSettings(BaseAppSettings):
    model_config = {**BaseAppSettings.model_config, "env_prefix": "TEST_LOG__"}


def test_setup_logging_and_capture() -> None:
    setup_logging(TestLogSettings(service_name="svc", log_level="DEBUG"))
    with capture_logs() as logs:
        get_logger("t").info("hello", key="value")
    assert any(e["event"] == "hello" and e["key"] == "value" for e in logs)
