"""外链文件抓取:URL 白名单识别 + 下载器(SSRF/规模/重定向收口)的聚焦测试。

零网络:下载全部走 httpx.MockTransport + 注入 resolver,不解析真实域名。
"""

from __future__ import annotations

import httpx
import pytest
from common.errors import UpstreamError

from agent_orchestrator.services.file_capture import (
    FILE_EXTENSIONS,
    DownloadedFile,
    FakeDownloader,
    FileRef,
    HttpFileDownloader,
    extract_file_urls,
    sanitize_filename,
)

# ---------------------------------------------------------------------------
# extract_file_urls
# ---------------------------------------------------------------------------


def test_extract_keeps_only_whitelisted_extensions() -> None:
    text = (
        "看这份报告 https://example.com/reports/q3.pdf 还有截图 "
        "https://cdn.example.com/img/a.png,以及网页 https://example.com/news/2024"
    )

    refs = extract_file_urls(text)

    assert [ref.url for ref in refs] == [
        "https://example.com/reports/q3.pdf",
        "https://cdn.example.com/img/a.png",
    ]
    assert refs[0].filename == "q3.pdf"
    assert refs[0].content_type == "application/pdf"
    assert refs[1].content_type == "image/png"


def test_extract_strips_trailing_punctuation_and_quotes() -> None:
    text = (
        "下载「https://example.com/a.zip」,或 (https://example.com/b.xlsx)。"
        "结束 https://example.com/c.md;"
    )

    urls = [ref.url for ref in extract_file_urls(text)]

    assert urls == [
        "https://example.com/a.zip",
        "https://example.com/b.xlsx",
        "https://example.com/c.md",
    ]


def test_extract_dedupes_preserving_order_and_honours_limit() -> None:
    text = " ".join(
        [
            "https://example.com/a.txt",
            "https://example.com/b.txt",
            "https://example.com/a.txt",
            "https://example.com/c.txt",
            "https://example.com/d.txt",
        ]
    )

    refs = extract_file_urls(text, limit=3)

    assert [ref.url for ref in refs] == [
        "https://example.com/a.txt",
        "https://example.com/b.txt",
        "https://example.com/c.txt",
    ]


def test_extract_decodes_chinese_filename_and_falls_back() -> None:
    text = (
        "https://example.com/files/%E5%91%A8%E6%8A%A5.pdf "
        "https://example.com/files/has%2Fslash.png"
    )

    refs = extract_file_urls(text)

    assert refs[0].filename == "周报.pdf"
    # 百分号编码的 / 会被解码后当路径分隔符,只留末段
    assert refs[1].filename == "slash.png"


def test_extract_ignores_non_http_and_empty_text() -> None:
    assert extract_file_urls("") == []
    assert extract_file_urls("ftp://example.com/a.pdf file:///etc/passwd") == []
    assert extract_file_urls("https://example.com/", limit=0) == []


def test_extract_empty_basename_falls_back_to_extension() -> None:
    refs = extract_file_urls("https://example.com/download/.pdf")

    assert refs[0].filename == "file.pdf"


def test_sanitize_filename_removes_path_and_control_chars() -> None:
    assert sanitize_filename("../../etc/passwd.md") == "passwd.md"
    assert sanitize_filename("bad\x00name.txt") == "badname.txt"
    assert sanitize_filename("", ".png") == "file.png"
    assert len(sanitize_filename("x" * 500 + ".txt")) == 200


# ---------------------------------------------------------------------------
# HttpFileDownloader(MockTransport + 注入 resolver,零网络)
# ---------------------------------------------------------------------------


def _public_resolver(host: str) -> list[str]:
    """默认测试 resolver:域名都当作公网地址(93.184.216.34 = example.com)。"""
    return ["93.184.216.34"]


def _transport(
    handler, calls: list[httpx.Request] | None = None
) -> httpx.MockTransport:
    """包一层 MockTransport,顺带记录请求(断言"没有发出请求"用)。"""

    def _handle(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        return handler(request)

    return httpx.MockTransport(_handle)


def _downloader(
    handler, *, max_bytes: int = 1024, resolver=_public_resolver, allowed_hosts=()
) -> HttpFileDownloader:
    return HttpFileDownloader(
        timeout_seconds=5.0,
        max_bytes=max_bytes,
        allowed_hosts=allowed_hosts,
        transport=_transport(handler),
        resolver=resolver,
    )


async def test_fetch_returns_bytes_and_content_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://example.com/files/a.pdf"
        return httpx.Response(
            200,
            content=b"%PDF-1.4",
            headers={
                "content-type": "application/pdf; charset=binary",
                "content-disposition": 'attachment; filename="q3.pdf"',
            },
        )

    result = await _downloader(handler).fetch("https://example.com/files/a.pdf")

    assert result == DownloadedFile(
        filename="q3.pdf", content_type="application/pdf", data=b"%PDF-1.4"
    )


async def test_fetch_prefers_rfc5987_filename_star() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"ok",
            headers={
                "content-disposition": (
                    'attachment; filename="fallback.txt"; '
                    "filename*=UTF-8''%E5%91%A8%E6%8A%A5.txt"
                )
            },
        )

    result = await _downloader(handler).fetch("https://example.com/x")

    assert result.filename == "周报.txt"
    # 响应头没有 content-type 时按扩展名兜底
    assert result.content_type == "text/plain"


