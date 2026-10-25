#!/usr/bin/env python3
"""R-33 黄金 run:进程内全链路端到端冒烟(零网络、零外部服务)。

用 TestClient 在进程内驱动 agent-orchestrator 的真实 FastAPI 应用(不起端口、
不依赖已启动的服务),用脚本化 FakeLLM 与合成 DANGER 工具走完一条完整链路:

    健康探针 → 会话 → 流式对话(meta/delta/done) → 复杂任务建 run →
    计划审批 → 危险操作确认 → 交付物 → 事件序列/SSE 重放 → sqlite 落库断言

用途:跨重构(R-27/R-28/R-29)的行为回归网——只依赖对外 HTTP 契约与数据库
最终状态,不依赖 graph/services 内部结构。

约定:
  * 零网络:模型/知识库/鉴权全部注入 fake;进程内 socket 守卫把任何
    AF_INET/AF_INET6 connect 视为失败(本脚本自身不启动任何服务);
  * 零外部依赖:真实模型、Docker、Postgres、消息队列都不需要;
  * 默认临时 sqlite,跑完清理;--keep 保留到 .cache/golden-run/<UTC 时间戳>/。

退出码:0 = 全部阶段 PASS;1 = 有阶段 FAIL(打印阶段表与失败断言)。

用法(推荐经包装脚本,自动定位 uv 项目):
    bash scripts/golden-run/golden-run.sh [--keep]
"""

from __future__ import annotations

import argparse
import json
import socket
import sqlite3
import sys
import tempfile
import time
import traceback
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

from common.testing import TestIdP  # noqa: E402  (uv workspace 共享库)
from fastapi.testclient import TestClient  # noqa: E402

from agent_orchestrator.core.config import Settings  # noqa: E402
from agent_orchestrator.main import create_app  # noqa: E402
from agent_orchestrator.services.llm import LLMReply, ToolCall  # noqa: E402
from agent_orchestrator.services.tools import (  # noqa: E402
    ToolRisk,
    ToolSpec,
    build_tools,
)

TENANT_ID = "tenant-golden"
_IDP = TestIdP()
TENANT = _IDP.bearer(TENANT_ID)

CHAT_TEXT = "你好"
CHAT_REPLY = "你好!我是测试助手,可以帮你整理日程。"
TASK_TEXT = "把本周日程整理成周报"
PUBLISH_TITLE = "对外发布说明"
PUBLISH_ARGS = json.dumps({"title": PUBLISH_TITLE}, ensure_ascii=False)
DELIVER_CONTENT = "# 交付物\n\n本周周报已生成。"

#: FakeLLM 的调用脚本(顺序即契约)。会话任务路径不产生 acceptance_criteria,
#: 因此 verify 节点不调模型;整条链路共消耗 6 次模型调用:
#: 0 分流判别(chat) | 1 对话回复 | 2 plan | 3 execute 工具请求
#: 4 execute 收尾正文 | 5 deliver
LLM_SCRIPT: list[str | LLMReply] = [
    '{"mode": "chat"}',
    CHAT_REPLY,
    "# 执行计划\n- [ ] 检索本周日程\n- [ ] 汇总成周报",
    LLMReply(
        content="",
        tool_calls=(
            ToolCall(
                id="call_read",
                name="search_knowledge",
                arguments='{"query": "本周日程"}',
            ),
            ToolCall(
                id="call_write",
                name="external_publish",
                arguments=PUBLISH_ARGS,
            ),
        ),
    ),
    "已发布对外发布说明:本周共 5 个工作日。",
    DELIVER_CONTENT,
]

EXPECTED_EVENTS = [
    "run.created.v1",
    "run.planned.v1",
    "run.approved.v1",
    "run.tool.called.v1",
    "run.confirmation.required.v1",
    "run.confirmation.received.v1",
    "run.tool.called.v1",
    "run.delivered.v1",
    "run.completed.v1",
]


# ---------------------------------------------------------------------------
# 零网络守卫
# ---------------------------------------------------------------------------


