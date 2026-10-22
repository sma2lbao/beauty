"""正文增量流(§6.3 ``token.delta.v1``):任务执行时把模型正文边产出边落成事件。

任务模式过去只发 meta + done,正文要等 run 终态才连同交付物一起出现;
现在节点把正在写的正文上报给 services 层的 sink,按批落成 run 事件,
会话页订阅 run 事件流即可实时看到输出。这里覆盖:

- 四段正文(plan/execute/verify/deliver)都按 seq 落成 token.delta.v1;
- 同阶段多批增量按 seq 拼接后与模型输出的正文逐字一致(不丢、不重、不乱序);
- 阶段切换处有分段边界(前端按 phase 分组显示);
- 攒批生效(不是逐字写库);
- 默认关闭时行为完全不变(既有事件序列断言依赖这一点)。
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from tests.conftest import TENANT, FakeLLM, _settings, verifier

PLAN = "# 执行计划\n\n## 步骤\n1. 整理日程"
EXECUTE = "已整理本周日程"
VERIFY = '{"summary": "全部通过", "verdicts": {"日程已整理": true}}'
DELIVER = "# 本周周报\n\n正文"

DELTA = "token.delta.v1"


def _delta_client(tmp_path: Path, fake: FakeLLM, name: str = "delta.db", **overrides):
    settings = _settings(
        tmp_path,
        name,
        llm_enabled=True,
        plan_auto_approve=True,
        run_delta_streaming=True,
        **overrides,
    )
    return TestClient(create_app(settings, llm_client=fake, token_verifier=verifier()))


def _create_run(client: TestClient) -> str:
    resp = client.post(
        "/api/v1/runs",
        json={
            "title": "周报",
            "goal": "整理本周日程",
            "acceptance_criteria": ["日程已整理"],
        },
        headers=TENANT,
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _events(client: TestClient, run_id: str) -> list[dict]:
    resp = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT)
    assert resp.status_code == 200
    return resp.json()


def _deltas(events: list[dict]) -> list[dict]:
    return [event for event in events if event["type"] == DELTA]


def _assembled(deltas: list[dict], phase: str) -> str:
    return "".join(
        d["payload"]["text"] for d in deltas if d["payload"]["phase"] == phase
    )


def test_run_streams_each_phase_as_token_deltas(tmp_path: Path) -> None:
    fake = FakeLLM([PLAN, EXECUTE, VERIFY, DELIVER])
    with _delta_client(tmp_path, fake) as client:
        run_id = _create_run(client)
        resp = client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

        events = _events(client, run_id)
        deltas = _deltas(events)
        assert deltas, "任务执行应产出正文增量事件"

        # 每段正文都回来了,且逐字一致(拼接顺序即 seq 顺序)
        assert _assembled(deltas, "plan") == PLAN
        assert _assembled(deltas, "execute") == EXECUTE
        assert _assembled(deltas, "verify") == VERIFY
        assert _assembled(deltas, "deliver") == DELIVER
        # 阶段按执行顺序推进(plan → execute → verify → deliver)
        assert list(dict.fromkeys(d["payload"]["phase"] for d in deltas)) == [
            "plan",
            "execute",
            "verify",
            "deliver",
        ]
        # seq 单调递增,且增量都排在终态之前(实时看到的一定早于结果)
        seqs = [event["seq"] for event in events]
        assert seqs == sorted(seqs)
        completed_seq = next(
            e["seq"] for e in events if e["type"] == "run.completed.v1"
        )
        assert max(d["seq"] for d in deltas) < completed_seq
        # 每批都是非空文本(空批不落事件)
        assert all(d["payload"]["text"] for d in deltas)

        # 终态与落库交付物不受影响(下载回来的正文就是模型输出的原文)
        artifacts = client.get(f"/api/v1/runs/{run_id}/artifacts", headers=TENANT)
        assert artifacts.status_code == 200
        artifact = artifacts.json()[0]
        download = client.get(
            f"/api/v1/runs/{run_id}/artifacts/{artifact['id']}/download",
            headers=TENANT,
        )
        assert download.status_code == 200
        assert download.text == DELIVER


def test_deltas_are_batched_by_chars(tmp_path: Path) -> None:
    fake = FakeLLM([PLAN, EXECUTE, VERIFY, DELIVER])
    with _delta_client(tmp_path, fake, "batch.db", run_delta_flush_chars=5) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)

        deltas = _deltas(_events(client, run_id))
        # 阈值调小后同一段正文会拆成多批——但仍能拼回原文
        assert _assembled(deltas, "deliver") == DELIVER
        deliver_batches = sum(1 for d in deltas if d["payload"]["phase"] == "deliver")
        assert deliver_batches > 1
        # 攒批:事件数远少于字符数(不是逐字写库)
        assert len(deltas) < len(PLAN) + len(EXECUTE) + len(VERIFY) + len(DELIVER)


def test_delta_streaming_off_keeps_event_sequence(tmp_path: Path) -> None:
    fake = FakeLLM([PLAN, EXECUTE, VERIFY, DELIVER])
    app = create_app(
        _settings(tmp_path, "off.db", llm_enabled=True, plan_auto_approve=True),
        llm_client=fake,
        token_verifier=verifier(),
    )
    with TestClient(app) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)

        types = [event["type"] for event in _events(client, run_id)]
        assert DELTA not in types
        assert types[:3] == ["run.created.v1", "run.planned.v1", "run.approved.v1"]


def test_task_conversation_streams_run_deltas_and_projects_reply(
    tmp_path: Path,
) -> None:
    # 对话创建的 run 没有验收标准,verify 节点短路——模型调用顺序是
    # triage → plan → execute → deliver
    fake = FakeLLM(['{"mode": "task"}', PLAN, EXECUTE, DELIVER])
    with _delta_client(tmp_path, fake, "conversation.db") as client:
        created = client.post("/api/v1/conversations", json={}, headers=TENANT)
        assert created.status_code == 201
        conversation_id = created.json()["id"]

        resp = client.post(
            f"/api/v1/conversations/{conversation_id}/messages/stream",
            json={"content": "把本周日程整理成周报"},
            headers=TENANT,
        )
        assert resp.status_code == 200
        # 流式端点本身仍是 meta + done(正文走 run 事件流,不重复推送)
        names = [
            line[len("event: ") :]
            for line in resp.text.split("\n")
            if line.startswith("event: ")
        ]
        assert names == ["meta", "done"]

        meta = next(
            json.loads(line[len("data: ") :])
            for line in resp.text.split("\n")
            if line.startswith("data: ")
        )
        run_id = meta["run"]["id"]

        deltas = _deltas(_events(client, run_id))
        assert _assembled(deltas, "plan") == PLAN
        assert _assembled(deltas, "deliver") == DELIVER

        # assistant 回复仍按统一路径投影进对话(不因流式而重复或缺失)
        messages = client.get(
            f"/api/v1/conversations/{conversation_id}/messages", headers=TENANT
        ).json()
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[-1]["content"] == DELIVER
