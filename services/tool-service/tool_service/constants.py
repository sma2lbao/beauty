"""工具服务的限额、路径策略与策略组装。

唯一真源:执行端(tool_service)与沙箱内 helper(helpers/fs_helper.py)共用
同一套常量。helper 是独立脚本(不能 import 服务包),因此策略随 stdin 的
policy 字段下发,由本模块负责组装。
"""

from __future__ import annotations

from typing import Any

# ---- 路径长度与文件大小 -------------------------------------------------
MAX_PATH_BYTES = 512
MAX_READ_FILE_BYTES = 8 * 1024 * 1024
MAX_EDIT_FILE_BYTES = 2 * 1024 * 1024
MAX_GREP_FILE_BYTES = 2 * 1024 * 1024
MAX_LINE_SCAN_CHARS = 20_000

# ---- read ---------------------------------------------------------------
DEFAULT_READ_LINES = 200
MAX_READ_LINES = 2000
DEFAULT_LINE_CHARS = 2000
MAX_LINE_CHARS = 4000
MAX_READ_CHARS = 25_000

# ---- write / edit(P1,先登记) --------------------------------------------
MAX_WRITE_BYTES = 512_000
MAX_WRITE_BYTES_PER_RUN = 5 * 1024 * 1024
MAX_WRITES_PER_RUN = 50

# ---- glob ---------------------------------------------------------------
DEFAULT_GLOB_RESULTS = 200
MAX_GLOB_RESULTS = 1000
MAX_GLOB_DEPTH = 32
MAX_GLOB_CHARS = 10_000

# ---- grep ---------------------------------------------------------------
DEFAULT_GREP_RESULTS = 100
MAX_GREP_RESULTS = 500
MAX_GREP_FILES = 5000
MAX_GREP_CHARS = 25_000
MAX_GREP_PATTERN_CHARS = 1000
MAX_CONTEXT_LINES = 3
GREP_DEADLINE_SECONDS = 10.0

# ---- list_dir -----------------------------------------------------------
DEFAULT_LIST_ENTRIES = 200
MAX_LIST_ENTRIES = 1000
MAX_LIST_DEPTH = 3

# ---- 执行 ---------------------------------------------------------------
DEFAULT_TOOL_TIMEOUT_SECONDS = 30.0
SANDBOX_TOOL_TTL_SECONDS = 1800
MAX_TOOL_CHARS = 25_000
MAX_TOOL_CALLS_PER_RUN = 60
MAX_ASK_CYCLES = 3

# ---- exec(危险工具,默认关闭;设计见 docs/design/tool-execution.md) ------
#: 命令字符串上限(字符);超出由 schema 拒绝。
MAX_EXEC_COMMAND_CHARS = 4000
#: cwd 参数上限(字符)。
MAX_EXEC_CWD_CHARS = 512
#: 默认命令超时(秒),也是 descriptor 声明上限。
DEFAULT_EXEC_TIMEOUT_SECONDS = 60.0
#: 单条命令超时硬上限(秒),请求可在此范围内覆盖。
MAX_EXEC_TIMEOUT_SECONDS = 120
#: stdout 截断字符数(保留头部)。
MAX_EXEC_STDOUT_CHARS = 16_000
#: stderr 截断字符数(保留尾部,错误摘要通常在最后)。
MAX_EXEC_STDERR_CHARS = 8_000
#: 整包回缩时两路输出各自保留的最小可读长度。
MIN_EXEC_STDOUT_CHARS = 2_000
MIN_EXEC_STDERR_CHARS = 1_000

# ---- 路径策略 -----------------------------------------------------------
# 只读拒绝的目录/文件名片段(按路径段匹配,大小写不敏感)。
DENY_READ_PARTS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        ".uv-cache",
        ".uv-python",
        ".nx",
        ".cache",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)
# 写入额外拒绝的敏感文件名片段。
DENY_WRITE_EXTRA_PARTS = frozenset(
    {
        ".env",
        ".npmrc",
        ".pypirc",
        ".netrc",
        ".ssh",
        ".aws",
        ".gnupg",
        ".kube",
        ".docker",
        "credentials",
        "id_rsa",
        "id_ed25519",
    }
)
DENY_WRITE_PARTS = DENY_READ_PARTS | DENY_WRITE_EXTRA_PARTS
# 遍历时剪枝的产物目录(不参与 glob/grep 递归)。
PRUNE_DIRECTORY_NAMES = frozenset(
    {"dist", "build", "out-tsc", "coverage", "reports", "doc_build"}
)
# 写入时按后缀拒绝的敏感文件(设计 9.2)。
DENY_WRITE_SUFFIXES = (".pem", ".key")


def build_policy() -> dict[str, Any]:
    """组装下发给沙箱 helper 的策略(全部为 JSON 可序列化值)。"""

    return {
        "deny_read_parts": sorted(DENY_READ_PARTS),
        "deny_write_parts": sorted(DENY_WRITE_PARTS),
        "deny_write_suffixes": list(DENY_WRITE_SUFFIXES),
        "prune_dirs": sorted(PRUNE_DIRECTORY_NAMES),
        "max_path_bytes": MAX_PATH_BYTES,
        "max_read_file_bytes": MAX_READ_FILE_BYTES,
        "max_edit_file_bytes": MAX_EDIT_FILE_BYTES,
        "max_grep_file_bytes": MAX_GREP_FILE_BYTES,
        "max_line_scan_chars": MAX_LINE_SCAN_CHARS,
        "default_read_lines": DEFAULT_READ_LINES,
        "max_read_lines": MAX_READ_LINES,
        "default_line_chars": DEFAULT_LINE_CHARS,
        "max_line_chars": MAX_LINE_CHARS,
        "max_read_chars": MAX_READ_CHARS,
        "max_write_bytes": MAX_WRITE_BYTES,
        "default_glob_results": DEFAULT_GLOB_RESULTS,
        "max_glob_results": MAX_GLOB_RESULTS,
        "max_glob_depth": MAX_GLOB_DEPTH,
        "max_glob_chars": MAX_GLOB_CHARS,
        "default_grep_results": DEFAULT_GREP_RESULTS,
        "max_grep_results": MAX_GREP_RESULTS,
        "max_grep_files": MAX_GREP_FILES,
        "max_grep_chars": MAX_GREP_CHARS,
        "max_grep_pattern_chars": MAX_GREP_PATTERN_CHARS,
        "max_context_lines": MAX_CONTEXT_LINES,
        "grep_deadline_seconds": GREP_DEADLINE_SECONDS,
        "default_list_entries": DEFAULT_LIST_ENTRIES,
        "max_list_entries": MAX_LIST_ENTRIES,
        "max_list_depth": MAX_LIST_DEPTH,
    }