class NetworkGuard:
    """把任何 AF_INET/AF_INET6 connect 记为违规并抛错,证明链路零网络。"""

    def __init__(self) -> None:
        self.violations: list[str] = []
        self._original = socket.socket

    def __enter__(self) -> NetworkGuard:
        guard = self
        original = self._original

        class GuardedSocket(original):  # type: ignore[misc, valid-type]
            def connect(self, address: Any) -> Any:
                if self.family in (socket.AF_INET, socket.AF_INET6):
                    guard.violations.append(f"connect({address!r})")
                    raise RuntimeError(
                        f"golden-run 检测到网络连接:{address!r}(本脚本必须零网络)"
                    )
                return super().connect(address)

            def connect_ex(self, address: Any) -> Any:
                if self.family in (socket.AF_INET, socket.AF_INET6):
                    guard.violations.append(f"connect_ex({address!r})")
                    raise RuntimeError(
                        f"golden-run 检测到网络连接:{address!r}(本脚本必须零网络)"
                    )
                return super().connect_ex(address)

        socket.socket = GuardedSocket  # type: ignore[assignment, misc]
        return self

    def __exit__(self, *exc_info: object) -> None:
        socket.socket = self._original  # type: ignore[assignment, misc]


# ---------------------------------------------------------------------------
# 测试替身(零网络;duck typing 满足 services 的 Protocol)
# ---------------------------------------------------------------------------


class FakeLLM:
    """按调用次序吐脚本回复:字符串自动包成 LLMReply(content=...)。"""

    def __init__(self, script: list[str | LLMReply]) -> None:
        self.script = list(script)
        self.calls: list[list[dict[str, Any]]] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMReply:
        self.calls.append(messages)
        index = len(self.calls) - 1
        if index >= len(self.script):
            raise AssertionError(
                f"FakeLLM 脚本不足:第 {index + 1} 次调用没有对应回复"
                f"(脚本共 {len(self.script)} 条)"
            )
        item = self.script[index]
        return item if isinstance(item, LLMReply) else LLMReply(content=item)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        reply = await self.complete(messages, model=model, tools=tools)
        for char in reply.content:
            yield char


class FakeKnowledge:
    """假知识库:内存命中,记录检索调用(零网络)。"""

    def __init__(self) -> None:
        self.queries: list[tuple[str, str, int]] = []

    async def search(self, tenant_id: str, query: str, limit: int) -> dict[str, Any]:
        self.queries.append((tenant_id, query, limit))
        return {
            "query": query,
            "provider": "local",
            "model": "hashing-256",
            "hits": [
                {
                    "title": "本周日程",
                    "score": 0.93,
                    "content": "周一评审、周三发布、周五复盘。",
                }
            ][:limit],
        }


class FakeDangerTool:
    """合成 DANGER 工具:对外不可逆写操作(验证确认闸,零网络)。"""

    name = "external_publish"

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def _handle(self, args: dict[str, Any]) -> str:
        self.sent.append(args)
        return f"已发布:{args.get('title', '')}"

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="对外不可逆写操作(黄金 run 用)",
            parameters={
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            },
            handler=self._handle,
            risk=ToolRisk.DANGER,
        )


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------


@dataclass
class Row:
    name: str
    assertion: str
    elapsed_ms: float
    status: str  # PASS | FAIL | SKIP
    detail: str = ""


@dataclass
class Report:
    rows: list[Row] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return any(row.status != "PASS" for row in self.rows)

    def run(self, name: str, assertion: str, fn: Callable[[], None]) -> bool:
        if self.failed:
            self.rows.append(Row(name, assertion, 0.0, "SKIP"))
            return False
        started = time.perf_counter()
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - 黄金 run 要把失败完整上报
            elapsed = (time.perf_counter() - started) * 1000
            self.rows.append(
                Row(name, assertion, elapsed, "FAIL", f"{type(exc).__name__}: {exc}")
            )
            traceback.print_exc()
            return False
        elapsed = (time.perf_counter() - started) * 1000
        self.rows.append(Row(name, assertion, elapsed, "PASS"))
        return True

    def print(self) -> None:
        print()
        print("阶段表")
        print("-" * 96)
        print(f"{'#':>2}  {'阶段':<18}{'断言':<34}{'耗时':>8}  结果")
        print("-" * 96)
        for index, row in enumerate(self.rows, start=1):
            print(
                f"{index:>2}  {_pad(row.name, 18)}{_pad(row.assertion, 34)}"
                f"{row.elapsed_ms:>7.0f}ms  {row.status}"
            )
            if row.detail:
                print(f"     └─ {row.detail}")
        print("-" * 96)

    def summary(self) -> str:
        passed = sum(1 for row in self.rows if row.status == "PASS")
        total = len(self.rows)
        elapsed = sum(row.elapsed_ms for row in self.rows) / 1000
        return f"{passed}/{total} 阶段通过,用时 {elapsed:.2f}s"


