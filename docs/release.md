# 发布流程（beauty moon）

> 版本策略、发版步骤与回滚。单一真源：本文档；执行工具 `npx nx release`（nx 23 自带，无额外依赖）。

## 1. 版本策略

- **全仓单一版本**：所有可发版单元统一版本号（JS 7 个 package.json + 根 pyproject + 10 个 Python 成员）。
  发布单元 = 整仓：一个版本号、一个 tag、一份 workspace CHANGELOG。
- **pre-1.0**：`0.x.y` 阶段允许破坏性变更，**minor 即 breaking**（不使用 `!` 语义化突增）。
- **版本号在哪**：JS 在各 package.json 的 `version`；Python 在各 pyproject.toml 的 `version`；一致性问题由
  `scripts/check-versions.sh` 门禁兜底（断言 18 处版本一致 + CHANGELOG 段存在 + nx.json release 配置存在）。
- Python 成员版本由 uv workspace 统一（成员间依赖写死 `==` 同版本），随 `uv lock` 联动。

## 2. 发版步骤（每一步在仓库根执行）

```sh
# 0) 前置:工作树干净、门禁全绿(refactor-gate + CI)
bash scripts/refactor-gate.sh --affected

# 1) dry-run 看版本变更与 changelog 预览(不落任何文件、不打 tag)
npx nx release --dry-run

# 2) 正式发版:统一 bump 版本、生成 CHANGELOG 段、创建 git commit 与 tag(v<prefix>)
npx nx release <new-version>      # 例: npx nx release 0.2.0

# 3) 版本号变更会牵动锁文件,用包管理命令重生成(禁止手改):
uv lock                            # Python workspace 成员版本
npm install --package-lock-only    # JS workspace 版本

# 4) 提交并推送(main 分支 + tag 一起):
git push origin main --follow-tags
```

- **publish 不启用**（nx.json 无 release.publish 配置）：仓库私有，npm/PyPI 均不发布；
  镜像发布属部署流程（见 deploy/README.md），不在 release 范围。
- CHANGELOG 分组由 conventional commits 决定（feat → 🚀 Features、fix → 🛠 Bug Fixes…），
  提交规范见 .github/COMMIT_CONVENTION.md。

## 3. 回滚

- **代码回滚**：`git revert <release-commit>`；release commit 只含版本号 + CHANGELOG + 锁文件，revert 即恢复。
- **tag 回滚**（未推送时）：`git tag -d v<version>`；已推送的 tag 禁删（历史不可变），用后续版本修正。
- **数据库**：发布不含 schema 变更承诺（迁移走 expand-contract，见根 AGENTS.md「模型/迁移」节）；
  回滚代码不回滚迁移。

## 4. 相关文件

| 文件                         | 作用                                                                              |
| ---------------------------- | --------------------------------------------------------------------------------- |
| nx.json `release` 段         | nx release 配置（projects、conventionalCommits、tagPrefix v、workspaceChangelog） |
| CHANGELOG.md                 | 发布历史（workspace 级，nx release 维护）                                         |
| scripts/check-versions.sh    | 版本一致性门禁（18 处 + CHANGELOG + release 配置）                                |
| .github/COMMIT_CONVENTION.md | 提交规范（changelog 分组的输入）                                                  |
