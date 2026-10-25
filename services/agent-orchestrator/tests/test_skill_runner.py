"""P3-C 技能包沙箱执行:execute 成功/失败链路与 ToolSpec 边界(零网络/零真实沙箱)。

夹具与 FakeSandbox/make_env 见 tests/support/skill_runner.py。
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_orchestrator.services.skill_runner import (
    LIST_OUTPUTS_PATH,
    OUTPUT_ROOT,
    PACKAGE_PATH,
    SKILL_ROOT,
    UNPACK_PATH,
    UNPACK_PY,
)
from agent_orchestrator.services.tools import ToolRisk
from tests.conftest import FakeWorkspace
from tests.support.skill_runner import PACKAGE_KEY, FakeSandbox, _exec, make_env


async def test_execute_runs_entrypoint_and_registers_artifact(tmp_path: Path) -> None:
    async with make_env(tmp_path) as env:
        raw = await env.runner.execute("t-1", env.run_id, "请生成周报")
        result = json.loads(raw)

        assert result["exit_code"] == 0
        assert result["stdout"] == "周报已生成"
        assert result["truncated"] is False
        assert [item["path"] for item in result["outputs"]] == ["report.md"]
        assert result["outputs"][0]["artifact_id"] == result["artifact_id"]
        assert "已登记" in result["summary"]

        # 沙箱:按 run 快照起一次性 skill 沙箱,network 取快照值
        created = env.sandbox.created[0]
        assert created["kind"] == "skill"
        assert created["image"] == env.settings.skill_python_image
        assert created["network"] == "deny"
        assert created["ttl_seconds"] == env.settings.skill_sandbox_ttl_seconds
        assert created["run_id"] == env.run_id

        # 取包 → 写包/解包脚本 → 沙箱内解包(sha 已校验)
        assert env.workspace.gets == [("t-1", PACKAGE_KEY)]
        assert env.sandbox.files[f"sb-1:{PACKAGE_PATH}"] == env.package
        assert env.sandbox.files[f"sb-1:{UNPACK_PATH}"] == UNPACK_PY.encode()
        unpack = env.sandbox.execs[0]
        assert unpack["argv"] == ["python3", UNPACK_PATH]
        assert unpack["cwd"] == "/workspace"
        assert json.loads(unpack["stdin"]) == {
            "zip": PACKAGE_PATH,
            "dest": SKILL_ROOT,
        }

        # entrypoint:runtime 解释器 + 快照 argv,cwd=技能根,stdin 传 input/goal/title
        entry = env.sandbox.execs[1]
        assert entry["argv"] == ["python", "scripts/run.py"]
        assert entry["cwd"] == SKILL_ROOT
        assert json.loads(entry["stdin"]) == {
            "input": "请生成周报",
            "goal": "生成周报",
            "title": "周报",
        }
        assert entry["timeout_seconds"] == 120 + 30

        # 产物:列表脚本用配置上限,README 被读回正文
        listing = env.sandbox.execs[2]
        assert listing["argv"] == [
            "python3",
            LIST_OUTPUTS_PATH,
            OUTPUT_ROOT,
            str(env.settings.skill_max_output_files),
        ]
        assert env.sandbox.reads == [f"{OUTPUT_ROOT}/report.md"]

        # 交付物登记:逐文件一件,filename/title=相对路径,source 标记技能来源
        row = await env.artifact()
        assert row is not None
        assert row.id == result["artifact_id"]
        assert row.title == "report.md"
        assert row.filename == "report.md"
        assert row.kind == "markdown"
        assert row.content_type == "text/markdown"
        assert row.source == "skill"
        assert row.storage == "filesystem"
        assert env.store is not None and row.object_key
        assert await env.store.get(row.object_key) == "# 报告".encode()

        assert env.sandbox.destroyed == ["sb-1"]


async def test_execute_without_package_returns_error(tmp_path: Path) -> None:
    async with make_env(tmp_path, run_overrides={"skill_object_key": None}) as env:
        result = json.loads(await env.runner.execute("t-1", env.run_id, None))
        assert "没有绑定可执行的技能包" in result["error"]
        assert env.sandbox.created == []
        assert env.workspace.gets == []


async def test_execute_sha_mismatch_destroys_sandbox(tmp_path: Path) -> None:
    async with make_env(tmp_path, run_overrides={"skill_sha256": "b" * 64}) as env:
        result = json.loads(await env.runner.execute("t-1", env.run_id, None))
        assert "sha256" in result["error"]
        assert env.sandbox.destroyed == ["sb-1"]
        assert env.sandbox.files == {}  # 校验失败不写包
        assert env.sandbox.execs == []


async def test_execute_unpack_failure_reports_reason(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    sandbox.unpack = _exec(
        exit_code=2,
        stdout=json.dumps({"ok": False, "code": "path_invalid", "message": "非法路径"}),
    )
    async with make_env(tmp_path, sandbox=sandbox) as env:
        result = json.loads(await env.runner.execute("t-1", env.run_id, None))
        assert "解包失败" in result["error"]
        assert "非法路径" in result["error"]
        assert env.sandbox.destroyed == ["sb-1"]
        # 解包失败不再执行 entrypoint
        assert [item["argv"][1] for item in env.sandbox.execs] == [UNPACK_PATH]


async def test_execute_entrypoint_nonzero_exit_is_reported(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    sandbox.entry = _exec(exit_code=2, stdout="partial", stderr="脚本炸了")
    async with make_env(tmp_path, sandbox=sandbox) as env:
        result = json.loads(await env.runner.execute("t-1", env.run_id, None))
        assert result["exit_code"] == 2
        assert result["stdout"] == "partial"
        assert result["stderr"] == "脚本炸了"
        assert env.sandbox.destroyed == ["sb-1"]
        # 失败也照样登记已产出的文本(让用户能拿到半成品)
        assert result["artifact_id"]
        assert await env.artifact() is not None


async def test_execute_rejects_unsafe_snapshot_entrypoint(tmp_path: Path) -> None:
    """快照被改成 ../ 开头时拒绝执行(不把 DB 快照当作可信输入)。"""
    async with make_env(tmp_path, entrypoint=("../evil.py",)) as env:
        result = json.loads(await env.runner.execute("t-1", env.run_id, None))
        assert "路径穿越" in result["error"]
        assert env.sandbox.destroyed == ["sb-1"]
        # 解包已发生,destroy 一定会跑
        assert [item["argv"][1] for item in env.sandbox.execs] == [UNPACK_PATH]


async def test_execute_workspace_failure_returns_error(tmp_path: Path) -> None:
    workspace = FakeWorkspace(fail=True)
    async with make_env(tmp_path, workspace=workspace) as env:
        result = json.loads(await env.runner.execute("t-1", env.run_id, None))
        assert "技能包读取失败" in result["error"]
        assert env.sandbox.destroyed == ["sb-1"]


async def test_execute_missing_run_returns_error(tmp_path: Path) -> None:
    async with make_env(tmp_path) as env:
        result = json.loads(await env.runner.execute("t-1", "no-such-run", None))
        assert "run 不存在" in result["error"]
        assert env.sandbox.created == []


async def test_execute_suppresses_destroy_failure(tmp_path: Path) -> None:
    sandbox = FakeSandbox()
    sandbox.destroy_error = RuntimeError("沙箱已过期")
    async with make_env(tmp_path, sandbox=sandbox) as env:
        result = json.loads(await env.runner.execute("t-1", env.run_id, None))
        assert result["exit_code"] == 0
        assert env.sandbox.destroyed == ["sb-1"]


async def test_spec_shape_and_confirmation(tmp_path: Path) -> None:
    async with make_env(tmp_path) as env:
        spec = env.runner.spec(env.run)
        assert spec.name == "skill_run"
        assert "沙箱" in spec.description
        assert "team.weekly-report" in spec.description
        # 沙箱内执行属受控写:自动执行,不再逐次人工确认
        assert spec.risk is ToolRisk.WRITE
        assert spec.requires_confirmation is False
        assert spec.parameters == {
            "type": "object",
            "properties": {
                "input": {
                    "type": "string",
                    "description": "可选的输入文本,经 stdin JSON 传给脚本",
                }
            },
            "additionalProperties": False,
        }


async def test_spec_handler_returns_error_text_instead_of_raising(
    tmp_path: Path,
) -> None:
    workspace = FakeWorkspace(fail=True)
    async with make_env(tmp_path, workspace=workspace) as env:
        spec = env.runner.spec(env.run)
        output = json.loads(await spec.handler({"input": "x"}))
        assert "技能包读取失败" in output["error"]
        assert env.sandbox.destroyed == ["sb-1"]


async def test_spec_handler_passes_input_to_stdin(tmp_path: Path) -> None:
    async with make_env(tmp_path) as env:
        spec = env.runner.spec(env.run)
        output = json.loads(await spec.handler({"input": "来自模型"}))
        assert output["exit_code"] == 0
        assert json.loads(env.sandbox.execs[1]["stdin"])["input"] == "来自模型"
