"""外链文件抓取(§7):工具产出的 http(s) 文件在执行时落成真实交付物。

零网络:下载器全部注入 FakeDownloader;断言覆盖"抓成功 / 抓失败不拖垮交付 /
开关关闭不抓"三条边界,以及多件交付物的列表与下载端点。
"""

from pathlib import Path

from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.services.file_capture import DownloadedFile, FakeDownloader
from agent_orchestrator.services.llm import LLMReply, ToolCall
from agent_orchestrator.services.tools import build_tools
from tests.conftest import (
    TENANT,
    FakeKnowledge,
    FakeLLM,
    _create_run,
    _settings,
    verifier,
)

FILE_URL = "https://files.example.com/report.pdf"
VERIFY_PASS = '{"summary": "验收通过", "verdicts": {"日程已整理": true}}'
DELIVERY = "# 本周周报\n\n- 日程已整理\n"


def _tool_reply() -> LLMReply:
    return LLMReply(
        content="",
        tool_calls=(
            ToolCall(
                id="call_1",
                name="search_knowledge",
                arguments='{"query": "本周日程"}',
            ),
        ),
    )


def _knowledge() -> FakeKnowledge:
    """命中里带上文件链接:这是"工具返回了可下载产物"的最小形态。"""
    return FakeKnowledge(
        hits=[
            {
                "title": "客户 A 合同",
                "score": 0.93,
                "content": f"合同评审安排在周四,附件:{FILE_URL}",
            }
        ]
    )


def _pdf() -> DownloadedFile:
    return DownloadedFile(
        filename="report.pdf", content_type="application/pdf", data=b"%PDF-1.4 report"
    )


def _app(
    tmp_path: Path,
    name: str,
    downloader: FakeDownloader | None,
    **overrides,
) -> TestClient:
    fake = FakeLLM(
        ["# 执行计划", _tool_reply(), "已检索到 1 条资料", VERIFY_PASS, DELIVERY]
    )
    app = create_app(
        _settings(tmp_path, name, llm_enabled=True, tools_enabled=True, **overrides),
        llm_client=fake,
        toolkit_factory=lambda tenant: build_tools(
            tenant, knowledge=_knowledge()
        ),
        token_verifier=verifier(),
    )
    if downloader is not None:
        # lifespan 里"测试注入优先":在 TestClient 进入前挂到 state 上
        app.state.artifact_downloader = downloader
    return TestClient(app)


def _deliver(client: TestClient) -> str:
    run_id = _create_run(client)
    client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
    response = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT)
    assert response.json()["status"] == "completed"
    return run_id


def _delivered(client: TestClient, run_id: str) -> dict:
    events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
    return next(e["payload"] for e in events if e["type"] == "run.delivered.v1")


def test_tool_file_url_is_captured_as_artifact(tmp_path: Path) -> None:
    downloader = FakeDownloader({FILE_URL: _pdf()})
    with _app(tmp_path, "capture.db", downloader) as client:
        run_id = _deliver(client)

        assert downloader.calls == [FILE_URL]
        artifacts = client.get(f"/api/v1/runs/{run_id}/artifacts", headers=TENANT).json()
        assert [row["source"] for row in artifacts] == ["report", "mcp"]
        pdf = artifacts[1]
        assert pdf["filename"] == "report.pdf"
        assert pdf["content_type"] == "application/pdf"
        assert pdf["size_bytes"] == len(b"%PDF-1.4 report")

        payload = _delivered(client, run_id)
        assert payload["download_errors"] == []
        assert len(payload["artifacts"]) == 2

        # 二进制件从下载端点取原始字节(不是被当文本塞进 content)
        response = client.get(
            f"/api/v1/runs/{run_id}/artifacts/{pdf['id']}/download", headers=TENANT
        )
        assert response.content == b"%PDF-1.4 report"
        assert response.headers["content-type"] == "application/pdf"
        assert "report.pdf" in response.headers["content-disposition"]


def test_download_failure_is_recorded_not_fatal(tmp_path: Path) -> None:
    downloader = FakeDownloader({FILE_URL: RuntimeError("下载通道挂了")})
    with _app(tmp_path, "capture-fail.db", downloader) as client:
        run_id = _deliver(client)

        # 抓取失败不拖垮交付:run 仍 completed,报告件照常有,失败如实记账
        artifacts = client.get(f"/api/v1/runs/{run_id}/artifacts", headers=TENANT).json()
        assert [row["source"] for row in artifacts] == ["report"]
        payload = _delivered(client, run_id)
        errors = payload["download_errors"]
        assert len(errors) == 1
        assert errors[0]["url"] == FILE_URL
        assert "下载通道挂了" in errors[0]["error"]


def test_download_disabled_skips_capture(tmp_path: Path) -> None:
    # 开关关闭时不装配下载器(测试不注入,验证"不抓"的默认行为)
    with _app(
        tmp_path, "capture-off.db", None, artifact_download_enabled=False
    ) as client:
        run_id = _deliver(client)

        artifacts = client.get(f"/api/v1/runs/{run_id}/artifacts", headers=TENANT).json()
        assert [row["source"] for row in artifacts] == ["report"]
        assert _delivered(client, run_id)["download_errors"] == []
