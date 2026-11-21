"""sandboxd provider:gVisor + Docker 远端沙箱(REST 控制面)。

sandboxd 部署在另一台机器上,通过 HTTP 管理沙箱生命周期与命令执行。
本实现对应 sandboxd 的 `/v1/sandboxes` REST API:

- `POST /v1/sandboxes` 创建;`DELETE /v1/sandboxes/{id}` 销毁;
- `POST /v1/sandboxes/{id}/exec` 执行(argv 数组,cwd/env/timeout);
- `PUT|GET|DELETE /v1/sandboxes/{id}/files?path=` 文件读写;
- `GET /v1/sandboxes/{id}/files/list?path=` 列目录。

与 E2B provider 的差异:

- sandboxd 的 PTY 通过 WebSocket 提供(/v1/sandboxes/{id}/pty),
  open_shell 可用;但协议没有可重连的 session id/pid,attach_shell 仍抛
  sandbox.provider_unsupported,由上层把会话标记失联;
- exec 没有 `stdin` 字段,`run_code` 需要的标准输入用「临时文件 + /bin/sh
  重定向」模拟,执行结束即删除;
- 控制面没有 metadata,创建时把沙箱名写进 `owner`;服务重启后按 `owner`
  从 `GET /v1/sandboxes` 查回 id。
"""

from __future__ import annotations

import logging
import os
import shlex
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, NoReturn
from urllib.parse import quote, urlencode
from uuid import uuid4

import httpx
from common.errors import NotFoundError, UpstreamError

from workspace.services.sandbox_provider.protocols import (
    DEFAULT_EXEC_TIMEOUT_SECONDS,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_SHELL_COLS,
    DEFAULT_SHELL_ROWS,
    ExecResult,
    FileEntry,
    SandboxSpec,
    ShellSession,
)
from workspace.services.sandbox_provider.sandboxd_shell import SandboxdShellSession
from workspace.services.sandbox_provider.utils import (
    MAX_ERROR_DETAIL_CHARS,
    _error_details,
    _truncate_text,
)

logger = logging.getLogger(__name__)

# 单次目录列举最多收集多少条,避免远端返回超大目录时把内存打满;
# 业务层最终还会按 500 条截断。
_MAX_LIST_ENTRIES = 5_000
# 文件上传/下载给足时间(单次 stdin 上限 1MB,普通文件更大)
_FILE_TRANSFER_TIMEOUT_SECONDS = 120.0
# microsandbox 的镜像别名不是合法 Docker 镜像名,创建时换成 sandboxd 默认镜像
_IMAGE_ALIASES = {"python"}
_ENTRY_KINDS = {
    "file": "file",
    "directory": "dir",
    "dir": "dir",
    "symlink": "symlink",
    "link": "symlink",
}
_META_NAME = "beauty_sandbox_name"


def _extract_id(payload: Any) -> str | None:
    """从创建/列举响应里取沙箱 id,兼容常见包装形状。"""
    if not isinstance(payload, dict):
        return None
    for key in ("id", "sandbox_id"):
        value = payload.get(key)
        if value:
            return str(value)
    for key in ("sandbox", "data", "item"):
        found = _extract_id(payload.get(key))
        if found:
            return found
    return None


