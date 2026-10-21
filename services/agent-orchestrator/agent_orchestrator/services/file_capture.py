"""外链文件抓取(把消息/技能输入里的 http(s) 链接下载成可登记的交付物字节)。

只做两件事:从一段文本里认出"看起来是文件"的 URL(按扩展名白名单),以及把
单个 URL 安全地下载成 bytes。三条边界:

1. **白名单**:只有已知文件扩展名才抓,避免把普通网页链接当文件;
2. **SSRF 收口**:先解析主机名,命中私网/回环/链路本地/保留/组播/未指定地址
   一律拒绝(除非 host 在 allowed_hosts 里显式放行);
3. **规模收口**:Content-Length 与实际读取长度都按 max_bytes 截停,不跟随
   重定向(避免绕过白名单的二次跳转),网络异常统一转 UpstreamError。

下载器是协议(FileDownloader),测试注入 FakeDownloader/MockTransport,零网络。
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import ParseResult, unquote, urlparse

import httpx
from common.errors import UpstreamError

#: 允许抓取的扩展名 → 约定 MIME(用于响应头缺失时兜底)
FILE_EXTENSIONS: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ),
    ".zip": "application/zip",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
    ".html": "text/html",
    ".htm": "text/html",
    ".tsv": "text/tab-separated-values",
}

DEFAULT_CONTENT_TYPE = "application/octet-stream"
#: 文件名净化后的最大长度(与交付物 title 的既有口径一致)
FILENAME_LIMIT = 200

_URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#@!$&*+,;=%()]+", re.IGNORECASE)
#: URL 尾部常带的中英文标点(正文里 URL 后接句号/括号/引号很常见)
_TRAILING = ".,;:!?)]}'\"》」』】、。，;:!?)"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_FILENAME_STAR = re.compile(r"filename\*\s*=\s*([^;]+)", re.IGNORECASE)
_FILENAME = re.compile(r"filename\s*=\s*(\"[^\"]*\"|[^;]*)")


@dataclass(frozen=True)
class FileRef:
    """文本里认出的一条待抓取文件链接。"""

    url: str
    filename: str
    content_type: str | None = None


@dataclass(frozen=True)
class DownloadedFile:
    """下载结果(字节 + 最终文件名/类型)。"""

    filename: str
    content_type: str
    data: bytes


class FileDownloader(Protocol):
    """下载器协议:上层只依赖 fetch,测试注入 FakeDownloader,零网络。"""

    async def fetch(self, url: str) -> DownloadedFile: ...


def sanitize_filename(name: str, suffix: str = "") -> str:
    """净化文件名:去路径分隔符与控制字符,截断;空则按扩展名兜底。"""
    cleaned = unquote(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _CONTROL.sub("", cleaned).strip().strip(" .")
    if len(cleaned) > FILENAME_LIMIT:
        cleaned = cleaned[:FILENAME_LIMIT]
    # 只有扩展名(如 URL 末段是 ".pdf")时按扩展名兜底,避免文件名裸成 "pdf"
    if not cleaned or cleaned.lower() == suffix.lstrip(".").lower():
        cleaned = f"file{suffix}" if suffix else "file"
    return cleaned


def extract_file_urls(text: str, *, limit: int = 10) -> list[FileRef]:
    """按扩展名白名单提取文本里的 http(s) 文件链接(去重保序,最多 limit 条)。"""
    if not text or limit <= 0:
        return []
    refs: list[FileRef] = []
    seen: set[str] = set()
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(_TRAILING)
        if not url or url in seen:
            continue
        parsed = urlparse(url)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
            continue
        path = parsed.path or ""
        suffix = _suffix(path)
        if suffix not in FILE_EXTENSIONS:
            continue
        seen.add(url)
        refs.append(
            FileRef(
                url=url,
                filename=sanitize_filename(path.rsplit("/", 1)[-1], suffix),
                content_type=FILE_EXTENSIONS[suffix],
            )
        )
        if len(refs) >= limit:
            break
    return refs


def _suffix(path: str) -> str:
    """取路径扩展名(小写);没有则返回空串。"""
    name = path.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return name[dot:].lower() if dot >= 0 else ""


def _parse_content_disposition(value: str) -> str | None:
    """从 Content-Disposition 取文件名(RFC 5987 的 filename* 优先)。"""
    match = _FILENAME_STAR.search(value)
    if match:
        raw = match.group(1).strip().strip("\"'")
        _, separator, rest = raw.partition("''")
        if separator:
            return sanitize_filename(unquote(rest), _suffix(rest))
        return sanitize_filename(unquote(raw))
    match = _FILENAME.search(value)
    if match:
        return sanitize_filename(match.group(1).strip().strip('"'), "")
    return None


def _guess_content_type(filename: str) -> str:
    """按扩展名猜 MIME(只认白名单内的扩展名)。"""
    return FILE_EXTENSIONS.get(_suffix(filename), DEFAULT_CONTENT_TYPE)


def _resolve_sync(host: str) -> list[str]:
    """同步解析主机名(在线程里跑,避免阻塞事件循环)。"""
    infos = socket.getaddrinfo(host, None)
    return [str(info[4][0]) for info in infos]


def _is_blocked_address(address: str) -> bool:
    """私网/回环/链路本地/保留/组播/未指定 → True(SSRF 收口)。"""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True  # 解析不到具体 IP 的一律不信
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


class HttpFileDownloader:
    """httpx 实现:白名单 host 可访问私网,其余先过 SSRF 检查再下载。"""

    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        max_bytes: int = 10 * 1024 * 1024,
        allowed_hosts: Sequence[str] = (),
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Callable[[str], list[str]] | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._max_bytes = max_bytes
        self._allowed_hosts = {host.lower() for host in allowed_hosts}
        self._transport = transport
        self._resolver = resolver

    async def _resolve(self, host: str) -> list[str]:
        """解析主机名;默认走线程里的 getaddrinfo(可注入假 resolver)。"""
        if self._resolver is not None:
            return list(self._resolver(host))
        return await asyncio.to_thread(_resolve_sync, host)

    async def _check_target(self, host: str) -> None:
        """SSRF 检查:allowed_hosts 显式放行,否则任一解析结果命中内网即拒绝。"""
        if not host:
            raise UpstreamError(
                "artifact.download_invalid", "链接缺少主机名", details={"host": host}
            )
        normalized = host.lower()
        if normalized in self._allowed_hosts:
            return
        try:
            addresses = await self._resolve(host)
        except OSError as exc:
            raise UpstreamError(
                "artifact.download_failed",
                f"域名解析失败:{host}",
                details={"host": host, "error": str(exc)},
            ) from exc
        if not addresses:
            raise UpstreamError(
                "artifact.download_failed",
                f"域名没有解析结果:{host}",
                details={"host": host},
            )
        blocked = [address for address in addresses if _is_blocked_address(address)]
        if blocked:
            raise UpstreamError(
                "artifact.download_blocked",
                f"目标地址指向内网,已拒绝下载:{host}",
                details={"host": host, "addresses": blocked},
            )

    async def fetch(self, url: str) -> DownloadedFile:
        """下载单个链接:校验协议/目标 → 流式读取(带长度上限)→ 组装结果。"""
        parsed = urlparse(url)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
            raise UpstreamError(
                "artifact.download_invalid",
                f"只支持 http(s) 链接:{url}",
                details={"url": url},
            )
        await self._check_target(parsed.hostname or "")
        timeout = httpx.Timeout(self._timeout, connect=min(5.0, self._timeout))
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            transport=self._transport,
        ) as client:
            try:
                async with client.stream("GET", url) as response:
                    return await self._read_response(response, parsed)
            except httpx.HTTPError as exc:
                raise UpstreamError(
                    "artifact.download_failed",
                    f"下载失败:{url}",
                    details={"url": url, "error": f"{type(exc).__name__}: {exc}"},
                ) from exc

    async def _read_response(
        self, response: httpx.Response, parsed: ParseResult
    ) -> DownloadedFile:
        """读响应体:先看状态码,再按 Content-Length/实际长度双上限截停。"""
        if 300 <= response.status_code < 400:
            raise UpstreamError(
                "artifact.download_redirect",
                f"不跟随重定向:{response.status_code}",
                details={
                    "status": response.status_code,
                    "location": response.headers.get("location", ""),
                },
            )
        if response.status_code >= 400:
            raise UpstreamError(
                "artifact.download_failed",
                f"下载失败:HTTP {response.status_code}",
                details={"status": response.status_code},
            )
        declared = response.headers.get("content-length")
        if declared is not None:
            try:
                declared_size = int(declared)
            except ValueError:
                declared_size = 0
            if declared_size > self._max_bytes:
                raise UpstreamError(
                    "artifact.too_large",
                    f"文件超过上限:{declared_size} > {self._max_bytes}",
                    details={"size": declared_size, "max_bytes": self._max_bytes},
                )
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > self._max_bytes:
                raise UpstreamError(
                    "artifact.too_large",
                    f"文件超过上限(实际已读 {total} 字节)",
                    details={"max_bytes": self._max_bytes},
                )
            chunks.append(chunk)
        data = b"".join(chunks)
        disposition = response.headers.get("content-disposition", "")
        url_path = getattr(parsed, "path", "") or ""
        suffix = _suffix(url_path)
        filename = (
            _parse_content_disposition(disposition) if disposition else None
        ) or sanitize_filename(url_path.rsplit("/", 1)[-1], suffix)
        header_type = (response.headers.get("content-type") or "").split(";")[0].strip()
        return DownloadedFile(
            filename=filename,
            content_type=header_type or _guess_content_type(filename),
            data=data,
        )


class FakeDownloader:
    """测试替身:按 URL 返回预置结果,或抛出预置异常;记录调用次序。"""

    def __init__(
        self,
        responses: dict[str, DownloadedFile | Exception] | None = None,
        *,
        default: DownloadedFile | Exception | None = None,
    ) -> None:
        self.responses = dict(responses or {})
        self.default = default
        self.calls: list[str] = []

    async def fetch(self, url: str) -> DownloadedFile:
        self.calls.append(url)
        result = self.responses.get(url, self.default)
        if result is None:
            raise UpstreamError(
                "artifact.download_failed",
                f"FakeDownloader 没有为 {url} 预置结果",
                details={"url": url},
            )
        if isinstance(result, Exception):
            raise result
        return result
