"""工作区替身(FakeWorkspace):交付物镜像与对象读写的内存实现(零网络)。"""

from __future__ import annotations

from common.errors import UpstreamError


class FakeWorkspace:
    """假工作区:记录交付物镜像与对象读写调用,可注入失败(零网络)。

    P3 技能包:put_object 返回固定 sha(测试拿它断言 run 快照),
    key 按租户前缀 + 序号生成(与 workspace-service 契约一致);
    objects 用于 get_object 往返。fail=True 时所有调用都失败。

    工作区归属:default_workspace 固定返回 default_id(记录 default_calls),
    get_workspace 只在 workspaces 里登记过的 id 上命中(未登记 → None 即 404),
    调用参数都记进 workspace_gets,便于断言"是否真的做了存在性校验"。
    """

    def __init__(
        self,
        fail: bool = False,
        *,
        workspaces: dict[str, dict] | None = None,
        default_id: str = "ws-default",
    ):
        self.fail = fail
        self.workspaces = workspaces or {}
        self.default_id = default_id
        self.created: list[dict] = []
        self.objects: dict[str, bytes] = {}
        self.puts: list[dict] = []
        self.gets: list[tuple[str, str]] = []
        self.deleted: list[tuple[str, str]] = []
        self.default_calls: list[str] = []
        self.workspace_gets: list[tuple[str, str]] = []
        self.sha256 = "a" * 64

    async def default_workspace(self, tenant_id: str) -> dict | None:
        """租户默认工作区(与 workspace-service GET /workspaces/default 同形)。"""
        self.default_calls.append(tenant_id)
        if self.fail:  # 与真实客户端同形:上游不可达 → 502 workspace.upstream_error
            raise UpstreamError("workspace.upstream_error", "工作区服务不可达")
        return {"id": self.default_id, "name": "默认工作区", "status": "active"}

    async def get_workspace(self, tenant_id: str, workspace_id: str) -> dict | None:
        """按 id 取工作区;未登记的 id 返回 None(服务层翻译成 404)。"""
        self.workspace_gets.append((tenant_id, workspace_id))
        if self.fail:  # 与真实客户端同形:上游不可达 → 502 workspace.upstream_error
            raise UpstreamError("workspace.upstream_error", "工作区服务不可达")
        return self.workspaces.get(workspace_id)

    async def create_artifact(
        self,
        tenant_id: str,
        *,
        title: str,
        kind: str,
        content: str | None = None,
        run_id: str | None = None,
        filename: str | None = None,
        content_type: str | None = None,
        data: bytes | None = None,
        workspace_id: str | None = None,
    ) -> dict:
        if self.fail:
            raise RuntimeError("工作区镜像失败")
        record = {
            "id": f"ws-art-{len(self.created) + 1}",
            "tenant_id": tenant_id,
            "title": title,
            "kind": kind,
            "content": content,
            "run_id": run_id,
            "filename": filename,
            "content_type": content_type,
            "data": data,
            "workspace_id": workspace_id,
        }
        self.created.append(record)
        return record

    async def put_object(
        self,
        tenant_id: str,
        data: bytes,
        *,
        filename: str | None = None,
        content_type: str = "application/zip",
    ) -> dict:
        if self.fail:
            raise RuntimeError("工作区对象存储失败")
        key = f"{tenant_id}/objects/skill-{len(self.puts) + 1}.zip"
        self.puts.append(
            {
                "tenant_id": tenant_id,
                "key": key,
                "filename": filename,
                "content_type": content_type,
                "size_bytes": len(data),
                "sha256": self.sha256,
                "data": data,
            }
        )
        self.objects[key] = data
        return {"key": key, "size_bytes": len(data), "sha256": self.sha256}

    async def get_object(self, tenant_id: str, key: str) -> bytes:
        self.gets.append((tenant_id, key))
        if self.fail:
            raise RuntimeError("工作区对象存储失败")
        if key not in self.objects:
            raise RuntimeError("对象不存在")
        return self.objects[key]

    async def delete_object(self, tenant_id: str, key: str) -> None:
        self.deleted.append((tenant_id, key))
        if self.fail:
            raise RuntimeError("工作区对象存储失败")
        self.objects.pop(key, None)
