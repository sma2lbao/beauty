"""记忆 P2 接线:嵌入提供方装配、自动抽取触发、outbox 消费与周期清扫。

P2 的三个后台能力(自动抽取、近重复巩固/过期清理、语义嵌入)都由容器装配;
本文件从"装配后端到端可见"的角度覆盖,不重复 memory_extraction / memory_sweeper
的单元用例。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from database.session import create_session_factory, session_scope
from events.bus import InMemoryEventBus
from events.envelope import EventEnvelope
from fastapi.testclient import TestClient

from agent_orchestrator.core.config import Settings
from agent_orchestrator.main import create_app
from agent_orchestrator.models.conversations import ChatMessage
from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories import MemoryRepository
from agent_orchestrator.services import embeddings, memory_extraction
from agent_orchestrator.services.embeddings import (
    EMBEDDING_MODEL_LOCAL,
    GatewayEmbeddingProvider,
    LocalHashProvider,
)
from agent_orchestrator.services.outbox import (
    MEMORY_CONSUMER,
    MemoryExtractionConsumer,
    register_consumers,
)
from agent_orchestrator.services.post_commit import (
    PostCommitRuntime,
    bind_runtime,
    drain_after_commit,
    register_after_commit,
)
from tests.conftest import TENANT, FakeLLM, _settings, verifier
from tests.support.conversations import _create_conversation, _post_message

PREFIX = "/api/v1/memories"
TENANT_ID = "t-1"
DELIVERED_EVENT = "run.delivered.v1"
OCCURRED_AT = "2026-10-25T00:00:00+00:00"


@pytest.fixture(autouse=True)
def _reset_embedding() -> Iterator[None]:
    """嵌入提供方是进程级全局:每个用例前后都复位到本地默认。"""
    embeddings.reset_embedding_provider()
    yield
    embeddings.reset_embedding_provider()


def _client_for(settings: Settings, fake: FakeLLM) -> TestClient:
    """按给定配置启动应用(临时 sqlite 建表、LLM 打桩、鉴权验真)。"""
    return TestClient(create_app(settings, llm_client=fake, token_verifier=verifier()))


def test_container_assembles_local_embedding_provider(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "embedding-local.db", llm_enabled=True)
    with _client_for(settings, FakeLLM(["unused"])) as client:
        provider = client.app.state.embedding_provider
        assert isinstance(provider, LocalHashProvider)
        assert provider.model == EMBEDDING_MODEL_LOCAL
        # 装配即固定为进程级提供方(记忆写入/检索都读它)
        assert embeddings.current_provider() is provider


def test_container_assembles_gateway_embedding_provider(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        "embedding-gateway.db",
        llm_enabled=True,
        memory_embedding_provider="gateway",
    )
    with _client_for(settings, FakeLLM(["unused"])) as client:
        provider = client.app.state.embedding_provider
        assert isinstance(provider, GatewayEmbeddingProvider)
        assert provider.model == settings.memory_embedding_model


def test_register_consumers_follows_auto_extract_switch(tmp_path: Path) -> None:
    enabled = _settings(
        tmp_path,
        "consumers-on.db",
        memory_enabled=True,
        memory_auto_extract_enabled=True,
    )
    disabled = _settings(tmp_path, "consumers-off.db")
    factory = create_session_factory(enabled.database_url)
    bus = InMemoryEventBus()
    names = register_consumers(
        bus, session_factory=factory, llm=FakeLLM(["[]"]), settings=enabled
    )
    assert names == [MEMORY_CONSUMER]
    assert bus.subscription_count == 1

    other = InMemoryEventBus()
    assert (
        register_consumers(
            other, session_factory=factory, llm=FakeLLM(["[]"]), settings=disabled
        )
        == []
    )
    assert other.subscription_count == 0


async def _deliver_run_event(
    settings: Settings,
    conversation_id: str,
    fake: FakeLLM,
    *,
    event_id: str = "evt-1",
) -> str:
    """seed 会话消息 + 绑定对话的 run,再对该 run 投递两次交付事件。"""
    factory = create_session_factory(settings.database_url)
    try:
        async with session_scope(factory) as session:
            run = Run(
                tenant_id=TENANT_ID,
                title="周报",
                goal="整理本周日程",
                conversation_id=conversation_id,
            )
            session.add_all(
                [
                    ChatMessage(
                        tenant_id=TENANT_ID,
                        conversation_id=conversation_id,
                        role="user",
                        content="以后周报都用中文写",
                    ),
                    ChatMessage(
                        tenant_id=TENANT_ID,
                        conversation_id=conversation_id,
                        role="assistant",
                        content="好的,记住了",
                    ),
                    run,
                ]
            )
            await session.flush()
            run_id = run.id
        consumer = MemoryExtractionConsumer(fake, factory, settings)
        envelope = EventEnvelope(
            id=event_id,
            type=DELIVERED_EVENT,
            occurred_at=OCCURRED_AT,
            payload={"run_id": run_id},
            tenant_id=TENANT_ID,
        )
        await consumer(envelope)
        await consumer(envelope)
        return run_id
    finally:
        await factory.engine.dispose()


def test_memory_consumer_extracts_from_run_conversation(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        "consumer.db",
        llm_enabled=True,
        memory_enabled=True,
        memory_auto_extract_enabled=True,
        memory_extraction_min_messages=2,
    )
    fake = FakeLLM(['[{"kind": "preference", "content": "用户偏好中文周报"}]'])
    with _client_for(settings, fake) as client:
        conversation_id = _create_conversation(client)
        run_id = asyncio.run(_deliver_run_event(settings, conversation_id, fake))
        rows = client.get(PREFIX, headers=TENANT).json()
    assert run_id
    assert len(rows) == 1
    assert rows[0]["source_type"] == "run"
    assert rows[0]["kind"] == "preference"
    assert rows[0]["content"] == "用户偏好中文周报"
    # 同一事件重投由 processed_events 去重:第二次不再调模型
    assert len(fake.calls) == 1


def test_memory_consumer_tolerates_bad_event(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        "consumer-bad.db",
        memory_enabled=True,
        memory_auto_extract_enabled=True,
    )
    fake = FakeLLM(["[]"])
    factory = create_session_factory(settings.database_url)
    consumer = MemoryExtractionConsumer(fake, factory, settings)
    # 缺 run_id:只记 warning,不抛(否则 outbox 会无限重投)
    asyncio.run(
        consumer(
            EventEnvelope(
                id="bad-event",
                type=DELIVERED_EVENT,
                occurred_at=OCCURRED_AT,
                payload={},
                tenant_id=TENANT_ID,
            )
        )
    )
    # 其他事件类型直接忽略
    asyncio.run(
        consumer(
            EventEnvelope(
                id="other-event",
                type="run.created.v1",
                occurred_at=OCCURRED_AT,
                payload={"run_id": "whatever"},
                tenant_id=TENANT_ID,
            )
        )
    )
    assert fake.calls == []


def test_container_starts_and_stops_memory_sweeper(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        "sweeper.db",
        llm_enabled=True,
        memory_enabled=True,
        memory_sweep_interval_seconds=3600.0,
    )
    with _client_for(settings, FakeLLM(["unused"])) as client:
        container = client.app.state.container
        task = container.memory_sweep_task
        assert task is not None
        assert not task.done()
        assert task.get_name() == "orchestrator-memory-sweeper"
    # stop() 必须取消后台任务并清空引用(否则关闭测试 app 会留悬挂任务)
    assert container.memory_sweep_task is None


def test_container_registers_memory_consumer_on_outbox(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        "outbox-memory.db",
        llm_enabled=True,
        outbox_enabled=True,
        outbox_dispatch_interval_seconds=60.0,
        memory_enabled=True,
        memory_auto_extract_enabled=True,
    )
    with _client_for(settings, FakeLLM(["unused"])) as client:
        assert client.app.state.outbox_consumers == [MEMORY_CONSUMER]


def test_chat_reply_triggers_auto_extraction(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        "chat-memory.db",
        llm_enabled=True,
        memory_enabled=True,
        memory_auto_extract_enabled=True,
        # 每 2 条消息(一轮问答)触发一次,便于确定性断言
        memory_extraction_min_messages=2,
    )
    fake = FakeLLM(
        [
            "好的,以后周报用中文写。",
            '[{"kind": "preference", "content": "用户偏好中文周报"}]',
        ]
    )
    with _client_for(settings, fake) as client:
        conversation_id = _create_conversation(client)
        _post_message(client, conversation_id, "以后周报用中文写", mode="chat")
        rows = client.get(PREFIX, headers=TENANT).json()
    # 第 1 次调模型出答复,第 2 次是抽取
    assert len(fake.calls) == 2
    assert len(rows) == 1
    assert rows[0]["source_type"] == "chat"
    assert rows[0]["content"] == "用户偏好中文周报"


def test_chat_skips_extraction_below_threshold(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        "chat-memory-throttle.db",
        llm_enabled=True,
        memory_enabled=True,
        memory_auto_extract_enabled=True,
        memory_extraction_min_messages=100,
    )
    # 只准备答复:若误触发抽取会因响应耗尽而报错
    fake = FakeLLM(["好的。"])
    with _client_for(settings, fake) as client:
        conversation_id = _create_conversation(client)
        _post_message(client, conversation_id, "你好", mode="chat")
        rows = client.get(PREFIX, headers=TENANT).json()
    assert len(fake.calls) == 1
    assert rows == []


def test_auto_extract_is_off_by_default() -> None:
    """P1-5 隐私默认值:自动抽取会把对话写进跨工作区可见的长期记忆,默认关。"""
    assert Settings.model_fields["memory_auto_extract_enabled"].default is False


async def _run_completion_with_callback(
    settings: Settings,
    fake: FakeLLM,
) -> None:
    """模拟 run 完成:seed 对话消息+run,绑定运行时后登记提交后回调。"""
    factory = create_session_factory(settings.database_url)
    try:
        async with session_scope(factory) as session:
            run = Run(
                tenant_id=TENANT_ID,
                title="周报",
                goal="整理本周日程",
                conversation_id="conv-run-1",
            )
            session.add_all(
                [
                    ChatMessage(
                        tenant_id=TENANT_ID,
                        conversation_id="conv-run-1",
                        role="user",
                        content="以后周报都用中文写",
                    ),
                    ChatMessage(
                        tenant_id=TENANT_ID,
                        conversation_id="conv-run-1",
                        role="assistant",
                        content="好的,记住了",
                    ),
                    run,
                ]
            )
            await session.flush()
            bind_runtime(
                session, PostCommitRuntime(factory, llm=fake, settings=settings)
            )
            memory_extraction.schedule_run_extraction(session, run)
        await drain_after_commit(session)
    finally:
        await factory.engine.dispose()


async def _active_memories(settings: Settings) -> list:
    factory = create_session_factory(settings.database_url)
    try:
        async with session_scope(factory) as session:
            return list(await MemoryRepository(session).list_active(TENANT_ID))
    finally:
        await factory.engine.dispose()


def test_run_completion_extracts_after_commit_without_outbox(tmp_path: Path) -> None:
    """P1-3:outbox 关闭时,run 完成走进程内提交后回调照样沉淀记忆。"""
    settings = _settings(
        tmp_path,
        "run-memory-post-commit.db",
        llm_enabled=True,
        outbox_enabled=False,
        memory_enabled=True,
        memory_auto_extract_enabled=True,
        memory_extraction_min_messages=2,
    )
    fake = FakeLLM(['[{"kind": "preference", "content": "用户偏好中文周报"}]'])
    # 先起一次应用让 sqlite 建表(容器 auto_create_tables),再直调服务层
    with _client_for(settings, FakeLLM(["unused"])):
        asyncio.run(_run_completion_with_callback(settings, fake))
        rows = asyncio.run(_active_memories(settings))
    assert len(rows) == 1
    assert rows[0].source_type == "run"
    assert rows[0].kind == "preference"
    assert rows[0].content == "用户偏好中文周报"


def test_run_completion_throttled_like_chat(tmp_path: Path) -> None:
    """run 路径与 chat 同一节流口径:累计条数不是 N 的整数倍时不抽取。"""
    settings = _settings(
        tmp_path,
        "run-memory-throttle.db",
        llm_enabled=True,
        outbox_enabled=False,
        memory_enabled=True,
        memory_auto_extract_enabled=True,
        memory_extraction_min_messages=100,
    )
    fake = FakeLLM(["[]"])
    with _client_for(settings, FakeLLM(["unused"])):
        asyncio.run(_run_completion_with_callback(settings, fake))
        rows = asyncio.run(_active_memories(settings))
    assert fake.calls == []
    assert rows == []


def test_run_extraction_defers_to_outbox_when_enabled(tmp_path: Path) -> None:
    """outbox 开启时交给消费者,提交后回调不再重复抽取(避免同一 run 抽两次)。"""
    settings = _settings(
        tmp_path,
        "run-memory-outbox.db",
        llm_enabled=True,
        outbox_enabled=True,
        outbox_dispatch_interval_seconds=60.0,
        memory_enabled=True,
        memory_auto_extract_enabled=True,
        memory_extraction_min_messages=2,
    )
    fake = FakeLLM(["[]"])

    async def scenario() -> None:
        factory = create_session_factory(settings.database_url)
        try:
            async with session_scope(factory) as session:
                bind_runtime(
                    session, PostCommitRuntime(factory, llm=fake, settings=settings)
                )
                memory_extraction.schedule_run_extraction(
                    session,
                    Run(
                        tenant_id=TENANT_ID,
                        title="周报",
                        goal="整理本周日程",
                        conversation_id="conv-run-1",
                    ),
                )
            await drain_after_commit(session)
        finally:
            await factory.engine.dispose()

    with _client_for(settings, fake):
        asyncio.run(scenario())
    assert fake.calls == []


def test_post_commit_failure_does_not_break_later_callbacks(tmp_path: Path) -> None:
    """提交后副作用失败只 warning:不能反噬响应,也不能打断后续回调。"""
    settings = _settings(tmp_path, "post-commit-failure.db")
    executed: list[str] = []

    async def scenario() -> None:
        factory = create_session_factory(settings.database_url)
        try:
            async with session_scope(factory) as session:
                bind_runtime(
                    session,
                    PostCommitRuntime(factory, llm=None, settings=settings),
                )

                async def failing() -> None:
                    raise RuntimeError("boom")

                async def succeeding() -> None:
                    executed.append("ok")

                register_after_commit(session, failing)
                register_after_commit(session, succeeding)
            await drain_after_commit(session)
        finally:
            await factory.engine.dispose()

    asyncio.run(scenario())
    assert executed == ["ok"]
