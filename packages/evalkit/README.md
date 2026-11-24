# evalkit

面向 monorepo 的离线 Agent/LLM 评测平台。核心只依赖 Python 标准库:

- **用例是数据**:evals/datasets/<name>/manifest.json + cases.jsonl;
- **adapter 与 grader 分离**:adapter 驱动被测目标,grader 只读 Observation;
- **确定性**:默认脚本化 Fake/录制回放,零网络,可进 CI;
- **基线门禁**:通过状态、得分、token、指标、事件集、工具集发生变化即提示漂移;
- **多格式报告**:JSON / JUnit XML / Markdown / 自包含 HTML。

## 快速开始

在仓库根目录:

```sh
bash scripts/eval/platform.sh validate
bash scripts/eval/platform.sh list --cases
bash scripts/eval/platform.sh run
bash scripts/eval/platform.sh run --suite agent-core
bash scripts/eval/platform.sh run --update-baseline
```

等价 Nx 命令:

```sh
npx nx run evalkit:eval
npx nx run evalkit:eval-baseline
npx nx test evalkit
npx nx lint evalkit
```

## 配置

平台配置在 `evals/evalkit.toml`。相对路径都相对仓库根;每个 `[[suite]]`
指定数据集、adapter(可省略,用 manifest 的默认值)、基线与通过率下限。

adapter 写 `module:ClassName`,由配置动态导入;工厂可接受一个 `options`
映射。adapter 必须提供 `name`、`run(case) -> Observation` 与 `close()`。

## 退出码

| 码  | 含义                                             |
| --- | ------------------------------------------------ |
| 0   | 全部通过且无基线漂移                             |
| 1   | 有用例失败,或未达到 fail_under / token 上限      |
| 2   | 仅基线漂移(确认行为变化后用 `--update-baseline`) |
| 3   | 配置、schema 或 adapter 加载错误                 |
