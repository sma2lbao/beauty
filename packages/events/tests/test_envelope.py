import pytest

from events.envelope import EventEnvelope, EventTypeError


def test_create_sets_fields() -> None:
    env = EventEnvelope.create("email.received.v1", {"message_id": "m-1"})
    assert env.type == "email.received.v1"
    assert env.payload == {"message_id": "m-1"}
    assert env.id and env.occurred_at


def test_json_roundtrip() -> None:
    env = EventEnvelope.create("run.completed.v1", {"run_id": "r-1"})
    assert EventEnvelope.from_json(env.to_json()) == env


@pytest.mark.parametrize(
    "bad",
    [
        "email.received",  # 缺版本段
        "Email.Received.v1",  # 大写
        "email..received.v1",  # 空段
        "email.received.vX",  # 版本非数字
    ],
)
def test_bad_type_rejected(bad: str) -> None:
    with pytest.raises(EventTypeError):
        EventEnvelope.create(bad, {})
