"""skill 运行器内嵌脚本模板(自 skill_runner.py 拆出)。"""

from __future__ import annotations

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
