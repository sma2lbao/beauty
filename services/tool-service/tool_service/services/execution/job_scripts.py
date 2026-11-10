"""后台作业的 shell 脚本拼装(纯函数,不碰 IO)。

作业 = 沙箱里一个被 setsid/nohup 脱离的后台进程,状态与日志落在作业目录:

    /workspace/.tool-jobs/<32hex>/stdout.log stderr.log pid exit

拼装约定:
- 外层 argv 恒为 ["/bin/sh", "-lc", script],由服务端生成;
- 模型给的 command 只经 shlex.quote 拼一次(进内层 /bin/sh -lc),之后各层
  拼的都是新生成的脚本串,不再混入用户输入;
- 作业目录 / pid / 日志路径全部由服务端用 job_id 生成,并再 quote 一次;
- 解析函数(parse_status / split_logs)是纯字符串处理,便于单测引号与截断。

为什么外层脚本要自己记录退出码:后台进程不是 exec 的子进程,拿不到 waitpid;
内层脚本把 $? 写进 exit 文件,后续 job_output 才有真实的退出码可读。
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from tool_service.services.helpers import WORKSPACE_ROOT

#: 作业目录的根(必须落在工作区内,沙箱重启后随工作区卷保留)。
JOBS_DIR = f"{WORKSPACE_ROOT}/.tool-jobs"
#: 作业 id:32 位小写十六进制(uuid4().hex),对外暴露前必须过这个正则。
JOB_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
#: 统一 shell(与 exec 一致,沙箱镜像保证存在)。
SHELL = "/bin/sh"

#: 状态行前缀与取值,脚本用 echo 输出(不用 printf 转义,避免层层反斜杠)。
STATUS_PREFIX = "STATUS:"
STATE_RUNNING = "running"
STATE_EXITED = "exited"
STATE_NOEXIT = "noexit"
STATE_MISSING = "missing"
STATE_KILLED = "killed"
STATE_UNKNOWN = "unknown"
_SCRIPT_RUNNING = "RUNNING"
_SCRIPT_NOEXIT = "NOEXIT"
_SCRIPT_MISSING = "MISSING"
_SCRIPT_KILLED = "KILLED"
_SCRIPT_EXIT = "EXIT:"

STDOUT_MARKER = "---STDOUT---"
STDERR_MARKER = "---STDERR---"
PID_PREFIX = "PID:"


def quote(value: str) -> str:
    """POSIX 单引号转义(服务端路径与模型 command 都走这里)。"""
    return shlex.quote(value)


@dataclass(frozen=True, slots=True)
class JobPaths:
    """一次作业在沙箱内的落盘位置(全部由 job_id 决定)。"""

    job_id: str
    directory: str
    stdout: str
    stderr: str
    pid: str
    exit_file: str


def is_valid_job_id(value: str) -> bool:
    return bool(JOB_ID_PATTERN.fullmatch(value or ""))


def job_paths(job_id: str) -> JobPaths:
    """按 job_id 算出作业目录与各文件路径(不校验 id,调用方先校验)。"""
    directory = f"{JOBS_DIR}/{job_id}"
    return JobPaths(
        job_id=job_id,
        directory=directory,
        stdout=f"{directory}/stdout.log",
        stderr=f"{directory}/stderr.log",
        pid=f"{directory}/pid",
        exit_file=f"{directory}/exit",
    )


def start_argv(*, command: str, cwd: str, paths: JobPaths) -> list[str]:
    """job_start 用的 argv:起后台进程并把 pid 打到 stdout。

    内层脚本先跑 command 并记录退出码;外层用 setsid 让后台进程自成一个
    会话/进程组(有 setsid 时 job_kill 才能整组终止),没有 setsid 的镜像
    退化为 nohup + 只杀直接子进程(设计 §10 的已知降级)。
    外层把 $! 写进 pid 文件并回显 PID:<pid>,调用方据此确认确实起来了。
    """
    inner = (
        f"/bin/sh -lc {quote(command)}"
        f" > {quote(paths.stdout)}"
        f" 2> {quote(paths.stderr)}"
        f"; echo $? > {quote(paths.exit_file)}"
    )
    script = "\n".join(
        [
            "set -eu",
            f"mkdir -p {quote(paths.directory)}",
            f"cd {quote(cwd)}",
            "setsid_bin=$(command -v setsid || true)",
            'if [ -n "$setsid_bin" ]; then',
            f'  "$setsid_bin" /bin/sh -c {quote(inner)} </dev/null >/dev/null 2>&1 &',
            "else",
            f"  nohup /bin/sh -c {quote(inner)} </dev/null >/dev/null 2>&1 &",
            "fi",
            "job_pid=$!",
            f'echo "$job_pid" > {quote(paths.pid)}',
            f'echo "{PID_PREFIX}$job_pid"',
        ]
    )
    return [SHELL, "-lc", script]


def status_argv(paths: JobPaths, *, stdout_chars: int, stderr_chars: int) -> list[str]:
    """job_output 用的 argv:一次 exec 同时拿状态与日志尾部。

    状态优先级:exit 文件(已结束,含真实退出码) > pid 存活(running) >
    pid 不在(NOEXIT:进程被 OOM/外部 kill -9,没来得及写退出码)。
    日志用 tail -c 只取尾部,避免把整份日志搬进模型上下文。
    """
    script = "\n".join(
        [
            "set -u",
            f"if [ -f {quote(paths.exit_file)} ]; then",
            f'  echo "{STATUS_PREFIX}{_SCRIPT_EXIT}$(cat {quote(paths.exit_file)})"',
            "else",
            f"  job_pid=$(cat {quote(paths.pid)} 2>/dev/null || true)",
            '  if [ -n "$job_pid" ] && kill -0 "$job_pid" 2>/dev/null; then',
            f'    echo "{STATUS_PREFIX}{_SCRIPT_RUNNING}"',
            "  else",
            f'    echo "{STATUS_PREFIX}{_SCRIPT_NOEXIT}"',
            "  fi",
            "fi",
            f"echo '{STDOUT_MARKER}'",
            f"tail -c {int(stdout_chars)} {quote(paths.stdout)} 2>/dev/null || true",
            f"echo '{STDERR_MARKER}'",
            f"tail -c {int(stderr_chars)} {quote(paths.stderr)} 2>/dev/null || true",
            "exit 0",
        ]
    )
    return [SHELL, "-lc", script]


def kill_argv(paths: JobPaths) -> list[str]:
    """job_kill 用的 argv:SIGTERM 进程组 → 1 秒 → SIGKILL,最后回显状态。

    已结束的作业不报错(幂等):只需回显 exit 文件里的真实退出码;若 TERM
    之后进程恰好正常退出,以 exit 文件为准判 exited,而不是硬写成 killed。
    """
    script = "\n".join(
        [
            "set -u",
            f"job_pid=$(cat {quote(paths.pid)} 2>/dev/null || true)",
            'if [ -z "$job_pid" ]; then',
            f'  echo "{STATUS_PREFIX}{_SCRIPT_MISSING}"',
            "  exit 0",
            "fi",
            'kill -TERM -"$job_pid" 2>/dev/null'
            ' || kill -TERM "$job_pid" 2>/dev/null || true',
            "sleep 1",
            f"if [ -f {quote(paths.exit_file)} ]; then",
            f'  echo "{STATUS_PREFIX}{_SCRIPT_EXIT}$(cat {quote(paths.exit_file)})"',
            "  exit 0",
            "fi",
            'kill -KILL -"$job_pid" 2>/dev/null'
            ' || kill -KILL "$job_pid" 2>/dev/null || true',
            "sleep 1",
            f"if [ -f {quote(paths.exit_file)} ]; then",
            f'  echo "{STATUS_PREFIX}{_SCRIPT_EXIT}$(cat {quote(paths.exit_file)})"',
            "else",
            f'  echo "{STATUS_PREFIX}{_SCRIPT_KILLED}"',
            "fi",
            "exit 0",
        ]
    )
    return [SHELL, "-lc", script]


def cleanup_argv(*, older_than_minutes: int, directory: str = JOBS_DIR) -> list[str]:
    """清理超保留期的作业目录(best-effort,失败不影响 job_start)。

    只删 JOBS_DIR 的直接子目录,且目录名必须是 32 位 hex 作业 id——即便
    未来有人把别的文件塞进 .tool-jobs,也不会被 rm -rf 带走。
    """
    minutes = max(int(older_than_minutes), 1)
    script = "\n".join(
        [
            "set -u",
            f"if [ -d {quote(directory)} ]; then",
            (
                f"  find {quote(directory)} -mindepth 1 -maxdepth 1 -type d"
                f' -name "[0-9a-f]*" -mmin +{minutes}'
                " -exec rm -rf -- {} + 2>/dev/null || true"
            ),
            "fi",
            "exit 0",
        ]
    )
    return [SHELL, "-lc", script]


def parse_status(output: str) -> tuple[str, int | None]:
    """解析 status_argv 的输出:返回 (状态, 退出码)。

    状态取值 running / exited / noexit / missing / killed / unknown;
    只有 exited 带退出码(退出码解析失败时给 None,不把整次调用判失败)。
    """
    text = output or ""
    start = text.find(STATUS_PREFIX)
    if start < 0:
        return (STATE_UNKNOWN, None)
    token = text[start + len(STATUS_PREFIX) :].split("\n", 1)[0].strip()
    if token.startswith(_SCRIPT_EXIT):
        raw = token[len(_SCRIPT_EXIT) :].strip()
        try:
            return (STATE_EXITED, int(raw))
        except ValueError:
            return (STATE_EXITED, None)
    if token == _SCRIPT_RUNNING:
        return (STATE_RUNNING, None)
    if token == _SCRIPT_NOEXIT:
        return (STATE_NOEXIT, None)
    if token == _SCRIPT_MISSING:
        return (STATE_MISSING, None)
    if token == _SCRIPT_KILLED:
        return (STATE_KILLED, None)
    return (STATE_UNKNOWN, None)


def _drop_leading_newline(text: str) -> str:
    return text[1:] if text.startswith("\n") else text


def _drop_trailing_newline(text: str) -> str:
    return text[:-1] if text.endswith("\n") else text


def split_logs(output: str) -> tuple[str, str]:
    """把 status_argv 的输出切成 (stdout 尾部, stderr 尾部)。"""
    _, found_stdout, rest = (output or "").partition(STDOUT_MARKER)
    if not found_stdout:
        return ("", "")
    stdout_text, found_stderr, stderr_text = rest.partition(STDERR_MARKER)
    # 标记前的换行由脚本 printf/echo 补出,不是日志内容,只裁 stdout 一侧
    # (stderr 之后直接结束,尾部字节就是日志原文)。
    stdout_text = _drop_trailing_newline(_drop_leading_newline(stdout_text))
    if not found_stderr:
        return (stdout_text, "")
    return (stdout_text, _drop_leading_newline(stderr_text))
