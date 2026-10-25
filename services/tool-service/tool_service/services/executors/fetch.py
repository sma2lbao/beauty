"""fetch 执行器:只读抓取公网文本(SSRF 防护 + 大小/跳转上限)。

与知识库检索不同,fetch 不代理任何下游服务,直接由本服务发起 HTTP GET:
因此必须自己守住边界(设计「工具服务安全边界」):

- 只允许 http/https,拒绝 URL 内置用户名/密码;
- 解析出的**所有**地址必须是公网单播,内网/回环/链路本地/保留段一律拒绝
  (防 SSRF 打内网服务或云元数据);
- 每一跳重定向都重新做地址校验(防「公网 302 到 127.0.0.1」绕过);
- 只接受文本型 content-type(HTML/JSON/XML/Markdown/纯文本),不下载图片与
  二进制;响应体按字节上限截断,文本按字符上限截断;
- 可选主机允许列表(空 = 任意公网主机),供部署侧收紧出口。

错误都是模型可见的数据(tool.* 信封里的 code),不是 API 错误。
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

#: 结果文本上限(低于 MAX_TOOL_CHARS=25000,给信封字段留余量)。
MAX_FETCH_TEXT_CHARS = 20_000
#: 单次抓取字节上限(超出截断并标记 truncated,不报错)。
DEFAULT_FETCH_MAX_BYTES = 1024 * 1024
#: 默认超时;实际请求超时由 descriptor.timeout_seconds 与执行编排兜底。
DEFAULT_FETCH_TIMEOUT_SECONDS = 15.0
#: 默认最多跟随的重定向跳数。
DEFAULT_FETCH_MAX_REDIRECTS = 3
#: 视为文本的 content-type(前缀匹配);其余按二进制拒绝。
TEXT_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/ld+json",
    "application/xml",
    "application/xhtml+xml",
    "application/rss+xml",
    "application/atom+xml",
    "application/yaml",
    "application/x-yaml",
    "application/toml",
)
#: 允许的 URL scheme。
ALLOWED_SCHEMES = ("http", "https")
#: 默认 UA(带产品标识,便于对方站点识别与限流沟通)。
USER_AGENT = "beauty-moon-tool-service/0.1 (+fetch)"
#: 重定向状态码。
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
#: content-type 里的 charset 参数。
_CHARSET_RE = re.compile(r"charset=([A-Za-z0-9_.:-]+)", re.IGNORECASE)
#: 错误响应体截断长度(只进 hint)。
ERROR_BODY_CHARS = 300

#: 主机解析器:host, port → 地址列表(测试可注入,避免真实 DNS)。
Resolver = Callable[[str, int], Awaitable[list[str]]]


class FetchError(Exception):
    """抓取失败;code 直接作为工具错误码(数据类错误,由执行器转信封)。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        hint: str = "",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.retryable = retryable


def is_public_address(raw: str) -> bool:
    """判断地址是否为公网单播:内网/回环/链路本地/组播/保留/未指定一律拒绝。"""

    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped is not None:
        return is_public_address(str(address.ipv4_mapped))
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


async def default_resolver(host: str, port: int) -> list[str]:
    """用系统解析器把主机名解析成地址列表(去重,保持顺序)。"""

    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses: list[str] = []
    for info in infos:
        address = info[4][0]
        if address not in addresses:
            addresses.append(address)
    return addresses


def _charset(content_type: str) -> str | None:
    """从 content-type 里取 charset(小写);没有则 None。"""

    match = _CHARSET_RE.search(content_type)
    return match.group(1).lower() if match else None


def _is_text(content_type: str) -> bool:
    """content-type 是否属于可当文本返回的类型;空值按文本处理。"""

    normalized = content_type.strip().lower()
    if not normalized:
        return True
    return any(
        normalized.startswith(prefix) or normalized.split(";")[0].strip() == prefix
        for prefix in TEXT_CONTENT_TYPES
    )


def parse_target(url: str) -> tuple[str, str, int]:
    """校验并解析 URL,返回 (scheme, host, port);非法 URL 抛 FetchError。"""

    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise FetchError("tool.invalid_args", f"URL 不合法:{exc}") from exc
    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise FetchError("tool.invalid_args", "只允许抓取 http/https URL")
    if parts.username or parts.password:
        raise FetchError("tool.invalid_args", "URL 不允许携带用户名或密码")
    host = parts.hostname
    if not host:
        raise FetchError("tool.invalid_args", "URL 缺少主机名")
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise FetchError("tool.invalid_args", f"URL 端口不合法:{exc}") from exc
    return scheme, host.lower(), port


