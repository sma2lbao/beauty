"""P3-C:在沙箱里执行用户上传的技能包,并以 skill_run 工具暴露给模型。

技能包是**任意代码**(上传时只做静态校验),所以执行必须发生在沙箱里:
orchestrator 只负责取包、解包、起进程、收产物,沙箱引擎与生命周期在
workspace-service(P1)。三条边界:

1. **不可信输入**:zip 来自对象存储,解包前先校验 sha256(与 run 快照一致),
   解包脚本(UNPACK_PY)在沙箱内再按上传同口径校验一遍路径穿越/绝对路径/
   符号链接/规模上限——双保险,不假设上传校验一定跑过;
2. **不可信输出**:entrypoint 的 stdout/stderr 截断后再回喂模型,产物目录只
   读文本(超限/二进制只报元数据,不把字节读回服务进程);
3. **能力即权限边界**:本工具只在 run 绑定了可执行技能包时装配(main 的
   extras factory 按 ToolContext.run_id 判断),chat 路径(run_id 为空)不可见。

产物登记:run 交付物可以有**多件**,因此每个产出文件**逐件登记**——
filename/source=skill 标记来源,文本与二进制都保留字节(二进制进
content_bytes 或对象存储);登记失败只写进 summary,不让工具本身失败。
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from common.artifacts import ArtifactStore
from common.logging import get_logger
from database.session import session_scope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.core.config import Settings
from agent_orchestrator.models.runs import Run, RunArtifact
from agent_orchestrator.repositories import RunRepository
from agent_orchestrator.services import artifacts
from agent_orchestrator.services.sandbox import Sandbox, SandboxExecResult
from agent_orchestrator.services.skill_packages import (
    MAX_FILES,
    MAX_UNCOMPRESSED_BYTES,
)
from agent_orchestrator.services.tools import ToolRisk, ToolSpec
from agent_orchestrator.services.workspace import Workspace

logger = get_logger(__name__)

#: 工具名(与 skill_packages.SKILL_RUN_TOOL 一致:有 entrypoint 的包自动获得)
SKILL_RUN_TOOL = "skill_run"
#: 包与辅助脚本在沙箱内的落地目录(workspace-service 写文件会自动建父目录)
SKILL_DIR = "/workspace/.skill"
PACKAGE_PATH = SKILL_DIR + "/package.zip"
UNPACK_PATH = SKILL_DIR + "/unpack.py"
LIST_OUTPUTS_PATH = SKILL_DIR + "/list_outputs.py"
#: 解包目标与产物目录(entrypoint 在 SKILL_ROOT 下执行,产物写 OUTPUT_ROOT)
SKILL_ROOT = "/workspace/skill"
OUTPUT_ROOT = "/workspace/output"
#: run 快照缺 timeout 时的默认值(与上传校验 DEFAULT_TIMEOUT_SECONDS 一致)
DEFAULT_TIMEOUT_SECONDS = 300
#: 回喂模型的 stdout/stderr 上限(防提示词爆炸)
STDOUT_LIMIT = 20_000
STDERR_LIMIT = 4_000
#: 执行器视为"已是解释器"的首段(命中则不拼 runtime 解释器)
INTERPRETERS = frozenset(
    {
        "python",
        "python3",
        "python3.11",
        "python3.12",
        "python3.13",
        "py",
        "node",
        "nodejs",
        "bash",
        "sh",
    }
)
#: runtime → 沙箱镜像里的解释器可执行名
RUNTIME_BINS = {"python": "python3", "node": "node"}
MARKDOWN_SUFFIXES = (".md", ".markdown")

_UNPACK_TEMPLATE = r'''#!/usr/bin/env python3
"""沙箱内解包技能包:按上传同口径再校验一遍路径与规模(双保险)。

stdin JSON: {"zip": "<包路径>", "dest": "<解压目录>"}
stdout JSON: {"ok": true, "files": N, "bytes": M}
    或 {"ok": false, "code": ..., "message": ...}