def _as_list(payload: Any) -> list[dict[str, Any]]:
    """把列举响应归一成条目列表,兼容 list / {sandboxes|items|data|results}。"""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("sandboxes", "items", "data", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _as_list(value)
            if nested:
                return nested
    # 少数实现会直接返回单个沙箱对象
    return [payload] if _extract_id(payload) else []


def _as_file_items(payload: Any) -> list[dict[str, Any]]:
    """把 files/list 响应归一成条目列表。"""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("entries", "files", "items", "children", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _as_file_items(value)
            if nested:
                return nested
    return []


def _pty_command(env: dict[str, str] | None) -> str:
    """拼出 sandboxd PTY 的启动命令;环境变量按 shell 安全方式转义。

    sandboxd 的 pty API 只接收一个 cmd 字符串,没有单独的 env 字段,所以用
    env KEY=VALUE ... /bin/sh -i 包装。选择 /bin/sh 是因为它几乎总在镜像里,
    不额外假设 bash 存在。
    """
    argv = ["/bin/sh", "-i"]
    if env:
        argv = ["env", *(f"{key}={value}" for key, value in env.items()), *argv]
    return shlex.join(argv)


def _websocket_status(exc: BaseException) -> int | None:
    """从 websockets 的握手异常里取 HTTP 状态码(取不到返回 None)。"""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return int(status) if isinstance(status, int) else None


def _sandbox_name(item: dict[str, Any]) -> str:
    """条目上的沙箱名:创建时写 owner,也兼容 name/metadata 形状。"""
    metadata = item.get("metadata")
    if isinstance(metadata, dict):
        for key in (_META_NAME, "name"):
            value = metadata.get(key)
            if value:
                return str(value)
    for key in ("owner", "name", "sandbox_name"):
        value = item.get(key)
        if value:
            return str(value)
    return ""


def _entry_path(item: dict[str, Any], parent: str) -> str:
    """条目在沙箱内的绝对路径;相对路径按列举目录归一。"""
    raw = str(item.get("path") or item.get("name") or "")
    if not raw or ".." in PurePosixPath(raw).parts:
        return ""
    candidate = raw if raw.startswith("/") else f"{parent.rstrip('/')}/{raw}"
    normalized = PurePosixPath(candidate).as_posix()
    return normalized if normalized.startswith("/") else ""


def _entry_kind(item: dict[str, Any], path: str) -> str:
    raw = item.get("kind") or item.get("type") or item.get("file_type")
    if isinstance(raw, dict):
        raw = raw.get("name") or raw.get("value")
    key = str(raw or "").lower()
    if key in _ENTRY_KINDS:
        return _ENTRY_KINDS[key]
    if item.get("is_symlink"):
        return "symlink"
    if item.get("is_dir"):
        return "dir"
    if path.endswith("/"):
        return "dir"
    return "other"


def _entry_size(item: dict[str, Any], kind: str) -> int:
    if kind == "dir":
        return 0
    for key in ("size", "size_bytes", "bytes"):
        value = item.get(key)
        if value is None:
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return 0


def _entry_modified(item: dict[str, Any]) -> float | None:
    for key in ("modified", "mtime", "modified_time"):
        value = item.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            if isinstance(value, str):
                with suppress(ValueError):
                    return datetime.fromisoformat(
                        value.replace("Z", "+00:00")
                    ).timestamp()
    return None


class SandboxdProvider:
    """sandboxd REST provider(gVisor + Docker,运行在另一台云服务器)。"""

    provider = "sandboxd"

    def __init__(
        self,
        *,
        base_url: str = "",
        token: str | None = None,
        default_image: str = "python:3.11-slim",
        request_timeout_seconds: float = 30.0,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        default_exec_timeout_seconds: int = DEFAULT_EXEC_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
        ws_connect: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self._base_url = (base_url or os.environ.get("SANDBOXD_URL") or "").rstrip("/")
        self._token = (token or os.environ.get("SANDBOXD_TOKEN") or "").strip()
        self._default_image = default_image.strip() or "python:3.11-slim"
        self._request_timeout_seconds = float(request_timeout_seconds)
        self._max_output_bytes = max_output_bytes
        self._default_exec_timeout_seconds = default_exec_timeout_seconds
        # 测试注入 MockTransport;生产为 None,httpx 走真实网络。
        self._transport = transport
        # 测试注入假 WebSocket;生产为 None,惰性导入 websockets.connect
        self._ws_connect = ws_connect
        # name → sandboxd id;进程重启后由 _resolve_id 从控制面查回
        self._ids: dict[str, str] = {}
        # name → 创建时的 TTL,exec 成功后用它向 sandboxd 续期
        self._ttls: dict[str, int] = {}

    def _require_config(self) -> None:
        if not self._base_url:
            raise UpstreamError(
                "sandbox.provider_unavailable",
                "未配置 sandboxd 地址:设置 WORKSPACE__SANDBOX_SANDBOXD_URL "
                "或 SANDBOXD_URL",
                details={"provider": "sandboxd"},
            )
        if not self._token:
            raise UpstreamError(
                "sandbox.provider_unavailable",
                "未配置 sandboxd token:设置 WORKSPACE__SANDBOX_SANDBOXD_TOKEN "
                "或 SANDBOXD_TOKEN",
                details={"provider": "sandboxd"},
            )

    def _image_for(self, spec: SandboxSpec) -> str:
        image = (spec.image or "").strip()
        if not image or image in _IMAGE_ALIASES:
            return self._default_image
        return image

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(
            transport=self._transport,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            yield client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        name: str,
        json_body: dict[str, Any] | None = None,
        content: bytes | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        operation: str = "generic",
        timeout_seconds: int | None = None,
    ) -> httpx.Response:
        """发一次请求;只把连接/超时异常收口,HTTP 状态由调用方映射。"""
        self._require_config()
        headers = {"Authorization": f"Bearer {self._token}"}
        if content is not None:
            headers["Content-Type"] = "application/octet-stream"
        request_timeout = timeout or self._request_timeout_seconds
        try:
            async with self._client() as client:
                return await client.request(
                    method,
                    f"{self._base_url}{path}",
                    headers=headers,
                    json=json_body,
                    content=content,
                    params=params,
                    timeout=request_timeout,
                )
        except httpx.TimeoutException as exc:
            if operation == "exec":
                raise UpstreamError(
                    "sandbox.exec_timeout",
                    f"沙箱命令执行超时({timeout_seconds or 0} 秒)",
                    details={
                        "sandbox_name": name,
                        "timeout_seconds": timeout_seconds,
                    },
                ) from exc
            raise UpstreamError(
                "sandbox.provider_error",
                "sandboxd 请求超时",
                details=_error_details(name, exc),
            ) from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(
                "sandbox.provider_error",
                "连接 sandboxd 失败",
                details=_error_details(name, exc),
            ) from exc

    async def _json(self, response: httpx.Response, *, name: str) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamError(
                "sandbox.provider_error",
                "sandboxd 返回了非 JSON 响应",
                details={
                    "sandbox_name": name,
                    "status": response.status_code,
                    "error": response.text[:MAX_ERROR_DETAIL_CHARS],
                },
            ) from exc

    @staticmethod
    def _status_details(name: str, response: httpx.Response) -> dict[str, Any]:
        detail: Any = response.text
        with suppress(ValueError):
            payload = response.json()
            if isinstance(payload, dict):
                detail = payload.get("detail") or payload.get("error") or payload
        return {
            "sandbox_name": name,
            "status": response.status_code,
            "error": str(detail)[:MAX_ERROR_DETAIL_CHARS],
        }

    def _raise_status(
        self,
        response: httpx.Response,
        name: str,
        *,
        not_found_message: str | None = None,
        not_found_path: str | None = None,
        timeout_message: str | None = None,
        timeout_seconds: int | None = None,
    ) -> NoReturn:
        status = response.status_code
        details = self._status_details(name, response)
        if status in (401, 403):
            raise UpstreamError(
                "sandbox.provider_unavailable",
                "sandboxd 鉴权失败:检查 token 与控制面地址",
                details=details,
            )
        if status == 404 and not_found_message is not None:
            raise NotFoundError(
                "sandbox.file_not_found",
                not_found_message,
                details={
                    "sandbox_name": name,
                    "path": not_found_path or "",
                },
            )
        if status == 404:
            raise UpstreamError(
                "sandbox.provider_error",
                "沙箱运行时实例不存在(可能已被回收)",
                details={"sandbox_name": name},
            )
        if status in (408, 504) and timeout_message is not None:
            raise UpstreamError(
                "sandbox.exec_timeout",
                timeout_message,
                details={
                    "sandbox_name": name,
                    "timeout_seconds": timeout_seconds,
                },
            )
        raise UpstreamError(
            "sandbox.provider_error",
            f"sandboxd 请求失败(HTTP {status})",
            details=details,
        )

    async def create(self, spec: SandboxSpec) -> None:
        ttl_seconds = max(1, int(spec.ttl_seconds))
        body = {
            "image": self._image_for(spec),
            "ttl_seconds": ttl_seconds,
            "network": spec.network == "public",
            "owner": spec.name,
            "memory_mb": int(spec.memory_mb),
            "cpus": float(spec.cpus),
        }
        response = await self._request(
            "POST",
            "/v1/sandboxes",
            name=spec.name,
            json_body=body,
            timeout=self._request_timeout_seconds,
        )
        if response.status_code >= 400:
            self._raise_status(response, spec.name)
        sandbox_id = _extract_id(await self._json(response, name=spec.name))
        if not sandbox_id:
            raise UpstreamError(
                "sandbox.provider_error",
                "sandboxd 创建沙箱未返回 id",
                details={"sandbox_name": spec.name},
            )
        self._ids[spec.name] = sandbox_id
        self._ttls[spec.name] = ttl_seconds

    async def _resolve_id(self, name: str) -> str | None:
        """先查进程缓存,再按 owner 从控制面查回;查不到返回 None。"""
        cached = self._ids.get(name)
        if cached:
            return cached
        response = await self._request(
            "GET", "/v1/sandboxes", name=name, timeout=self._request_timeout_seconds
        )
        if response.status_code >= 400:
            self._raise_status(response, name)
        for item in _as_list(await self._json(response, name=name)):
            if _sandbox_name(item) != name:
                continue
            sandbox_id = _extract_id(item)
            if sandbox_id:
                self._ids[name] = sandbox_id
                return sandbox_id
        return None

    async def _require_id(self, name: str) -> str:
        sandbox_id = await self._resolve_id(name)
        if sandbox_id is None:
            raise UpstreamError(
                "sandbox.provider_error",
                "沙箱运行时实例不存在(可能已被回收)",
                details={"sandbox_name": name},
            )
        return sandbox_id

    async def exec(
        self,
        name: str,
        argv: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout_seconds: int | None = None,
    ) -> ExecResult:
        sandbox_id = await self._require_id(name)
        timeout = int(
            timeout_seconds
            if timeout_seconds is not None
            else self._default_exec_timeout_seconds
        )
        if stdin is None:
            data = await self._exec_json(
                sandbox_id,
                argv,
                cwd=cwd,
                env=env,
                timeout=timeout,
                name=name,
            )
        else:
            data = await self._exec_with_stdin(
                sandbox_id,
                argv,
                stdin,
                cwd=cwd,
                env=env,
                timeout=timeout,
                name=name,
            )
        stdout, stdout_cut = _truncate_text(
            str(data.get("stdout") or ""), self._max_output_bytes
        )
        stderr, stderr_cut = _truncate_text(
            str(data.get("stderr") or ""), self._max_output_bytes
        )
        result = ExecResult(
            exit_code=int(data.get("exit_code", -1)),
            stdout=stdout,
            stderr=stderr,
            duration_ms=int(data.get("duration_ms") or 0),
            truncated=bool(data.get("truncated")) or stdout_cut or stderr_cut,
        )
        await self._extend_best_effort(name, sandbox_id)
        return result

    async def _exec_json(
        self,
        sandbox_id: str,
        argv: list[str],
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        stdin: str | None = None,
        timeout: int,
        name: str,
    ) -> dict[str, Any]:
        """POST exec;把超时/异步/detached 等异常形状收口成契约错误。"""
        body: dict[str, Any] = {
            "cmd": [str(part) for part in argv],
            "cwd": cwd or "/workspace",
            "env": dict(env or {}),
            "detach": False,
            "shell": False,
            "timeout_seconds": timeout,
        }
        response = await self._request(
            "POST",
            f"/v1/sandboxes/{sandbox_id}/exec",
            name=name,
            json_body=body,
            timeout=timeout + 20,
            operation="exec",
            timeout_seconds=timeout,
        )
        if response.status_code >= 400:
            self._raise_status(
                response,
                name,
                timeout_message=f"沙箱命令执行超时({timeout} 秒)",
                timeout_seconds=timeout,
            )
        data = await self._json(response, name=name)
        if not isinstance(data, dict):
            raise UpstreamError(
                "sandbox.provider_error",
                "sandboxd exec 返回了非对象响应",
                details={"sandbox_name": name},
            )
        if data.get("detached"):
            raise UpstreamError(
                "sandbox.provider_error",
                "sandboxd 返回了异步执行结果,无法同步取回",
                details={"sandbox_name": name},
            )
        if data.get("timed_out"):
            raise UpstreamError(
                "sandbox.exec_timeout",
                f"沙箱命令执行超时({timeout} 秒)",
                details={"sandbox_name": name, "timeout_seconds": timeout},
            )
        return data

    async def _exec_with_stdin(
        self,
        sandbox_id: str,
        argv: list[str],
        stdin: str,
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        timeout: int,
        name: str,
    ) -> dict[str, Any]:
        """sandboxd exec 没有 stdin 字段:先上传临时文件,再让 /bin/sh 重定向。"""
        temp_path = f"/tmp/.beauty-stdin-{uuid4().hex}"
        upload = await self._upload(
            sandbox_id, temp_path, stdin.encode("utf-8"), name=name
        )
        if upload.status_code >= 400:
            self._raise_status(upload, name)
        stdin_env = dict(env or {})
        stdin_env["BEAUTY_SANDBOX_STDIN"] = temp_path
        wrapper = [
            "/bin/sh",
            "-c",
            'exec "$@" < "$BEAUTY_SANDBOX_STDIN"',
            "beauty-stdin",
            *argv,
        ]
        try:
            return await self._exec_json(
                sandbox_id,
                wrapper,
                cwd=cwd,
                env=stdin_env,
                timeout=timeout,
                name=name,
            )
        finally:
            await self._delete_file_best_effort(sandbox_id, temp_path, name=name)

    async def _upload(
        self,
        sandbox_id: str,
        path: str,
        data: bytes,
        *,
        name: str,
    ) -> httpx.Response:
        return await self._request(
            "PUT",
            f"/v1/sandboxes/{sandbox_id}/files",
            name=name,
            content=data,
            params={"path": path},
            timeout=max(self._request_timeout_seconds, _FILE_TRANSFER_TIMEOUT_SECONDS),
        )

    async def _delete_file_best_effort(
        self, sandbox_id: str, path: str, *, name: str
    ) -> None:
        with suppress(Exception):
            await self._request(
                "DELETE",
                f"/v1/sandboxes/{sandbox_id}/files",
                name=name,
                params={"path": path},
                timeout=10.0,
            )

    async def _extend_best_effort(self, name: str, sandbox_id: str) -> None:
        """执行成功后尽力续期 sandboxd 侧 TTL;失败不影响已拿到的结果。"""
        ttl_seconds = self._ttls.get(name)
        if not ttl_seconds:
            return
        try:
            response = await self._request(
                "POST",
                f"/v1/sandboxes/{sandbox_id}/extend",
                name=name,
                json_body={"ttl_seconds": ttl_seconds},
                timeout=self._request_timeout_seconds,
            )
            if response.status_code == 404:
                self._ids.pop(name, None)
        except Exception as exc:  # noqa: BLE001 - 续期失败不能改变 exec 结果
            logger.debug("sandboxd 续期失败(%s): %s", name, exc)

    async def write_file(self, name: str, path: str, data: bytes) -> None:
        sandbox_id = await self._require_id(name)
        response = await self._upload(sandbox_id, path, data, name=name)
        if response.status_code < 400:
            return
        if response.status_code in (401, 403):
            self._raise_status(response, name)
        parent = str(PurePosixPath(path).parent)
        if parent in ("", ".", "/"):
            self._raise_status(response, name)
        try:
            await self._mkdir(sandbox_id, parent, name=name)
        except UpstreamError:
            # 建父目录也失败时,暴露原始写失败(更接近真实原因)
            self._raise_status(response, name)
        retry = await self._upload(sandbox_id, path, data, name=name)
        if retry.status_code >= 400:
            self._raise_status(retry, name)

    async def _mkdir(self, sandbox_id: str, path: str, *, name: str) -> None:
        data = await self._exec_json(
            sandbox_id,
            ["mkdir", "-p", path],
            cwd=None,
            env=None,
            timeout=15,
            name=name,
        )
        if int(data.get("exit_code", 0)) != 0:
            raise UpstreamError(
                "sandbox.provider_error",
                "创建沙箱内父目录失败",
                details={
                    "sandbox_name": name,
                    "path": path,
                    "error": str(data.get("stderr") or "")[:MAX_ERROR_DETAIL_CHARS],
                },
            )

    async def read_file(self, name: str, path: str) -> bytes:
        sandbox_id = await self._require_id(name)
        response = await self._request(
            "GET",
            f"/v1/sandboxes/{sandbox_id}/files",
            name=name,
            params={"path": path},
            timeout=max(self._request_timeout_seconds, _FILE_TRANSFER_TIMEOUT_SECONDS),
        )
        if response.status_code >= 400:
            self._raise_status(
                response,
                name,
                not_found_message=f"沙箱内文件不存在:{path}",
                not_found_path=path,
            )
        return response.content

    async def list_dir(
        self, name: str, path: str, *, depth: int = 1
    ) -> list[FileEntry]:
        sandbox_id = await self._require_id(name)
        depth = max(1, depth)
        base = path.rstrip("/") or "/"
        collected: dict[str, FileEntry] = {}
        queue: list[str] = [base]
        seen: set[str] = {base}
        while queue and len(collected) < _MAX_LIST_ENTRIES:
            current = queue.pop(0)
            try:
                items = await self._list_once(sandbox_id, current, name=name)
            except NotFoundError:
                if current == base:
                    raise
                continue
            for item in items:
                child = _entry_path(item, current)
                if not child or child == current:
                    continue
                if base != "/" and not child.startswith(f"{base}/"):
                    continue
                relative = child[len(base) :].lstrip("/")
                if not relative:
                    continue
                level = relative.count("/") + 1
                if level > depth:
                    continue
                kind = _entry_kind(item, child)
                collected.setdefault(
                    child,
                    FileEntry(
                        path=child,
                        kind=kind,
                        size=_entry_size(item, kind),
                        modified=_entry_modified(item),
                    ),
                )
                if kind == "dir" and level < depth and child not in seen:
                    seen.add(child)
                    queue.append(child)
        return sorted(collected.values(), key=lambda entry: entry.path)

    async def _list_once(
        self, sandbox_id: str, path: str, *, name: str
    ) -> list[dict[str, Any]]:
        response = await self._request(
            "GET",
            f"/v1/sandboxes/{sandbox_id}/files/list",
            name=name,
            params={"path": path},
            timeout=max(self._request_timeout_seconds, _FILE_TRANSFER_TIMEOUT_SECONDS),
        )
        if response.status_code >= 400:
            self._raise_status(
                response,
                name,
                not_found_message=f"沙箱内目录不存在:{path}",
                not_found_path=path,
            )
        return _as_file_items(await self._json(response, name=name))

    async def destroy(self, name: str) -> None:
        sandbox_id = await self._resolve_id(name)
        if sandbox_id is None:
            self._ids.pop(name, None)
            self._ttls.pop(name, None)
            return
        try:
            response = await self._request(
                "DELETE",
                f"/v1/sandboxes/{sandbox_id}",
                name=name,
                timeout=self._request_timeout_seconds,
            )
            if response.status_code >= 400 and response.status_code != 404:
                self._raise_status(response, name)
        finally:
            self._ids.pop(name, None)
            self._ttls.pop(name, None)

    def _pty_url(
        self,
        sandbox_id: str,
        *,
        cmd: str,
        cwd: str,
        cols: int,
        rows: int,
    ) -> str:
        base = self._base_url
        if base.startswith("https://"):
            ws_base = "wss://" + base[len("https://") :]
        elif base.startswith("http://"):
            ws_base = "ws://" + base[len("http://") :]
        else:
            ws_base = base
        query = urlencode({"cmd": cmd, "cwd": cwd, "cols": cols, "rows": rows})
        return f"{ws_base}/v1/sandboxes/{quote(sandbox_id, safe='')}/pty?{query}"

    def _raise_ws_error(self, name: str, exc: BaseException) -> NoReturn:
        status = _websocket_status(exc)
        details = _error_details(name, exc)
        if status is not None:
            details["status"] = status
        if status in (401, 403):
            raise UpstreamError(
                "sandbox.provider_unavailable",
                "sandboxd PTY 鉴权失败:检查 token 与控制面地址",
                details=details,
            ) from exc
        if status == 404:
            raise UpstreamError(
                "sandbox.provider_error",
                "沙箱运行时实例不存在(可能已被回收)",
                details={"sandbox_name": name, "status": status},
            ) from exc
        raise UpstreamError(
            "sandbox.provider_error",
            "连接 sandboxd PTY 失败",
            details=details,
        ) from exc

    async def _connect_pty(
        self,
        sandbox_id: str,
        *,
        name: str,
        cmd: str,
        cwd: str,
        cols: int,
        rows: int,
    ) -> Any:
        self._require_config()
        connector = self._ws_connect
        if connector is None:
            from websockets.asyncio.client import connect as ws_connect

            connector = ws_connect
        try:
            return await connector(
                self._pty_url(
                    sandbox_id, cmd=cmd, cwd=cwd, cols=max(1, cols), rows=max(1, rows)
                ),
                additional_headers={"Authorization": f"Bearer {self._token}"},
                # 与 httpx 客户端一致:不读取系统代理,长驻连接不设帧大小上限
                proxy=None,
                max_size=None,
                open_timeout=max(5.0, self._request_timeout_seconds),
                close_timeout=5.0,
            )
        except Exception as exc:  # noqa: BLE001 - websockets 异常层次较杂
            self._raise_ws_error(name=name, exc=exc)

    async def open_shell(
        self,
        name: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        cols: int = DEFAULT_SHELL_COLS,
        rows: int = DEFAULT_SHELL_ROWS,
    ) -> ShellSession:
        sandbox_id = await self._require_id(name)
        websocket = await self._connect_pty(
            sandbox_id,
            name=name,
            cmd=_pty_command(env),
            cwd=cwd or "/workspace",
            cols=cols,
            rows=rows,
        )
        return SandboxdShellSession(websocket, name=name)

    async def attach_shell(
        self,
        name: str,
        pid: int,
        *,
        cols: int = DEFAULT_SHELL_COLS,
        rows: int = DEFAULT_SHELL_ROWS,
    ) -> ShellSession:
        raise UpstreamError(
            "sandbox.provider_unsupported",
            "sandboxd PTY 没有可重连的 session id/pid,无法接管既有会话",
            details={"provider": "sandboxd"},
        )


__all__ = ["SandboxdProvider"]
