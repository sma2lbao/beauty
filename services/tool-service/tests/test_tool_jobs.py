"""后台作业(job_*):开关、启动、配额、输出/终止、lazy timeout 与审计。

沙箱用 JobSandboxProvider 按脚本内容脚本化回包(零网络):启动脚本、状态脚本、
终止脚本各自返回预置结果,清理脚本一律成功。所有断言都走真实 HTTP 端点,
从而顺带覆盖目录开关、审批下限、幂等审计与 tool_calls 落库。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from common.clients.sandbox import SandboxExecResult
from common.errors import UpstreamError
from fastapi.testclient import TestClient

from tests.conftest import ACCOUNT, FakeSandboxProvider, _settings, verifier
from tool_service.main import create_app
from tool_service.services.execution import job_scripts

JOB_ID = "b" * 32
OTHER_JOB_ID = "c" * 32
TOOLS = ("job_start", "job_output", "job_kill")


def _ok(stdout: str = "", *, exit_code: int = 0) -> SandboxExecResult:
    return SandboxExecResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr="",
        duration_ms=3,
        truncated=False,
    )


def _status_output(state: str, *, stdout: str = "", stderr: str = "") -> str:
    return f"STATUS:{state}\n---STDOUT---\n{stdout}\n---STDERR---\n{stderr}"


class JobSandboxProvider(FakeSandboxProvider):
    """按脚本种类分派的沙箱替身:cleanup 一律成功,其余按队列回包。"""

    def __init__(self) -> None:
        super().__init__()
        self.replies: list[SandboxExecResult | BaseException] = []

    async def exec(
        self,
        account_id: str,
        sandbox_id: str,
        argv: list[str],
        *,
        cwd: str = "/workspace",
        stdin: str | None = None,
        timeout_seconds: int | None = None,
    ) -> SandboxExecResult:
        self.exec_calls.append(
            {
                "account_id": account_id,
                "sandbox_id": sandbox_id,
                "argv": argv,
                "cwd": cwd,
                "stdin": stdin,
                "timeout_seconds": timeout_seconds,
            }
        )
        if self.exec_error is not None:
            raise self.exec_error
        script = argv[-1] if argv else ""
        if "rm -rf --" in script:
            return _ok()
        if not self.replies:
            return _ok()
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def _client(
    tmp_path: Path, provider: JobSandboxProvider, **overrides: Any
) -> TestClient:
    app = create_app(
        _settings(tmp_path, jobs_enabled=True, **overrides),
        verifier(),
        sandbox_provider=provider,
    )
    return TestClient(app)


def _execute(
    client: TestClient,
    tool: str,
    arguments: dict[str, Any],
    *,
    tool_call_id: str = "call-job",
    approved: bool = True,
    headers: dict[str, str] | None = None,
) -> Any:
    return client.post(
        f"/v1/tools/{tool}:execute",
        json={
            "tool_call_id": tool_call_id,
            "arguments": arguments,
            "approved": approved,
        },
        headers=headers or ACCOUNT,
    )


def _connect(tmp_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(tmp_path / "test.db")
    connection.row_factory = sqlite3.Row
    return connection


def _insert_job(
    tmp_path: Path,
    *,
    job_id: str = JOB_ID,
    account_id: str = "t-1",
    status: str = "running",
    exit_code: int | None = None,
    created_at: str | None = None,
    finished_at: str | None = None,
) -> None:
    with _connect(tmp_path) as db:
        db.execute(
            "INSERT INTO background_jobs (id, account_id, sandbox_id, scope_key,"
            " run_id, conversation_id, tool_call_id, name, command, cwd, status,"
            " exit_code, finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                job_id,
                account_id,
                "sbx-1",
                "acct:t-1",
                None,
                None,
                "call-seed",
                None,
                "sleep 100",
                "/workspace",
                status,
                exit_code,
                finished_at,
            ),
        )
        if created_at is not None:
            db.execute(
                "UPDATE background_jobs SET created_at = ? WHERE id = ?",
                (created_at, job_id),
            )


def _job_row(tmp_path: Path, job_id: str = JOB_ID) -> dict[str, Any] | None:
    with _connect(tmp_path) as db:
        row = db.execute(
            "SELECT * FROM background_jobs WHERE id = ?", (job_id,)
        ).fetchone()
    return dict(row) if row is not None else None


def _call_with(provider: JobSandboxProvider, marker: str) -> dict[str, Any]:
    for call in provider.exec_calls:
        if marker in call["argv"][-1]:
            return call
    raise AssertionError(f"未找到包含 {marker!r} 的沙箱调用:{provider.exec_calls}")


# ---- 目录与开关 ----------------------------------------------------------


def test_job_tools_are_listed_with_risk_and_executor(tmp_path: Path) -> None:
    provider = JobSandboxProvider()
    with _client(tmp_path, provider) as client:
        tools = {tool["name"]: tool for tool in client.get("/v1/tools").json()["tools"]}

    assert set(TOOLS) <= set(tools)
    assert tools["job_start"]["risk"] == "destructive"
    assert tools["job_start"]["executor"] == "sandbox"
    assert tools["job_output"]["risk"] == "read"
    assert tools["job_kill"]["risk"] == "write"


def test_job_start_requires_approval(tmp_path: Path) -> None:
    provider = JobSandboxProvider()
    with _client(tmp_path, provider) as client:
        resp = _execute(client, "job_start", {"command": "echo hi"}, approved=False)

    assert resp.status_code == 403
    assert resp.json()["code"] == "tool.confirmation_required"
    assert provider.exec_calls == []


# ---- job_start -----------------------------------------------------------


def test_job_start_launches_background_process_and_persists_row(
    tmp_path: Path,
) -> None:
    provider = JobSandboxProvider()
    provider.replies = [_ok("PID:4321\n")]
    command = "echo 'hi'; echo $(date)"
    with _client(tmp_path, provider) as client:
        resp = _execute(
            client,
            "job_start",
            {"command": command, "cwd": "work", "name": "训练"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    job_id = body["job_id"]
    assert job_scripts.is_valid_job_id(job_id)
    assert body["status"] == "running"
    assert body["name"] == "训练"
    assert body["command"] == command
    assert body["cwd"] == "/workspace/work"
    assert body["log_path"] == f"/workspace/.tool-jobs/{job_id}/"
    assert body["started_at"].endswith("Z")
    assert any("job_output" in note for note in body["notes"])

    start_call = _call_with(provider, "mkdir -p")
    assert start_call["argv"][:2] == ["/bin/sh", "-lc"]
    script = start_call["argv"][-1]
    # 模型 command 只经 shlex.quote 嵌入内层脚本(内层再被整体 quote 一层),
    # 因此原文绝不能裸露在脚本里,且同一行只出现一次外层调用。
    assert command not in script
    assert script.count("mkdir -p") == 1
    assert "setsid" in script

    row = _job_row(tmp_path, job_id)
    assert row is not None
    assert row["status"] == "running"
    assert row["account_id"] == "t-1"
    assert row["tool_call_id"] == "call-job"
    assert row["cwd"] == "/workspace/work"
    assert row["command"] == command


def test_job_start_rejects_cwd_outside_workspace(tmp_path: Path) -> None:
    provider = JobSandboxProvider()
    provider.replies = [_ok("PID:1\n")]
    with _client(tmp_path, provider) as client:
        resp = _execute(client, "job_start", {"command": "echo hi", "cwd": "/etc"})

    body = resp.json()
    assert resp.status_code == 200
    assert body["ok"] is False
    assert body["error"]["code"] == "tool.invalid_args"
    assert provider.exec_calls == []


def test_job_start_enforces_concurrent_limit(tmp_path: Path) -> None:
    provider = JobSandboxProvider()
    with _client(tmp_path, provider) as client:
        for index in range(5):
            _insert_job(tmp_path, job_id=f"{index:032x}")
        resp = _execute(client, "job_start", {"command": "echo hi"})

    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "tool.budget_exceeded"
    assert body["error"]["hint"]
    assert provider.exec_calls == []


def test_job_start_reports_script_failure_and_best_effort_kills(
    tmp_path: Path,
) -> None:
    provider = JobSandboxProvider()
    provider.replies = [
        _ok("", exit_code=1),
        _ok("STATUS:MISSING\n"),
    ]
    with _client(tmp_path, provider) as client:
        resp = _execute(client, "job_start", {"command": "cd /nope"})

    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "tool.upstream_error"
    # 启动失败后必须尝试收拾残局(这里给 kill 脚本的回包 MISSING 也可接受)。
    assert any("kill -TERM" in call["argv"][-1] for call in provider.exec_calls)
    with _connect(tmp_path) as db:
        count = db.execute("SELECT COUNT(*) FROM background_jobs").fetchone()[0]
    assert count == 0


# ---- job_output ----------------------------------------------------------


def test_job_output_returns_running_snapshot(tmp_path: Path) -> None:
    provider = JobSandboxProvider()
    provider.replies = [
        _ok(_status_output("RUNNING", stdout="epoch 1 loss 0.4", stderr="warn"))
    ]
    with _client(tmp_path, provider) as client:
        _insert_job(tmp_path)
        resp = _execute(client, "job_output", {"job_id": JOB_ID})

    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "running"
    assert body["exit_code"] is None
    assert body["stdout"] == "epoch 1 loss 0.4"
    assert body["stderr"] == "warn"
    assert body["finished_at"] is None
    assert any("仍在运行" in note for note in body["notes"])
    assert _job_row(tmp_path)["status"] == "running"


def test_job_output_marks_exited_with_real_exit_code(tmp_path: Path) -> None:
    provider = JobSandboxProvider()
    provider.replies = [_ok(_status_output("EXIT:3", stdout="boom"))]
    with _client(tmp_path, provider) as client:
        _insert_job(tmp_path)
        resp = _execute(client, "job_output", {"job_id": JOB_ID})

    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "exited"
    assert body["exit_code"] == 3
    assert body["stdout"] == "boom"
    assert body["finished_at"] is not None
    row = _job_row(tmp_path)
    assert row["status"] == "exited"
    assert row["exit_code"] == 3


def test_job_output_marks_lost_when_process_vanished(tmp_path: Path) -> None:
    provider = JobSandboxProvider()
    provider.replies = [_ok(_status_output("NOEXIT"))]
    with _client(tmp_path, provider) as client:
        _insert_job(tmp_path)
        resp = _execute(client, "job_output", {"job_id": JOB_ID})

    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "lost"
    assert any("OOM" in note for note in body["notes"])
    assert _job_row(tmp_path)["status"] == "lost"


def test_job_output_marks_lost_when_sandbox_is_gone(tmp_path: Path) -> None:
    provider = JobSandboxProvider()
    provider.replies = [
        UpstreamError(
            "sandbox.upstream_error",
            "沙箱服务返回 404",
            details={"status": 404},
        )
    ]
    with _client(tmp_path, provider) as client:
        _insert_job(tmp_path)
        resp = _execute(client, "job_output", {"job_id": JOB_ID})

    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "lost"
    assert _job_row(tmp_path)["status"] == "lost"


def test_job_output_applies_lazy_timeout(tmp_path: Path) -> None:
    provider = JobSandboxProvider()
    provider.replies = [_ok(_status_output("KILLED"))]
    with _client(tmp_path, provider) as client:
        _insert_job(tmp_path, created_at="2020-01-01 00:00:00")
        resp = _execute(client, "job_output", {"job_id": JOB_ID})

    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "timeout"
    assert any("timeout" in note for note in body["notes"])
    assert any("kill -TERM" in call["argv"][-1] for call in provider.exec_calls)
    assert _job_row(tmp_path)["status"] == "timeout"


def test_job_output_is_account_scoped(tmp_path: Path) -> None:
    provider = JobSandboxProvider()
    with _client(tmp_path, provider) as client:
        _insert_job(tmp_path, account_id="t-2", job_id=OTHER_JOB_ID)
        resp = _execute(client, "job_output", {"job_id": OTHER_JOB_ID}, headers=ACCOUNT)

    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "tool.not_found"
    assert provider.exec_calls == []


@pytest.mark.parametrize("job_id", ["", "not-a-job", "B" * 32])
def test_job_output_rejects_invalid_job_id(tmp_path: Path, job_id: str) -> None:
    provider = JobSandboxProvider()
    with _client(tmp_path, provider) as client:
        resp = _execute(client, "job_output", {"job_id": job_id})

    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "tool.invalid_args"


# ---- job_kill ------------------------------------------------------------


def test_job_kill_terminates_running_job(tmp_path: Path) -> None:
    provider = JobSandboxProvider()
    provider.replies = [_ok(_status_output("KILLED"))]
    with _client(tmp_path, provider) as client:
        _insert_job(tmp_path)
        resp = _execute(client, "job_kill", {"job_id": JOB_ID})

    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "killed"
    assert body["already_finished"] is False
    assert _job_row(tmp_path)["status"] == "killed"


def test_job_kill_finished_job_is_idempotent(tmp_path: Path) -> None:
    provider = JobSandboxProvider()
    with _client(tmp_path, provider) as client:
        _insert_job(
            tmp_path,
            status="exited",
            exit_code=2,
            finished_at="2026-01-01 00:00:00",
        )
        resp = _execute(client, "job_kill", {"job_id": JOB_ID})

    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "exited"
    assert body["exit_code"] == 2
    assert body["already_finished"] is True
    assert provider.exec_calls == []


def test_job_kill_other_account_is_not_found(tmp_path: Path) -> None:
    provider = JobSandboxProvider()
    with _client(tmp_path, provider) as client:
        _insert_job(tmp_path, account_id="t-2", job_id=OTHER_JOB_ID)
        resp = _execute(client, "job_kill", {"job_id": OTHER_JOB_ID})

    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "tool.not_found"
    assert provider.exec_calls == []


def test_job_kill_marks_lost_when_sandbox_is_gone(tmp_path: Path) -> None:
    provider = JobSandboxProvider()
    provider.replies = [
        UpstreamError(
            "sandbox.unreachable",
            "沙箱服务不可达",
            details={"status": 404},
        )
    ]
    with _client(tmp_path, provider) as client:
        _insert_job(tmp_path)
        resp = _execute(client, "job_kill", {"job_id": JOB_ID})

    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "lost"
    assert _job_row(tmp_path)["status"] == "lost"


# ---- 审计 ----------------------------------------------------------------


def test_job_tools_write_audit_rows(tmp_path: Path) -> None:
    provider = JobSandboxProvider()
    provider.replies = [
        _ok("PID:5678\n"),
        _ok(_status_output("RUNNING", stdout="hi")),
        _ok(_status_output("KILLED")),
    ]
    with _client(tmp_path, provider) as client:
        started = _execute(client, "job_start", {"command": "echo hi"})
        job_id = started.json()["job_id"]
        _execute(client, "job_output", {"job_id": job_id}, tool_call_id="call-out")
        _execute(client, "job_kill", {"job_id": job_id}, tool_call_id="call-kill")

    with _connect(tmp_path) as db:
        rows = db.execute(
            "SELECT tool_name, risk, decision, ok, sandbox_id FROM tool_calls"
            " ORDER BY tool_name"
        ).fetchall()
    by_tool = {row["tool_name"]: dict(row) for row in rows}
    assert set(by_tool) == set(TOOLS)
    assert by_tool["job_start"]["risk"] == "destructive"
    assert by_tool["job_start"]["decision"] == "approved"
    assert by_tool["job_output"]["risk"] == "read"
    assert by_tool["job_kill"]["risk"] == "write"
    assert all(row["ok"] == 1 for row in by_tool.values())
    assert by_tool["job_start"]["sandbox_id"] == "sbx-1"
