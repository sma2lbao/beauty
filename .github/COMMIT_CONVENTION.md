# 提交规范（Conventional Commits）

> W0 R-06 约定文档。本仓库 git log 实践已是该风格（feat/chore/docs + 可选 scope），此处固化为唯一约定；不引入 commitlint 工具链（W0 非目标），由 PR 自查与 review 把关。

## 格式

```
<type>(<scope>): <subject>
```

- **type**（小写，必填）：
  - `feat`：新功能/新能力
  - `fix`：缺陷修复
  - `docs`：仅文档
  - `refactor`：重构（行为等价；本仓库重构波次的主要 type）
  - `test`：仅测试
  - `chore`：构建/工具/依赖/配置等杂项
  - `build`：构建系统或外部依赖
  - `ci`：CI 配置
  - `perf`：性能优化
  - `style`：格式（不改逻辑）
- **scope**（可选，小写）：改动聚焦的域，如 `web`、`design`、`common`、`agent-orchestrator`、`services`、`env`、`nx`。跨多个不相关域时不写 scope。
- **subject**（必填）：祈使句、句尾不加句号；中文或英文均可，与仓库既有实践一致。

## 本仓实践示例（取自 git log）

```
feat(web): 任务执行期显示 token.delta 实时正文
feat(common): OTel 追踪装配(原生 telemetry + httpx 插桩)
chore(env): 各服务 .env.example 补可观测配置
docs(refactoring): W0 立项（RFC-001 + baseline + roadmap）
ci(gha): R-03 最小 CI + R-06 PR 模板与提交规范
```

## 规则

1. 一个 commit 只做一件事；禁止大爆炸合并（一个 commit 跨多个服务/多个目录层次，playbook §8.3）。
2. 重构 PR 中禁止混入功能/缺陷修复；发现即拆分（playbook §8.3）。
3. `uv.lock`、`package-lock.json` 只能随对应包管理命令产生的变更一起提交，禁止手改。
4. 破坏性变更在 subject 前加 `!`（如 `refactor(api)!: ...`）并在 body 写迁移说明；本仓重构波次要求行为等价，原则上不应出现 `!`。
5. commit 与 PR 序列对应（RFC 的「PR 序列」字段）；每个 commit 可独立 revert（playbook §8.2 回滚剧本）。
