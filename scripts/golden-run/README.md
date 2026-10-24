# 黄金 run(端到端冒烟)

R-33 的跨重构回归网:用 TestClient 在进程内驱动 agent-orchestrator 的真实
FastAPI 应用(不起端口、不依赖已启动的服务),用脚本化 FakeLLM 与合成 DANGER
工具走完一条完整业务链路:

    健康探针 → 会话 → 流式对话(meta/delta/done) → 复杂任务建 run →
    计划审批 → 危险操作确认 → 交付物 → 事件序列/SSE 重放 → sqlite 落库断言

## 用法

    bash scripts/golden-run/golden-run.sh           # 临时 sqlite,跑完清理
    bash scripts/golden-run/golden-run.sh --keep    # 保留到 .cache/golden-run/<UTC 时间戳>/

退出码:0 = 全部阶段 PASS;1 = 有阶段 FAIL(打印阶段表与失败断言)。

## 为什么需要它

跨重构批次(R-27 graph 去反向依赖、R-28 拆 services/runs.py、R-29 API 瘦身)
只允许改内部结构,不允许改对外 HTTP 契约与 SSE 事件序列。黄金 run 只断言:

- REST 路径与响应字段、run/会话状态机流转;
- SSE 帧格式与事件顺序(run.created.v1 → ... → run.completed.v1);
- 交付物正文/清单/下载字节一致;
- sqlite 最终状态(runs / run_events / run_artifacts / chat_messages)。

它不 import graph 或 services 内部模块,因此重构内部实现不应让它变红;
真变了就说明契约被破坏。

## 零网络与零外部依赖

- 模型:脚本化 FakeLLM(按调用次序吐回复,带一次「只读检索 + 危险写操作」的
  function call);知识库:内存假实现;鉴权:common.testing.TestIdP 本地密钥 +
  JWKS MockTransport;
- 进程内 socket 守卫把任何 AF_INET / AF_INET6 connect 视为失败;
- 不需要真实模型、Docker、Postgres、Redis 或任何已启动的服务;
- 所有会连外部的配置开关在建 Settings 时显式钉死(_env_file=None),
  不受本地 .env 与 shell 环境变量影响。

## 维护约定

- 契约变化(端点/字段/事件名/顺序)时,先改本脚本的断言,再改实现——脚本是
  契约的可执行文档;
- FakeLLM 的脚本条数即「模型调用次数契约」:多一次/少一次调用都会失败;
- 失败时 --keep 保留 sqlite,便于用 sqlite3 .cache/golden-run/<ts>/golden.db
  对照阶段表排查。
