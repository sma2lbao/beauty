# workspace-service

工作区服务(架构 §3/§7):**工作区 + 交付物元数据 + 对象存储 + 沙箱生命周期**。

§7 数据模型要点把 `artifacts` 归属到本服务(实体文件在对象存储,COS/R2 阶段 B)。

- `POST /v1/workspaces`、`GET /v1/workspaces`、`GET /v1/workspaces/{id}`:
  工作区(团队资料空间);
- `GET /v1/workspaces/default`:当前租户的默认工作区(**幂等建档**),
  agent-orchestrator 交付时往这里推;
- `POST /v1/artifacts`(标题/类型 + 正文或二进制,可带 `run_id`):body 里
  `content`(文本)与 `content_base64`(二进制)**恰好给一个**(都给/都不给 422),
  可带 `filename`/`content_type`;
- `GET /v1/workspaces/{id}/artifacts`(元数据列表)、`GET /v1/artifacts/{id}`
  (元数据 + 文本正文)、`GET /v1/artifacts/{id}/download`(原始字节 + 附件头
  `Content-Disposition: attachment; filename*=UTF-8''…` + `Cache-Control: private, no-store`)、
  `DELETE /v1/artifacts/{id}`;
- `/healthz` `/readyz`:存活/就绪探针。

交付物内容通过 `common.artifacts.ArtifactStore` 落盘:

- 文本类(`text/*`、JSON/YAML/XML 等)进 `content` 列;二进制进 `content_bytes`
  列(下载端点按 MIME 取原始字节,不再假设"交付物一定是 Markdown");
- `ARTIFACT_STORE=database`:上述两列即正文;
- `ARTIFACT_STORE=filesystem`(默认):字节直接写 `ARTIFACT_ROOT`
  (键 `<租户>/<工作区>/<交付物><安全后缀>`,后缀由 MIME 推导、bin 兜底),
  行里只留 `object_key`/`size_bytes`/`filename`/`content_type`;
- 换 S3/COS 只需实现该协议,读端契约不变。

## 对象存储 API(P3:skill 包)

通用二进制对象接口,供 agent-orchestrator 存用户上传的 skill 包(zip):内容直接落
`ArtifactStore`,**不登记 `workspace_artifacts` 行**(本 API 没有 DB 表),
因此与交付物元数据互不影响。

- `POST /v1/objects`(201):body `{filename?, content_type?, content_base64}`
  (默认 `application/octet-stream`),返回 `{key, size_bytes, sha256}`。
  `filename` **只用于安全后缀**(`.zip`/`.skill` 等,默认空),不参与路径;
  key 形如 `<租户>/objects/<uuid4hex><后缀>`;
- `GET /v1/objects?key=`:返回 `{key, content_base64, size_bytes, sha256}`;
- `DELETE /v1/objects?key=`(204):幂等,只删对象,不删任何 DB 行。

错误码:

| 场景                                          | 错误码                   | HTTP |
| --------------------------------------------- | ------------------------ | ---- |
| Base64 非法                                   | `object.content_invalid` | 400  |
| 超过 `OBJECT_MAX_BYTES`                       | `object.too_large`       | 400  |
| key 不存在或不属于当前租户                    | `object.not_found`       | 404  |
| 对象存储后端未配置(`ARTIFACT_STORE=database`) | `object.store_missing`   | 503  |

跨租户读取/删除一律 404(与不存在不可区分);`size_bytes`/`sha256` 均对解码后的
原始字节计算。配置:`WORKSPACE__OBJECT_MAX_BYTES`(默认 `20971520`,即 20MB)。

## 沙箱(§7 阶段 B / P1)

执行底座是 [microsandbox](https://github.com/superradcompany/microsandbox)(microVM
隔离),为后续**用户 skill / stdio MCP** 提供执行环境。**P1 已落地**:provider 抽象、
沙箱实例生命周期、HTTP API 与过期回收;stdio MCP / skill 打包在 P2/P3。

接口:

- `POST /v1/sandboxes`(201):创建沙箱。body 支持 `kind`(skill|mcp|generic)、
  `image`、`ttl_seconds`、`cpus`、`memory_mb`、`network`(deny|public)、
  `workspace_id`、`run_id`;不传资源时用服务端默认值。`deny` 完全断网;
  `public` 只放公网(走 microsandbox `NetworkProfile.PUBLIC`),**不包含宿主/私网**;