def _pad(text: str, width: int) -> str:
    """按显示宽度补空格(中文占 2 列),让阶段表对齐。"""
    import unicodedata

    shown = sum(
        2 if unicodedata.east_asian_width(char) in ("W", "F") else 1 for char in text
    )
    return text + " " * max(0, width - shown)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# ---------------------------------------------------------------------------
# SSE 解析(与 api 层帧格式一致)
# ---------------------------------------------------------------------------


def parse_sse(text: str) -> list[tuple[str | None, str, dict[str, Any] | None]]:
    """SSE 文本 → [(id, event, data)];忽略 keep-alive 注释帧。"""
    frames: list[tuple[str | None, str, dict[str, Any] | None]] = []
    for block in text.split("\n\n"):
        if not block or block.lstrip().startswith(":"):
            continue
        sid = event = data = None
        for line in block.splitlines():
            if line.startswith("id: "):
                sid = line[4:]
            elif line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if event:
            frames.append((sid, event, json.loads(data) if data else None))
    return frames


# ---------------------------------------------------------------------------
# 装配
# ---------------------------------------------------------------------------


def _settings(db_path: Path) -> Settings:
    """测试专用配置:自足、零网络(显式入参优先级最高,不受本地 .env 影响)。"""
    return Settings(
        _env_file=None,
        env="dev",
        database_url=f"sqlite+aiosqlite:///{db_path}",
        auto_create_tables=True,
        sse_poll_interval_seconds=0.05,
        run_delta_streaming=False,
        # 模型与工具:LLM 走注入 fake;工具走假知识库 + 合成 DANGER 工具
        llm_enabled=True,
        tools_enabled=True,
        # 所有会连外部的开关一律钉死(即使 shell 里导出了 AGENT_ORCHESTRATOR__*)
        outbox_enabled=False,
        knowledge_ingest_enabled=False,
        workspace_enabled=False,
        sandbox_enabled=False,
        mcp_stdio_enabled=False,
        mcp_enabled=False,
        mcp_server_enabled=False,
        mcp_oauth_enabled=False,
        jobs_enabled=False,
        artifact_download_enabled=False,
        plan_auto_approve=False,  # 有 DANGER 工具时人工审批,覆盖审批闸
    )


