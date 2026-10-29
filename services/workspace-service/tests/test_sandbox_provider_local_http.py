"""LocalSandboxProvider 经 HTTP 的生命周期端到端(走 lifespan 的 build 分支)。

不注入 mock provider:验证 `create_app` 按 settings 装配 local provider,
create / exec / 文件读写 / tree / delete 全链路落在 tmp_path 下的沙箱根。
"""

from __future__ import annotations

import base64
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import TENANT, _settings, verifier
from tests.support.sandbox_local import (
    _dir_of,
    posix_only,
)
from workspace.main import create_app
from workspace.services.sandbox_provider import (
    LOCAL_WORKSPACE_SUBDIR,
    LocalSandboxProvider,
)

# --- HTTP 端到端(不注入 provider,走 lifespan 的 build 分支) ----------------


@posix_only
def test_http_local_provider_lifecycle(tmp_path: Path) -> None:
    local_root = tmp_path / "sb"
    settings = _settings(
        tmp_path,
        "local.db",
        sandbox_provider="local",
        sandbox_local_root=str(local_root),
    )
    app = create_app(settings, token_verifier=verifier())

    with TestClient(app) as client:
        # lifespan 走 build_sandbox_provider,并打 local 隔离告警
        assert isinstance(app.state.sandbox_provider, LocalSandboxProvider)
        assert app.state.sandbox_provider.provider == "local"

        created = client.post("/v1/sandboxes", json={}, headers=TENANT)
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["provider"] == "local"
        name = body["name"]
        sandbox_dir = _dir_of(local_root, name)
        assert (sandbox_dir / LOCAL_WORKSPACE_SUBDIR).is_dir()

        executed = client.post(
            f"/v1/sandboxes/{body['id']}/exec",
            json={"argv": ["sh", "-c", "printf 'from-local'"]},
            headers=TENANT,
        )
        assert executed.status_code == 200, executed.text
        assert executed.json()["exit_code"] == 0
        assert executed.json()["stdout"] == "from-local"
        assert executed.json()["truncated"] is False

        payload = b"e2e-bytes\n"
        written = client.put(
            f"/v1/sandboxes/{body['id']}/files",
            json={
                "path": "/workspace/notes/hello.txt",
                "content_base64": base64.b64encode(payload).decode("ascii"),
            },
            headers=TENANT,
        )
        assert written.status_code == 204
        read = client.get(
            f"/v1/sandboxes/{body['id']}/files",
            params={"path": "/workspace/notes/hello.txt"},
            headers=TENANT,
        )
        assert read.status_code == 200
        assert base64.b64decode(read.json()["content_base64"]) == payload
        assert read.json()["size_bytes"] == len(payload)
        assert (sandbox_dir / "workspace/notes/hello.txt").read_bytes() == payload

        # 执行现场:根目录只列直接子项,进一层才看到文件
        root_tree = client.get(
            f"/v1/sandboxes/{body['id']}/tree", headers=TENANT
        ).json()
        assert root_tree["path"] == "/workspace"
        assert [(item["name"], item["kind"]) for item in root_tree["entries"]] == [
            ("notes", "dir")
        ]

        notes_tree = client.get(
            f"/v1/sandboxes/{body['id']}/tree",
            params={"path": "/workspace/notes", "depth": 2},
            headers=TENANT,
        )
        assert notes_tree.status_code == 200, notes_tree.text
        assert notes_tree.json()["truncated"] is False
        assert [
            (item["name"], item["kind"], item["size_bytes"])
            for item in notes_tree.json()["entries"]
        ] == [("hello.txt", "file", len(payload))]

        deleted = client.delete(f"/v1/sandboxes/{body['id']}", headers=TENANT)
        assert deleted.status_code == 204
        assert not sandbox_dir.exists()