async def test_fetch_blocks_loopback_target_without_sending_request() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("不应该发出请求")

    downloader = _downloader(
        handler, resolver=lambda host: ["127.0.0.1"], allowed_hosts=()
    )
    with pytest.raises(UpstreamError) as excinfo:
        await downloader.fetch("http://internal.example.com/secret.pdf")

    assert excinfo.value.code == "artifact.download_blocked"
    assert calls == []


async def test_fetch_blocks_metadata_address() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("不应该发出请求")

    downloader = _downloader(handler, resolver=lambda host: ["169.254.169.254"])
    with pytest.raises(UpstreamError) as excinfo:
        await downloader.fetch("http://metadata.example.com/latest/meta-data.json")

    assert excinfo.value.code == "artifact.download_blocked"
    assert excinfo.value.details["addresses"] == ["169.254.169.254"]


async def test_fetch_allows_explicitly_allowed_host() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"hi", headers={"content-type": "text/plain"}
        )

    downloader = _downloader(
        handler,
        resolver=lambda host: ["127.0.0.1"],
        allowed_hosts=["internal.example.com"],
    )

    result = await downloader.fetch("http://internal.example.com/a.txt")

    assert result.data == b"hi"


async def test_fetch_rejects_oversized_body_by_actual_length() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"x" * 64, headers={"content-type": "text/plain"}
        )

    with pytest.raises(UpstreamError) as excinfo:
        await _downloader(handler, max_bytes=16).fetch("https://example.com/big.txt")

    assert excinfo.value.code == "artifact.too_large"


async def test_fetch_rejects_oversized_declared_content_length() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x",
            headers={"content-length": "999", "content-type": "text/plain"},
        )

    with pytest.raises(UpstreamError) as excinfo:
        await _downloader(handler, max_bytes=16).fetch("https://example.com/big.txt")

    assert excinfo.value.code == "artifact.too_large"
    assert excinfo.value.details["size"] == 999


async def test_fetch_does_not_follow_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://127.0.0.1/x"})

    with pytest.raises(UpstreamError) as excinfo:
        await _downloader(handler).fetch("https://example.com/redir.pdf")

    assert excinfo.value.code == "artifact.download_redirect"
    assert excinfo.value.details["location"] == "http://127.0.0.1/x"


async def test_fetch_wraps_network_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(UpstreamError) as excinfo:
        await _downloader(handler).fetch("https://example.com/a.pdf")

    assert excinfo.value.code == "artifact.download_failed"
    assert "ConnectError" in excinfo.value.details["error"]


async def test_fetch_rejects_non_http_scheme() -> None:
    with pytest.raises(UpstreamError) as excinfo:
        await HttpFileDownloader(resolver=_public_resolver).fetch("file:///etc/passwd")

    assert excinfo.value.code == "artifact.download_invalid"


async def test_fetch_uses_header_content_type_when_present() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"{}", headers={"content-type": "application/json"}
        )

    result = await _downloader(handler).fetch("https://example.com/data")

    assert result.content_type == "application/json"
    assert result.filename == "data"


# ---------------------------------------------------------------------------
# FakeDownloader
# ---------------------------------------------------------------------------


async def test_fake_downloader_returns_or_raises_per_url() -> None:
    good = DownloadedFile("a.png", "image/png", b"png")
    boom = UpstreamError("artifact.download_failed", "挂了")
    downloader = FakeDownloader(
        {"https://example.com/a.png": good, "https://example.com/b.png": boom}
    )

    assert await downloader.fetch("https://example.com/a.png") == good
    with pytest.raises(UpstreamError):
        await downloader.fetch("https://example.com/b.png")
    with pytest.raises(UpstreamError):
        await downloader.fetch("https://example.com/unknown.png")
    assert downloader.calls == [
        "https://example.com/a.png",
        "https://example.com/b.png",
        "https://example.com/unknown.png",
    ]


def test_file_extension_table_covers_expected_types() -> None:
    assert set(FILE_EXTENSIONS) >= {
        ".png",
        ".pdf",
        ".csv",
        ".xlsx",
        ".zip",
        ".md",
    }
    assert extract_file_urls("https://example.com/a.tsv")[0].content_type == (
        "text/tab-separated-values"
    )
    assert isinstance(FileRef("u", "f"), FileRef)