退出码:0 成功;2 校验失败(服务层据此中止执行)。
"""

import json
import os
import re
import sys
import zipfile

MAX_FILES = __MAX_FILES__
MAX_TOTAL_BYTES = __MAX_UNCOMPRESSED_BYTES__
SKILL_MD = "SKILL.md"
S_IFLNK = 0o120000


def fail(code, message):
    payload = {"ok": False, "code": code, "message": message}
    print(json.dumps(payload, ensure_ascii=False))
    sys.exit(2)


def validate(name):
    """归一化包内相对路径;拒绝绝对路径/穿越/NUL,不合法返回 None。"""
    if not name or "\x00" in name:
        return None
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return None
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        return None
    return "/".join(parts)


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        fail("bad_request", "stdin 不是合法 JSON")
    zip_path = payload.get("zip")
    dest = payload.get("dest")
    if not zip_path or not dest:
        fail("bad_request", "缺少 zip/dest")
    os.makedirs(dest, exist_ok=True)
    try:
        archive = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        fail("package_invalid", "不是合法的 zip 包")
    with archive:
        entries = [info for info in archive.infolist() if not info.is_dir()]
        if len(entries) > MAX_FILES:
            fail("too_large", "文件数超过上限 %d" % MAX_FILES)
        if sum(info.file_size for info in entries) > MAX_TOTAL_BYTES:
            fail("too_large", "解压后超过上限 %d 字节" % MAX_TOTAL_BYTES)
        mapping = {}
        for info in entries:
            if (info.external_attr >> 16) & 0o170000 == S_IFLNK:
                fail("path_invalid", "不允许符号链接:" + info.filename)
            normalized = validate(info.filename)
            if normalized is None:
                fail("path_invalid", "非法包内路径:" + info.filename)
            if normalized in mapping:
                fail("path_invalid", "重复路径:" + normalized)
            mapping[normalized] = info
        names = list(mapping)
        # 与服务端归一化口径一致:整包只包一层顶层目录时剥掉
        if SKILL_MD not in names:
            tops = set(name.split("/")[0] for name in names)
            if len(tops) == 1:
                top = next(iter(tops))
                if all("/" in name for name in names):
                    mapping = dict(
                        (name[len(top) + 1 :], info)
                        for name, info in mapping.items()
                    )
                    names = list(mapping)
        root = os.path.realpath(dest)
        total = 0
        for name in names:
            target = os.path.realpath(os.path.join(root, *name.split("/")))
            if target != root and not target.startswith(root + os.sep):
                fail("path_escape", "解压路径越出目标目录:" + name)
            parent = os.path.dirname(target)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with archive.open(mapping[name]) as source, open(target, "wb") as sink:
                while True:
                    chunk = source.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_TOTAL_BYTES:
                        fail("too_large", "解压后超过上限 %d 字节" % MAX_TOTAL_BYTES)
                    sink.write(chunk)
    print(json.dumps({"ok": True, "files": len(names), "bytes": total}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

_UNPACK_PLACEHOLDERS = {
    "__MAX_FILES__": str(MAX_FILES),
    "__MAX_UNCOMPRESSED_BYTES__": str(MAX_UNCOMPRESSED_BYTES),
}
UNPACK_PY = _UNPACK_TEMPLATE
for _placeholder, _value in _UNPACK_PLACEHOLDERS.items():
    UNPACK_PY = UNPACK_PY.replace(_placeholder, _value)

LIST_OUTPUTS_PY = r'''#!/usr/bin/env python3
"""列出产物目录里的文件(相对路径/大小),给服务层决定读哪些正文。

argv: [root, limit];stdout 单行 JSON:
    {"files": [{"path": ..., "size": ...}], "truncated": bool}
