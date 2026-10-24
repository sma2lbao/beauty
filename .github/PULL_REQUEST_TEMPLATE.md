<!-- W0 R-06：PR 模板（docs/refactoring-playbook.md §11.2 成稿；字段与 §3–6 对应） -->

## 变更内容

- <一句话：改了什么结构，行为是否等价>

## 关联

- RFC-<nnn> / 任务板 <task-id> / 关闭 <gap 条目>

## 行为等价说明

- 未改变的：<接口/事件/DB/UI>
- 有意改变的：<逐条列出，并说明影响与公告方式>

## 验证

<!-- 命令矩阵见 playbook §6.1；受限环境先固化缓存：UV_CACHE_DIR=$PWD/.uv-cache UV_PYTHON_INSTALL_DIR=$PWD/.uv-python -->

- [ ] `npx nx lint <项目>`
- [ ] `npx nx typecheck <项目>`（TS）
- [ ] `npx nx test <项目> -- --run`（TS）/ `npx nx test <项目>`（Python）
- [ ] `npx nx build <项目>`（公共 API）
- [ ] 迁移：`uv run alembic check`（若有 ORM 变更；cd services/<svc> 后执行）
- [ ] 门禁脚本：`scripts/refactor-gate.sh <项目>`
- 测试结果：<通过数/总耗时>；新增测试：<文件列表>

## 指标

| 指标   | 变更前 | 变更后 |
| ------ | ------ | ------ |
| <指标> |        |        |

## 回滚

- 动作：revert <sha> / flag <name> off
- 数据影响：<无 / 需补偿说明>
- 验证：<命令>

## 自查

- [ ] 未混入功能/缺陷修复
- [ ] 未手改生成物/锁文件/.env
- [ ] 未弱化既有测试
- [ ] 旧路径已删除或已建带日期的删除任务
- [ ] 文档单一真源已更新（无新增双真源；被取代的旧文档已移 docs/archive/<yyyy-mm>/）
