# 评测页面演示快照(M0)

这些 JSON 是评测页在 M0 阶段的只读数据源,不是构建产物。

- `index.json`:最近若干次运行的元数据、套件摘要与失败用例;
- `runs/<id>.json`:单次运行的完整报告,含逐用例评分器、轨迹字段与逐轮输入输出。

页面入口:`/evals`(仅管理员,侧栏「管理」组),运行详情:`/evals/runs/<id>`。
M0 只做静态快照,不连数据库;M1 接 eval-service 后页面协议不变,只替换
`apps/web/src/lib/eval-api.ts` 里的两条 URL。

## 从真实运行生成

在仓库根执行:

```sh
bash scripts/eval/platform.sh run
node scripts/eval/publish-web.mjs --limit 2 --trigger local
```

脚本从 `reports/eval/*.json` 选最近 N 份报告,裁掉超长 prompt/reply 与事件,
写到本目录。常用参数:

```sh
node scripts/eval/publish-web.mjs --help
node scripts/eval/publish-web.mjs --limit 5 --branch main --sha <git-sha>
```

演示数据可以随时重新生成;不要把 `reports/eval` 下的完整报告直接提交,
它们可能含更长的模型输入输出。
