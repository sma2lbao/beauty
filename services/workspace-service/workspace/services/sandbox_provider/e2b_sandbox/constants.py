"""E2B provider 的 metadata 键与分页/条目常量。"""

from __future__ import annotations

# 沙箱名称/账号写进 E2B metadata:重启后按名字查回 sandbox_id 的唯一依据。
_META_NAME = "beauty_sandbox_name"
_META_ACCOUNT = "beauty_account_id"
# 控制面列沙箱时的每页大小与最多翻页数(按 metadata 精确过滤,通常一页命中)
_LIST_PAGE_SIZE = 50
_LIST_MAX_PAGES = 10
_ENTRY_KINDS = {"file": "file", "dir": "dir", "symlink": "symlink"}
