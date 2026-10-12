# workspace-service

工作区服务(架构 §3/§7):**工作区 + 交付物元数据 + 对象存储**。
§7 数据模型要点把 `artifacts` 归属到本服务(实体文件在对象存储,COS/R2 阶段 B)。

- `POST /v1/workspaces`、`GET /v1/workspaces`、`GET /v1/workspaces/{id}`:
  工作区(团队资料空间);
- `GET /v1/workspaces/default`:当前租户的默认工作区(**幂等建档**),
  agent-orchestrator 交付时往这里推;
- `POST /v1/artifacts`(标题/类型/正文,可带 `run_id`)、
  `GET /v1/workspaces/{id}/artifacts`(元数据列表)、
  `GET /v1/artifacts/{id}`(元数据 + 正文)、`DELETE /v1/artifacts/{id}`;
- `/healthz` `/readyz`:存活/就绪探针。

交付物内容通过 `common.artifacts.ArtifactStore` 落盘:

- `ARTIFACT_STORE=database`:正文存 `workspace_artifacts.content` 列;
- `ARTIFACT_STORE=filesystem`(默认):正文落 `ARTIFACT_ROOT`
  (键 `<租户>/<工作区>/<交付物>.md`),行里只留 `object_key`/`size_bytes`;
- 换 S3/COS 只需实现该协议,读端契约不变。

**阶段 B(明确未实现)**:云沙箱生命周期(E2B/容器实例)。本服务先把工作区与
交付物这两件确定的事做扎实;沙箱 API 在具备运行环境(E2B key)后再落地,
不提供"看起来能用"的空接口。

命令(仓库根):`npx nx run workspace-service:serve`(:8005)/ `npx nx test workspace-service`
迁移:`cd services/workspace-service && uv run alembic upgrade head`