class FetchClient:
    """只读抓取客户端;测试可注入 transport 与 resolver(零网络)。"""

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_FETCH_TIMEOUT_SECONDS,
        max_bytes: int = DEFAULT_FETCH_MAX_BYTES,
        max_redirects: int = DEFAULT_FETCH_MAX_REDIRECTS,
        allowed_hosts: tuple[str, ...] | list[str] = (),
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds,
            transport=transport,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
        )
        self._max_bytes = max(1, max_bytes)
        self._max_redirects = max(0, max_redirects)
        self._allowed_hosts = tuple(
            host.strip().lower().lstrip(".")
            for host in allowed_hosts
            if host and host.strip()
        )
        self._resolver: Resolver = resolver or default_resolver
        self._timeout_seconds = timeout_seconds

    async def aclose(self) -> None:
        """关闭底层 HTTP 客户端。"""

        await self._client.aclose()

    def _check_allowed_host(self, host: str) -> None:
        """主机允许列表校验(空列表 = 任意公网主机)。"""

        if not self._allowed_hosts:
            return
        for allowed in self._allowed_hosts:
            if host == allowed or host.endswith("." + allowed):
                return
        raise FetchError(
            "tool.fetch_blocked",
            f"目标主机 {host} 不在允许列表内",
            hint=f"允许的主机:{', '.join(self._allowed_hosts)}",
        )

    async def _check_public(self, host: str, port: int) -> None:
        """允许列表 + 公网地址校验(解析失败按不可达处理)。"""

        self._check_allowed_host(host)
        try:
            addresses = await self._resolver(host, port)
        except OSError as exc:
            raise FetchError(
                "tool.fetch_blocked",
                f"无法解析目标主机 {host}",
                hint=str(exc),
                retryable=True,
            ) from exc
        if not addresses:
            raise FetchError(
                "tool.fetch_blocked", f"无法解析目标主机 {host}", retryable=True
            )
        if not all(is_public_address(address) for address in addresses):
            raise FetchError(
                "tool.fetch_blocked",
                f"拒绝访问非公网地址:{host}",
                hint="内网、回环、链路本地与保留地址不允许抓取",
            )

    async def get(self, url: str) -> dict[str, Any]:
        """抓取一个 URL;重定向逐跳重新校验地址(防 SSRF 绕过)。"""

        original = url
        target = url
        for _ in range(self._max_redirects + 1):
            _, host, port = parse_target(target)
            await self._check_public(host, port)
            status, location, content_type, body, bytes_truncated = await self._request(
                target
            )
            if status in REDIRECT_STATUSES and location:
                target = urljoin(target, location)
                continue
            break
        else:
            raise FetchError(
                "tool.too_many_redirects",
                f"重定向超过 {self._max_redirects} 跳",
                hint="直接抓取最终地址,或换成 raw 链接",
            )

        if status == 404:
            raise FetchError(
                "tool.not_found",
                "目标返回 404(不存在或无权限)",
                hint=_body_hint(body, content_type),
            )
        if 400 <= status < 500:
            raise FetchError(
                "tool.fetch_failed",
                f"目标返回 {status}",
                hint=_body_hint(body, content_type),
            )
        if status >= 500:
            raise FetchError(
                "tool.fetch_failed",
                f"目标返回 {status}",
                hint=_body_hint(body, content_type),
                retryable=True,
            )
        if not _is_text(content_type):
            raise FetchError(
                "tool.unsupported_content",
                f"目标不是文本内容({content_type.split(';')[0].strip() or '未知类型'})",
                hint="fetch 只读文本(HTML/JSON/XML/Markdown/纯文本),不下载图片与二进制",
            )

        text = _decode(body, content_type)
        text_truncated = len(text) > MAX_FETCH_TEXT_CHARS
        if text_truncated:
            text = text[:MAX_FETCH_TEXT_CHARS]
        return {
            "url": original,
            "final_url": target,
            "status": status,
            "content_type": content_type,
            "bytes": len(body),
            "truncated": bool(bytes_truncated or text_truncated),
            "text": text,
        }

    async def _request(self, url: str) -> tuple[int, str | None, str, bytes, bool]:
        """发一次 GET,按字节上限截断响应体,返回 (status, location, ct, body, 截断)。"""

        async with self._client.stream("GET", url) as response:
            content_type = response.headers.get("content-type", "")
            location = response.headers.get("location")
            buffer = bytearray()
            truncated = False
            async for chunk in response.aiter_bytes():
                remaining = self._max_bytes - len(buffer)
                if remaining <= 0:
                    truncated = True
                    break
                if len(chunk) > remaining:
                    buffer += chunk[:remaining]
                    truncated = True
                    break
                buffer += chunk
            return (
                response.status_code,
                location,
                content_type,
                bytes(buffer),
                truncated,
            )


def _decode(body: bytes, content_type: str) -> str:
    """按 charset(缺省 utf-8)解码;非法字节用替换符,不抛异常。"""

    encoding = _charset(content_type) or "utf-8"
    try:
        return body.decode(encoding, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _body_hint(body: bytes, content_type: str) -> str:
    """把错误响应体压成一行短提示(截断,避免撑爆信封)。"""

    text = " ".join(_decode(body[:4096], content_type).split())
    return text[:ERROR_BODY_CHARS]