只读元数据,不读正文——正文由服务层按大小上限逐个 read_file。
"""

import json
import os
import sys

root = sys.argv[1] if len(sys.argv) > 1 else "/workspace/output"
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
files = []
truncated = False
if os.path.isdir(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            if len(files) >= limit:
                truncated = True
                break
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            files.append({"path": rel, "size": size})
        if truncated:
            break
print(json.dumps({"files": files, "truncated": truncated}, ensure_ascii=False))
'''


class SkillRunError(Exception):
    """技能包执行失败(可读原因;execute 转成 {"error": ...} 返回)。"""


@dataclass
class OutputFile:
    """产物目录里的一个文件(未超限时读到字节,文本额外解码出正文)。"""

    path: str
    size: int
    text: str | None = None
    binary: bool = False
    truncated: bool = False
    data: bytes | None = None
    artifact_id: str | None = None

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "path": self.path,
            "size": self.size,
            "binary": self.binary,
            "truncated": self.truncated,
        }
        if self.artifact_id is not None:
            payload["artifact_id"] = self.artifact_id
        return payload


def _error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    """按字符截断;返回 (文本, 是否截断)。"""
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _entrypoint_argv(entrypoint: list[str] | None, runtime: str) -> list[str]:
    """解析入口命令:runtime 选解释器,entrypoint 首段已是解释器则原样用。

    上传时已拒绝绝对路径/穿越;这里再守一遍(DB 快照不能当作可信输入),
    避免沙箱内执行被快照改动带偏。
    """
    argv = [str(arg) for arg in entrypoint or []]
    if not argv:
        raise SkillRunError("该技能包没有 entrypoint,不能执行")
    for arg in argv:
        if "\x00" in arg:
            raise SkillRunError("entrypoint 含 NUL 字符")
        candidate = arg.replace("\\", "/")
        if candidate.startswith("/") or (len(candidate) > 1 and candidate[1] == ":"):
            raise SkillRunError(f"entrypoint 不允许绝对路径:{arg}")
        if ".." in candidate.split("/"):
            raise SkillRunError(f"entrypoint 不允许路径穿越:{arg}")
    if argv[0] in INTERPRETERS:
        return argv
    return [RUNTIME_BINS.get(runtime, "python3"), *argv]


def _content_type_for(item: OutputFile) -> str:
    """产物 MIME:文本按 .md/其他区分,二进制按扩展名猜,兜底二进制流。"""
    if not item.binary:
        if item.path.lower().endswith(MARKDOWN_SUFFIXES):
            return "text/markdown"
        return "text/plain"
    guessed, _encoding = mimetypes.guess_type(item.path)
    return guessed or artifacts.DEFAULT_CONTENT_TYPE


def _unpack_failure_summary(result: SandboxExecResult) -> str:
    """解包失败时的可读原因(stdout 里的 JSON 优先,否则 stderr 尾巴)。"""
    try:
        payload = json.loads((result.stdout or "").strip() or "{}")
    except ValueError:
        payload = {}
    if isinstance(payload, dict) and payload.get("message"):
        return str(payload["message"])
    tail = (result.stderr or "").strip()[-500:]
    return tail or f"解包脚本退出码 {result.exit_code}"


class SkillRunner:
    """在沙箱内执行 run 绑定的技能包快照(P3-C 的 skill_run 工具)。

    session_factory 用于取 run 快照与登记产物(短事务,不在沙箱调用期间持锁);
    sandbox/workspace 都是协议(测试注入 Fake,零网络零真实沙箱);
    store 与交付物存储一致(None=database 模式,内容进 run_artifacts.content)。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        sandbox: Sandbox,
        workspace: Workspace,
        store: ArtifactStore | None,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._sandbox = sandbox
        self._workspace = workspace
        self._store = store
        self._settings = settings

    # ---- 工具暴露 ----

    def spec(self, run: Run) -> ToolSpec:
        """构造 skill_run 工具:跑任意代码,必须过人工确认闸(§6.2)。"""
        tenant_id = run.tenant_id
        run_id = run.id
        skill_label = run.skill_id or "上传技能包"

        async def handler(args: dict[str, Any]) -> str:
            input_text = str(args.get("input") or "")
            try:
                return await self.execute(tenant_id, run_id, input_text)
            except Exception as exc:  # noqa: BLE001 - 工具失败回喂模型,不炸图
                logger.warning(
                    "skill run failed",
                    tenant_id=tenant_id,
                    run_id=run_id,
                    error=f"{type(exc).__name__}: {exc}",
                )
                return _error(f"{type(exc).__name__}: {exc}")

        return ToolSpec(
            name=SKILL_RUN_TOOL,
            description=(
                f"在沙箱内执行技能「{skill_label}」的 entrypoint,"
                "返回 stdout/stderr 与产物清单(在沙箱内运行:网络禁用、"
                "工作区受控,自动执行)"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "input": {
                        "type": "string",
                        "description": "可选的输入文本,经 stdin JSON 传给脚本",
                    }
                },
                "additionalProperties": False,
            },
            handler=handler,
            risk=ToolRisk.WRITE,
        )

    # ---- 执行 ----

    async def execute(
        self, tenant_id: str, run_id: str, input_text: str | None = None
    ) -> str:
        """取 run 快照 → 沙箱执行 entrypoint → 登记产物;返回 JSON 文本。

        失败一律以 {"error": ...} 文本返回(工具边界不抛):模型据此决策,
        用户看到可读原因。沙箱在 finally 里销毁,不泄漏资源。
        """
        run = await self._load_run(tenant_id, run_id)
        if run is None:
            return _error("run 不存在或不属于该租户")
        if not run.skill_object_key:
            return _error("该 run 没有绑定可执行的技能包")
        if not run.skill_sha256:
            return _error("该 run 缺少技能包校验信息(sha256),拒绝执行")
        if not run.skill_entrypoint:
            return _error("该技能包没有 entrypoint,不能执行")

        sandbox_id: str | None = None
        try:
            sandbox_id = await self._create_sandbox(tenant_id, run)
            await self._unpack(tenant_id, sandbox_id, run)
            result = await self._run_entrypoint(tenant_id, sandbox_id, run, input_text)
            outputs, listing_truncated = await self._collect_outputs(
                tenant_id, sandbox_id
            )
            registered, summary = await self._register_outputs(
                tenant_id, run_id, outputs
            )
            return self._result_payload(
                result, outputs, listing_truncated, registered, summary
            )
        except SkillRunError as exc:
            logger.info(
                "skill run rejected",
                tenant_id=tenant_id,
                run_id=run_id,
                reason=str(exc),
            )
            return _error(str(exc))
        finally:
            if sandbox_id is not None:
                with suppress(Exception):
                    await self._sandbox.destroy(tenant_id, sandbox_id)

    async def _load_run(self, tenant_id: str, run_id: str) -> Run | None:
        """短事务取 run 快照(租户隔离;不存在返回 None)。"""
        async with session_scope(self._session_factory) as session:
            try:
                return await RunRepository(session).require_for_tenant(
                    tenant_id, run_id
                )
            except Exception:  # noqa: BLE001 - 不存在与越权同义(404)
                return None

    async def _create_sandbox(self, tenant_id: str, run: Run) -> str:
        """按 run 快照的 runtime/network 起一次性沙箱(kind=skill)。"""
        runtime = run.skill_runtime or "python"
        image = (
            self._settings.skill_node_image
            if runtime == "node"
            else self._settings.skill_python_image
        )
        info = await self._sandbox.create(
            tenant_id,
            kind="skill",
            image=image,
            ttl_seconds=self._settings.skill_sandbox_ttl_seconds,
            network=run.skill_network or "deny",
            run_id=run.id,
        )
        return info.id

    async def _unpack(self, tenant_id: str, sandbox_id: str, run: Run) -> None:
        """取包 → 校验 sha256 → 写包与解包脚本 → 沙箱内解包(再校验)。"""
        key = str(run.skill_object_key)
        try:
            package = await self._workspace.get_object(tenant_id, key)
        except Exception as exc:  # noqa: BLE001 - 上游不可用/不存在统一转可读错误
            message = f"{type(exc).__name__}: {exc}"
            raise SkillRunError(f"技能包读取失败:{message}") from exc
        digest = hashlib.sha256(package).hexdigest()
        if digest != run.skill_sha256:
            raise SkillRunError(
                "技能包 sha256 与 run 快照不一致,拒绝执行"
                f"(快照 {run.skill_sha256[:12]},实际 {digest[:12]})"
            )
        await self._sandbox.write_file(tenant_id, sandbox_id, PACKAGE_PATH, package)
        await self._sandbox.write_file(
            tenant_id, sandbox_id, UNPACK_PATH, UNPACK_PY.encode("utf-8")
        )
        result = await self._sandbox.exec(
            tenant_id,
            sandbox_id,
            ["python3", UNPACK_PATH],
            cwd="/workspace",
            stdin=json.dumps(
                {"zip": PACKAGE_PATH, "dest": SKILL_ROOT}, ensure_ascii=False
            ),
            timeout_seconds=self._settings.skill_exec_timeout_seconds,
        )
        if result.exit_code != 0:
            raise SkillRunError(f"技能包解包失败:{_unpack_failure_summary(result)}")

    async def _run_entrypoint(
        self,
        tenant_id: str,
        sandbox_id: str,
        run: Run,
        input_text: str | None,
    ) -> SandboxExecResult:
        """执行 entrypoint:stdin 传 input/goal/title,超时取快照与配置的小值 +30。"""
        runtime = run.skill_runtime or "python"
        argv = _entrypoint_argv(run.skill_entrypoint, runtime)
        timeout = (
            min(
                run.skill_timeout_seconds or DEFAULT_TIMEOUT_SECONDS,
                self._settings.skill_exec_timeout_seconds,
            )
            + 30
        )
        payload = {
            "input": input_text or "",
            "goal": run.goal,
            "title": run.title,
        }
        return await self._sandbox.exec(
            tenant_id,
            sandbox_id,
            argv,
            cwd=SKILL_ROOT,
            stdin=json.dumps(payload, ensure_ascii=False),
            timeout_seconds=timeout,
        )

    async def _collect_outputs(
        self, tenant_id: str, sandbox_id: str
    ) -> tuple[list[OutputFile], bool]:
        """列产物目录并读回文本产物(超限/二进制只报元数据)。"""
        result = await self._sandbox.exec(
            tenant_id,
            sandbox_id,
            [
                "python3",
                LIST_OUTPUTS_PATH,
                OUTPUT_ROOT,
                str(self._settings.skill_max_output_files),
            ],
            cwd="/workspace",
            timeout_seconds=self._settings.skill_exec_timeout_seconds,
        )
        listing = self._parse_listing(result)
        outputs: list[OutputFile] = []
        for item in listing.get("files") or []:
            path = str(item.get("path") or "")
            size = int(item.get("size") or 0)
            if not path or path.startswith("/") or ".." in path.split("/"):
                continue  # 防御:清单脚本只产出相对路径,异常项不读
            if size > self._settings.skill_max_output_bytes:
                outputs.append(OutputFile(path=path, size=size, truncated=True))
                continue
            try:
                data = await self._sandbox.read_file(
                    tenant_id, sandbox_id, f"{OUTPUT_ROOT}/{path}"
                )
            except Exception as exc:  # noqa: BLE001 - 单文件读失败不中止整次执行
                logger.warning(
                    "skill output read failed",
                    tenant_id=tenant_id,
                    path=path,
                    error=f"{type(exc).__name__}: {exc}",
                )
                outputs.append(OutputFile(path=path, size=size, truncated=True))
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                outputs.append(
                    OutputFile(path=path, size=len(data), binary=True, data=data)
                )
            else:
                outputs.append(
                    OutputFile(path=path, size=len(data), text=text, data=data)
                )
        return outputs, bool(listing.get("truncated"))

    def _parse_listing(self, result: SandboxExecResult) -> dict[str, Any]:
        """解析产物清单;脚本失败/输出非法按"没有产物"处理(不炸工具)。"""
        if result.exit_code != 0:
            logger.warning(
                "skill output listing failed",
                exit_code=result.exit_code,
                stderr=(result.stderr or "")[-500:],
            )
            return {}
        raw = (result.stdout or "").strip()
        if not raw:
            return {}
        try:
            payload = json.loads(raw.splitlines()[-1])
        except ValueError:
            logger.warning("skill output listing is not JSON", raw=raw[:300])
            return {}
        return payload if isinstance(payload, dict) else {}

    async def _register_outputs(
        self, tenant_id: str, run_id: str, outputs: list[OutputFile]
    ) -> tuple[list[RunArtifact], str]:
        """把每个读到字节的产物**逐件**登记为 run 交付物(文本/二进制都留字节)。

        逐文件独立事务:单件登记失败只记进 summary,不影响其余文件,也不让
        工具本身失败;登记成功的行 id 写回 OutputFile.artifact_id。
        """
        registrable = [item for item in outputs if item.data is not None]
        if not registrable:
            return [], "产物目录里没有可登记的文件"
        saved: list[RunArtifact] = []
        failures: list[str] = []
        for item in registrable:
            content_type = _content_type_for(item)
            try:
                async with session_scope(self._session_factory) as session:
                    run = await RunRepository(session).require_for_tenant(
                        tenant_id, run_id
                    )
                    row = await artifacts.persist_file_artifact(
                        session,
                        run,
                        filename=item.path,
                        content_type=content_type,
                        data=item.data or b"",
                        store=self._store,
                        kind=artifacts.infer_kind(content_type),
                        source=artifacts.ARTIFACT_SKILL_SOURCE,
                        title=item.path,
                    )
                    item.artifact_id = row.id
                saved.append(row)
            except Exception as exc:  # noqa: BLE001 - 单件登记失败不改变执行结果
                logger.warning(
                    "skill output artifact persist failed",
                    tenant_id=tenant_id,
                    run_id=run_id,
                    path=item.path,
                    error=f"{type(exc).__name__}: {exc}",
                )
                failures.append(f"{item.path}: {type(exc).__name__}: {exc}")
        note = f"已登记 {len(saved)} 个文件为 run 交付物"
        if failures:
            note += f";产物登记失败 {len(failures)} 个:" + ";".join(failures)
        return saved, note

    def _result_payload(
        self,
        result: SandboxExecResult,
        outputs: list[OutputFile],
        listing_truncated: bool,
        registered: list[RunArtifact],
        summary: str,
    ) -> str:
        """组装回喂模型的 JSON:stdout/stderr 截断,产物逐件带自己的 artifact_id。"""
        stdout, stdout_truncated = _truncate(result.stdout or "", STDOUT_LIMIT)
        stderr, stderr_truncated = _truncate(result.stderr or "", STDERR_LIMIT)
        payload: dict[str, Any] = {
            "exit_code": result.exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "outputs": [item.as_payload() for item in outputs],
            "truncated": bool(
                result.truncated
                or stdout_truncated
                or stderr_truncated
                or listing_truncated
                or any(item.truncated for item in outputs)
            ),
            "summary": summary,
        }
        if registered:
            # 顶层 artifact_id 取第一件(兼容只认单件的旧前端);多件看 outputs
            payload["artifact_id"] = registered[0].id
        return json.dumps(payload, ensure_ascii=False)
