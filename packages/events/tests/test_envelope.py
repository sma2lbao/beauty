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


def test_envelope_carries_tenant_and_round_trips() -> None:
    """租户随信封走:跨服务消费者不依赖外部上下文。"""
    event = EventEnvelope.create("run.delivered.v1", {"a": 1}, tenant_id="t-1")
    assert event.tenant_id == "t-1"
    assert EventEnvelope.from_json(event.to_json()) == event

    # 旧构造方式(无租户)仍然可用
    legacy = EventEnvelope.create("run.delivered.v1", {})
    assert legacy.tenant_id is None