- `GET /v1/sandboxes/{id}`:实例详情;
- `POST /v1/sandboxes/{id}/exec`:执行命令(`argv` 不经过 shell),
  返回 `exit_code/stdout/stderr/duration_ms/truncated`,成功后滑动续期
  (单次 TTL 上限 `sandbox_max_ttl_seconds`);
- `PUT /v1/sandboxes/{id}/files`(204)、`GET /v1/sandboxes/{id}/files?path=`:
  文件读写,内容 Base64;路径必须在 `/workspace` 下且不能含 `..`;
- `POST /v1/sandboxes/{id}/touch`:显式续期;
- `DELETE /v1/sandboxes/{id}`(204):先销毁运行时,再删记录。

实现要点:

- 业务层只依赖 `SandboxProvider` 协议;microsandbox **惰性导入**,
  测试与没有 runtime 的 dev 启动不加载 SDK;
- 创建时**先落库** `status=creating` 再拉起运行时;失败保留
  `status=failed` + `last_error`(可追踪),对外统一 502;
- 过期实例在 exec/文件操作前校验:先销毁运行时,再返回 409 `sandbox.expired`;
  后台 reaper(`sandbox_reaper_enabled`/`sandbox_reaper_interval_seconds`)
  周期回收 `expires_at` 已过的实例,单次失败只记录 `last_error`,不阻塞其他实例;
- provider 异常统一收口(不再把 SDK 堆栈泄漏成 500):

| 场景                           | 错误码                                            | HTTP |
| ------------------------------ | ------------------------------------------------- | ---- |
| 运行时未安装/未启用            | `sandbox.provider_unavailable`                    | 502  |
| 运行时操作失败/超时            | `sandbox.provider_error` / `sandbox.exec_timeout` | 502  |
| 沙箱过期                       | `sandbox.expired`                                 | 409  |
| 沙箱不存在/跨租户              | `sandbox.not_found`                               | 404  |
| 路径非法(非 /workspace、含 ..) | `sandbox.path_invalid`                            | 400  |
| 沙箱内文件不存在               | `sandbox.file_not_found`                          | 404  |

配置(env 前缀 `WORKSPACE__`,括号内为默认值):

| 配置项                                                                  | 说明                                                                         |
| ----------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| `SANDBOX_PROVIDER`(microsandbox)                                        | `microsandbox` 用真 microVM;`unavailable` 显式返回 502,供没有 runtime 的 dev |
| `SANDBOX_DEFAULT_IMAGE`(python)                                         | microsandbox 镜像别名                                                        |
| `SANDBOX_DEFAULT_TTL_SECONDS`(1800) / `SANDBOX_MAX_TTL_SECONDS`(7200)   | 默认/上限 TTL                                                                |
| `SANDBOX_DEFAULT_CPUS`(1.0) / `SANDBOX_DEFAULT_MEMORY_MB`(512)          | 默认配额                                                                     |
| `SANDBOX_NETWORK`(deny)                                                 | 默认网络策略(`deny`/`public`)                                                |
| `SANDBOX_EXEC_TIMEOUT_SECONDS`(60) / `SANDBOX_MAX_OUTPUT_BYTES`(262144) | 执行超时与输出截断上限                                                       |
| `SANDBOX_REAPER_ENABLED`(true) / `SANDBOX_REAPER_INTERVAL_SECONDS`(60)  | 过期回收开关/间隔                                                            |
| `SANDBOX_MSB_HOME`("")                                                  | microsandbox runtime home(空=SDK 默认 `~/.microsandbox`)                     |

**无 runtime / 部署注意**:

- 没有 microsandbox runtime 或 SDK 未安装时,创建沙箱返回明确的
  `502 sandbox.provider_unavailable`,**不提供"看起来能用"的空接口**;
- 部署时 `MSB_HOME`(或 `WORKSPACE__SANDBOX_MSB_HOME`)必须指向**可写目录**
  ——默认的 `~/.microsandbox` 在容器/受限环境里往往不可写;
- 首次创建需要拉 OCI 镜像,runtime 必须能访问 registry(如 index.docker.io);
  网络策略 `deny` 只限制**沙箱内**出网,不影响宿主侧拉镜像。

测试(零网络、零 runtime):`tests/conftest.py` 的 `FakeSandboxProvider`
注入内存文件表与固定执行输出,`create_app(..., sandbox_provider=fake)` 覆盖
生命周期/隔离/路径校验/过期回收/故障收口,不会启动真实 microVM。

命令(仓库根):`npx nx run workspace-service:serve`(:8005)/ `npx nx test workspace-service`
迁移:`cd services/workspace-service && uv run alembic upgrade head`
