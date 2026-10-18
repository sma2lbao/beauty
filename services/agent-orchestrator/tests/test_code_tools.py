"""编码 agent function tools:路径边界、只读/写操作与确认标记。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agent_orchestrator.services.code_tools import code_specs
from agent_orchestrator.services.tools import ToolRegistry


def _registry(root: Path) -> ToolRegistry:
    return ToolRegistry(code_specs([str(root)]))


def _json(text: str) -> dict:
    return json.loads(text)


def test_code_tools_read_write_edit_search(tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    async def scenario() -> None:
        write = await registry.execute(
            "code_write_file", json.dumps({"path": "src/a.py", "content": "x = 1\n"})
        )
        assert "已写入" in write

        read = await registry.execute(
            "code_read_file", json.dumps({"path": "src/a.py"})
        )
        assert "1" in read and "x = 1" in read

        search = await registry.execute(
            "code_search_text", json.dumps({"pattern": "x = 1"})
        )
        assert "src/a.py:1" in search

        edit = await registry.execute(
            "code_edit_file",
            json.dumps(
                {
                    "path": "src/a.py",
                    "old_string": "x = 1",
                    "new_string": "x = 2",
                }
            ),
        )
        assert "已编辑" in edit

        listing = await registry.execute(
            "code_list_files", json.dumps({"path": "src"})
        )
        assert "src/a.py" in listing
        assert registry.requires_confirmation("code_write_file") is True
        assert registry.requires_confirmation("code_read_file") is False

    asyncio.run(scenario())


def test_code_tools_reject_escape_and_deny_git(tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    async def scenario() -> None:
        escaped = await registry.execute(
            "code_read_file", json.dumps({"path": "../../etc/passwd"})
        )
        assert _json(escaped).get("error")
        denied = await registry.execute(
            "code_write_file",
            json.dumps({"path": ".git/config", "content": "x"}),
        )
        assert _json(denied).get("error")

    asyncio.run(scenario())


def test_code_tools_deny_sensitive_paths(tmp_path: Path) -> None:
    (tmp_path / '.env').write_text('SECRET=1')
    (tmp_path / '.cache').mkdir()
    (tmp_path / '.cache' / 'x').write_text('cached')
    registry = _registry(tmp_path)

    async def scenario() -> None:
        env_read = await registry.execute(
            'code_read_file', json.dumps({'path': '.env'})
        )
        assert _json(env_read).get('error')
        listing = await registry.execute('code_list_files', json.dumps({}))
        assert '.cache' not in listing and '.env' not in listing
        search = await registry.execute(
            'code_search_text', json.dumps({'pattern': 'SECRET'})
        )
        assert search == '(无匹配)'

    asyncio.run(scenario())