def _make_workdir(keep: bool) -> tuple[Path, Iterator[None] | None]:
    if keep:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        workdir = ROOT / ".cache" / "golden-run" / stamp
        workdir.mkdir(parents=True, exist_ok=True)
        return workdir, None
    tempdir = tempfile.TemporaryDirectory(prefix="golden-run-")
    return Path(tempdir.name), tempdir.cleanup


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="agent-orchestrator 进程内黄金 run(零网络端到端冒烟)"
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="保留临时 sqlite 与工作目录(默认放 .cache/golden-run/<UTC 时间戳>/)",
    )
    args = parser.parse_args(argv)

    workdir, cleanup = _make_workdir(args.keep)
    db_path = workdir / "golden.db"
    report = Report()
    guard = NetworkGuard()
    ctx = SimpleNamespace(
        db_path=db_path,
        idp=_IDP,
        fake=FakeLLM(LLM_SCRIPT),
        knowledge=FakeKnowledge(),
        danger=FakeDangerTool(),
        expected_events=list(EXPECTED_EVENTS),
    )

    def phase_health() -> None:
        response = ctx.client.get("/healthz")
        check(response.status_code == 200, f"/healthz 期望 200,实际 {response.status_code}")
        check(response.json() == {"status": "ok"}, f"/healthz 响应异常:{response.json()}")
        response = ctx.client.get("/readyz")
        check(
            response.status_code == 200,
            f"/readyz 期望 200,实际 {response.status_code}:{response.text[:200]}",
        )

    def phase_chat_sse() -> None:
        response = ctx.client.post("/api/v1/conversations", json={}, headers=TENANT)
        check(
            response.status_code == 201,
            f"建会话期望 201,实际 {response.status_code}:{response.text[:200]}",
        )
        ctx.conversation_id = response.json()["id"]

        response = ctx.client.post(
            f"/api/v1/conversations/{ctx.conversation_id}/messages/stream",
            json={"content": CHAT_TEXT, "mode": "auto"},
            headers=TENANT,
        )
        check(
            response.status_code == 200,
            f"消息 SSE 期望 200,实际 {response.status_code}:{response.text[:300]}",
        )
        check(
            response.headers["content-type"].startswith("text/event-stream"),
            f"Content-Type 不是 SSE:{response.headers['content-type']}",
        )
        frames = parse_sse(response.text)
        names = [name for _, name, _ in frames]
        check(names and names[0] == "meta", f"首帧应为 meta,实际 {names[:3]}")
        check(names[-1] == "done", f"末帧应为 done,实际 {names[-3:]}")
        check("delta" in names, "chat 流没有 delta 增量帧")
        meta = frames[0][2] or {}
        check(meta.get("mode") == "chat", f"meta.mode 期望 chat,实际 {meta.get('mode')}")
        check(meta.get("run") is None, "chat 模式不应创建 run")
        streamed = "".join(
            str(data.get("text", ""))
            for _, name, data in frames
            if name == "delta" and data
        )
        check(streamed == CHAT_REPLY, f"delta 拼接与回复不一致:{streamed!r}")
        done = frames[-1][2] or {}
        check(
            (done.get("reply") or {}).get("content") == CHAT_REPLY,
            "done.reply 内容与脚本回复不一致",
        )

        messages = ctx.client.get(
            f"/api/v1/conversations/{ctx.conversation_id}/messages", headers=TENANT
        ).json()
        check(
            [item["role"] for item in messages] == ["user", "assistant"],
            f"对话落库角色不符:{[item['role'] for item in messages]}",
        )
        check(
            messages[-1]["content"] == CHAT_REPLY,
            "assistant 回复未按原样落库",
        )

    def phase_task_run() -> None:
        response = ctx.client.post(
            f"/api/v1/conversations/{ctx.conversation_id}/messages/stream",
            json={"content": TASK_TEXT, "mode": "task"},
            headers=TENANT,
        )
        check(
            response.status_code == 200,
            f"任务消息 SSE 期望 200,实际 {response.status_code}:{response.text[:300]}",
        )
        frames = parse_sse(response.text)
        check(
            [name for _, name, _ in frames] == ["meta", "done"],
            f"任务路径应只有 meta+done,实际 {[name for _, name, _ in frames]}",
        )
        meta = frames[0][2] or {}
        check(
            meta.get("mode") == "run_created",
            f"meta.mode 期望 run_created,实际 {meta.get('mode')}",
        )
        run = meta.get("run") or {}
        check(
            run.get("status") == "awaiting_approval",
            f"建 run 后应停在 awaiting_approval,实际 {run.get('status')}",
        )
        check(
            run.get("conversation_id") == ctx.conversation_id,
            "run 未关联到会话",
        )
        ctx.run_id = run["id"]
        check(
            (frames[-1][2] or {}).get("reply") is None,
            "任务路径的 done.reply 应为 null",
        )

        plan = ctx.client.get(f"/api/v1/runs/{ctx.run_id}/plan", headers=TENANT).json()
        check(
            plan.get("status") == "awaiting_approval",
            f"/plan 期望 awaiting_approval,实际 {plan.get('status')}",
        )
        check("执行计划" in plan.get("plan_markdown", ""), "计划正文未落在 plan_markdown")

        detail = ctx.client.get(
            f"/api/v1/conversations/{ctx.conversation_id}", headers=TENANT
        ).json()
        check(detail.get("active_run_id") == ctx.run_id, "会话 active_run_id 未指向新 run")
        messages = ctx.client.get(
            f"/api/v1/conversations/{ctx.conversation_id}/messages", headers=TENANT
        ).json()
        check(
            [item["role"] for item in messages] == ["user", "assistant", "user"],
            f"任务消息落库角色不符:{[item['role'] for item in messages]}",
        )

    def phase_approve_and_confirm() -> None:
        response = ctx.client.post(f"/api/v1/runs/{ctx.run_id}/approve", headers=TENANT)
        check(
            response.status_code == 200,
            f"审批期望 200,实际 {response.status_code}:{response.text[:300]}",
        )
        run = response.json()
        check(
            run["status"] == "awaiting_confirmation",
            f"审批后应停在 awaiting_confirmation,实际 {run['status']}",
        )
        check(
            ctx.danger.sent == [],
            f"确认前危险工具绝不能执行,实际已执行 {ctx.danger.sent}",
        )
        events = ctx.client.get(
            f"/api/v1/runs/{ctx.run_id}/events", headers=TENANT
        ).json()
        required = [e for e in events if e["type"] == "run.confirmation.required.v1"]
        check(len(required) == 1, "缺少 run.confirmation.required.v1 事件")
        pending = required[0]["payload"]["pending_writes"]
        check(len(pending) == 1, f"pending_writes 期望 1 条,实际 {len(pending)}")
        check(
            pending[0]["name"] == "external_publish",
            f"pending_writes[0].name 期望 external_publish,实际 {pending[0]['name']}",
        )
        check(
            json.loads(pending[0]["arguments"]).get("title") == PUBLISH_TITLE,
            "pending_writes[0].arguments 与模型请求不一致",
        )

        response = ctx.client.post(
            f"/api/v1/runs/{ctx.run_id}/confirm",
            json={"action": "approve"},
            headers=TENANT,
        )
        check(
            response.status_code == 200,
            f"确认期望 200,实际 {response.status_code}:{response.text[:300]}",
        )
        run = response.json()
        check(
            run["status"] == "completed",
            f"确认后应 completed,实际 {run['status']}({run.get('status_reason')})",
        )
        check(
            ctx.danger.sent == [{"title": PUBLISH_TITLE}],
            f"危险工具执行记录异常:{ctx.danger.sent}",
        )

    def phase_events() -> None:
        events = ctx.client.get(
            f"/api/v1/runs/{ctx.run_id}/events", headers=TENANT
        ).json()
        ctx.events = events
        types = [event["type"] for event in events]
        check(
            types == ctx.expected_events,
            f"事件序列不符\n  期望 {ctx.expected_events}\n  实际 {types}",
        )
        # 事件回放不含 run.step.v1(步骤时间线走 GET /runs/{id}/steps,见
        # services/run_events.py 的 list_events 注释),因此 seq 在过滤后的列表里
        # 允许有空洞:断言严格递增且唯一,而不是从 1 连续。
        seqs = [event["seq"] for event in events]
        check(
            seqs == sorted(seqs) and len(seqs) == len(set(seqs)),
            f"事件 seq 不是严格递增且唯一:{seqs}",
        )
        names = [
            event["payload"].get("name")
            for event in events
            if event["type"] == "run.tool.called.v1"
        ]
        check(
            names == ["search_knowledge", "external_publish"],
            f"工具事件顺序/名称不符:{names}",
        )
        required_index = types.index("run.confirmation.required.v1")
        received_index = types.index("run.confirmation.received.v1")
        read_index = next(
            index
            for index, type_ in enumerate(types)
            if type_ == "run.tool.called.v1"
        )
        write_index = len(types) - 1 - types[::-1].index("run.tool.called.v1")
        check(read_index < required_index, "只读工具应在确认需求之前执行")
        check(write_index > received_index, "写操作应在人工确认之后执行")
        check(write_index < types.index("run.completed.v1"), "写操作应在 completed 之前")
        completed = next(e for e in events if e["type"] == "run.completed.v1")
        check(completed["payload"].get("rounds", 0) >= 1, "run.completed 缺少轮次")
        check("run.failed.v1" not in types, "链路中出现 run.failed.v1")

    def phase_run_sse() -> None:
        with ctx.client.stream(
            "GET", f"/api/v1/runs/{ctx.run_id}/events/stream", headers=TENANT
        ) as response:
            check(
                response.status_code == 200,
                f"run SSE 期望 200,实际 {response.status_code}",
            )
            raw = b"".join(response.iter_raw()).decode()
        frames = parse_sse(raw)
        names = [name for _, name, _ in frames]
        check(
            names == [*ctx.expected_events, "stream.end"],
            f"SSE 重放事件序列不符:{names}",
        )
        ids = [sid for sid, _, _ in frames[:-1]]
        # SSE id 是 run 内事件 seq,与 /events 回放同源;step 事件被过滤后同样有空洞,
        # 因此对齐已取回的过滤后事件 seq,而不是 1..n 的连续整数。
        expected_ids = [str(event["seq"]) for event in ctx.events]
        check(ids == expected_ids, f"SSE id(seq)不符:{ids} != {expected_ids}")
        check(
            frames[-1][2] == {"status": "completed"},
            f"stream.end payload 不符:{frames[-1][2]}",
        )

    def phase_artifact() -> None:
        response = ctx.client.get(
            f"/api/v1/runs/{ctx.run_id}/artifact", headers=TENANT
        )
        check(
            response.status_code == 200,
            f"取交付物期望 200,实际 {response.status_code}:{response.text[:300]}",
        )
        artifact = response.json()
        check(
            artifact["content"] == DELIVER_CONTENT,
            f"交付物正文与 deliver 脚本不一致:{artifact['content']!r}",
        )
        check(artifact["kind"] == "markdown", f"交付物 kind 不符:{artifact['kind']}")
        check(artifact["run_id"] == ctx.run_id, "交付物 run_id 不匹配")
        check(artifact["title"] == TASK_TEXT, f"交付物标题不符:{artifact['title']}")
        ctx.artifact = artifact

        metas = ctx.client.get(
            f"/api/v1/runs/{ctx.run_id}/artifacts", headers=TENANT
        ).json()
        check(len(metas) == 1, f"交付物清单期望 1 件,实际 {len(metas)}")
        check(metas[0]["id"] == artifact["id"], "清单与主交付物 id 不一致")

        download = ctx.client.get(
            f"/api/v1/runs/{ctx.run_id}/artifacts/{artifact['id']}/download",
            headers=TENANT,
        )
        check(
            download.status_code == 200,
            f"下载交付物期望 200,实际 {download.status_code}",
        )
        check(
            download.content == DELIVER_CONTENT.encode("utf-8"),
            "下载字节与交付物正文不一致",
        )

    def phase_db() -> None:
        connection = sqlite3.connect(ctx.db_path)
        try:
            row = connection.execute(
                "select status, attempts, rounds, title from runs where id = ?",
                (ctx.run_id,),
            ).fetchone()
            check(row is not None, f"runs 表没有 run {ctx.run_id}")
            status, attempts, rounds, title = row
            check(status == "completed", f"runs.status 期望 completed,实际 {status}")
            check(attempts == 1, f"runs.attempts 期望 1,实际 {attempts}")
            check(rounds >= 1, f"runs.rounds 期望 >=1,实际 {rounds}")
            check(title == TASK_TEXT, f"runs.title 不符:{title}")

            rows = list(
                connection.execute(
                    "select type from run_events where run_id = ? order by seq",
                    (ctx.run_id,),
                )
            )
            # run.step.v1 是步骤视图(S1)的底层记录,不进用户事件回放(见
            # services/run_events.py 的 list_events),对齐时先剔除再比。
            event_types = [row[0] for row in rows if row[0] != "run.step.v1"]
            check(event_types == ctx.expected_events, f"DB 事件序列不符:{event_types}")
            step_count = sum(1 for row in rows if row[0] == "run.step.v1")
            check(step_count >= 1, "run.step.v1 步骤事件未落库")

            artifacts = list(
                connection.execute(
                    "select kind, title from run_artifacts where run_id = ?",
                    (ctx.run_id,),
                )
            )
            check(len(artifacts) == 1, f"run_artifacts 期望 1 行,实际 {len(artifacts)}")
            check(artifacts[0][0] == "markdown", "交付物 kind 落库不符")
            check(artifacts[0][1] == TASK_TEXT, "交付物 title 落库不符")

            roles = [
                item[0]
                for item in connection.execute(
                    "select role from chat_messages where conversation_id = ? "
                    "order by rowid",
                    (ctx.conversation_id,),
                )
            ]
            check(
                roles == ["user", "assistant", "user", "assistant"],
                f"对话落库角色序列不符:{roles}",
            )
            last_content = connection.execute(
                "select content from chat_messages where conversation_id = ? "
                "order by rowid desc limit 1",
                (ctx.conversation_id,),
            ).fetchone()[0]
            check(
                "本周周报已生成" in last_content,
                f"run 终态未投影成交付物 assistant 消息:{last_content[:80]!r}",
            )

            conversation = connection.execute(
                "select title from conversations where id = ?",
                (ctx.conversation_id,),
            ).fetchone()
            check(conversation is not None, "conversations 表缺少会话")
            # 会话标题取首条用户消息(任务消息只是并入同一会话,不改标题)
            check(conversation[0] == CHAT_TEXT, f"会话标题不符:{conversation[0]}")

            calls = len(ctx.fake.calls)
            check(
                calls == len(LLM_SCRIPT),
                f"FakeLLM 调用次数期望 {len(LLM_SCRIPT)},实际 {calls}",
            )
            check(
                ctx.knowledge.queries == [(TENANT_ID, "本周日程", 5)],
                f"知识库检索调用不符:{ctx.knowledge.queries}",
            )
        finally:
            connection.close()

    def phase_zero_network() -> None:
        check(
            guard.violations == [],
            f"检测到网络连接尝试:{guard.violations}",
        )

    client_phases = [
        ("健康探针", "/healthz + /readyz 200", phase_health),
        ("会话与流式对话", "meta/delta*/done 且回复落库", phase_chat_sse),
        ("复杂任务建 run", "meta.run 停在 awaiting_approval", phase_task_run),
        (
            "计划审批",
            "approve → awaiting_confirmation 且未执行写操作",
            phase_approve_and_confirm,
        ),
        ("run 事件序列", "9 类事件有序、seq 严格递增", phase_events),
        ("run SSE 重放", "重放全事件 + stream.end", phase_run_sse),
        ("交付物", "正文/清单/下载字节一致", phase_artifact),
    ]
    post_phases = [
        ("数据库落库", "runs/run_events/run_artifacts/chat_messages 一致", phase_db),
        ("零网络守卫", "全程无 AF_INET connect", phase_zero_network),
    ]

    app = create_app(
        _settings(db_path),
        llm_client=ctx.fake,
        toolkit_factory=lambda tenant_id: build_tools(
            tenant_id, knowledge=ctx.knowledge
        ).extended([ctx.danger.spec()]),
        token_verifier=_IDP.verifier(),
    )

    try:
        with guard:
            with TestClient(app) as client:
                ctx.client = client
                for name, assertion, fn in client_phases:
                    report.run(name, assertion, fn)
            for name, assertion, fn in post_phases:
                report.run(name, assertion, fn)
    finally:
        if cleanup is not None:
            cleanup()
        else:
            print(f"\n保留工作目录:{workdir}")
            print(f"  sqlite:{db_path}")

    report.print()
    print(f"\n黄金 run:{report.summary()}")
    if report.failed:
        print("结论:FAIL(存在失败阶段,详见上方断言)")
        return 1
    print("结论:PASS(全链路端到端通过)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
