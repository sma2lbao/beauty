# 安全策略与漏洞处置（beauty moon）

> 单一真源：本文档。执行物：CI `security-audit` job + `security/audit-allowlist.json` + `scripts/check-audit-allowlist.cjs`。

## 1. 扫描范围与口径

| 面                       | 工具                                                                            | 阻断性           | 说明                                                                        |
| ------------------------ | ------------------------------------------------------------------------------- | ---------------- | --------------------------------------------------------------------------- |
| JS 生产依赖              | `npm audit --omit=dev --registry=https://registry.npmjs.org --audit-level=high` | **阻断**         | 必须保持 0 high；默认 registry 是镜像（audit 端点 404），必须显式指向 npmjs |
| JS 全量（含 dev 工具链） | `npm audit --registry=...`（完整输出）                                          | 非阻断，报告留档 | dev 工具（nx/vitest/rspress）的传递依赖漏洞不影响生产运行面                 |
| Python 全部依赖          | `uv export --format requirements-txt --no-hashes` → `uvx pip-audit -r`          | **阻断**         | 本地 pypi 不可达，只在 CI 跑                                                |
| 允许清单                 | `node scripts/check-audit-allowlist.cjs`                                        | **阻断**         | 过期条目即失败，强制周期性复核                                              |

## 2. 漏洞处置流程

```text
发现(CI audit / dependabot / 人工)
  → 评估:是否在生产依赖面?是否有可用 fix?
      生产 + 有 fix → 立即升级(走正常 PR + 门禁),不进清单
      生产 + 无 fix → 最高优先级:找替代实现或自行缓解,经 lead 批准临时进清单(期限 ≤ 1 个月)
      dev 链 + 有 fix → 随依赖更新节奏升级
      dev 链 + 无 fix → 进允许清单(owner + reason + expires ≤ 季度)
  → 修复/登记后:CI 必须回绿
  → 季度复盘:逐条复核清单,能修的修,不能修的续期并更新理由
```

- **修复优先于豁免**：`npm audit fix`（无破坏）随时可跑；`--force`（跨 major）须评估兼容性后单独 PR。
- **禁止无期限豁免**：每条 expires 最长季度末；`check-audit-allowlist.cjs` 到期即红。
- **允许清单不是永久白名单**：条目携带 `reason`（为什么安全/为什么修不了）与 `owner`（谁负责跟进）。

## 3. 允许清单（security/audit-allowlist.json）

- 当前登记的全部是 **dev 工具链传递依赖**（axios/brace-expansion/follow-redirects/smol-toml 链，经 rspress/vitest/@nxlv 引入），
  生产依赖面 0 high。
- 字段：`id`（GHSA/OSV 编号）、`advisory`、`ecosystem`、`range`、`reason`、`owner`、`expires`。
- 变更清单走普通 PR（reviewer 关注 reason 是否仍然成立）。

## 4. 依赖自动更新（dependabot）

- 三个 ecosystem 周更（github-actions / npm / pip），已分组（dev 与 prod 分组、patch/minor 自动、major 手工评估），
  配置见 `.github/dependabot.yml`。
- dependabot PR 同样过全部门禁（lint/test/build/security-audit）。

## 5. 报告漏洞

- 私有仓库：直接提 issue / 联系 owner（lead）。
- 公开披露前先内部修复与公告，避免 0-day 窗口。
