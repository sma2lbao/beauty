# @beauty/desktop · 本地电脑模式

桌面端应用:把 beauty moon 的会话、文件读写与命令执行放到**用户本机**跑,
是 `apps/web` 云端控制台之外的第二运行形态(参考 dsh desktop、WorkBuddy、豆包工作的本地模式)。

```
┌──────────────────────────── apps/desktop ────────────────────────────┐
│ 渲染进程(React)  ←  window.beautyDesktop  ←  预加载(contextBridge) │
│      │                                             │                 │
│      └── 会话/文件/设置界面               IPC 通道(shared/ipc.ts)   │
│                                                    ▼                 │
│ 主进程(Electron)  ──  本地运行时(src/main/local/*)                  │
│      ├── settings-store   设置持久化(userData/settings.json)         │
│      ├── session-store    会话持久化(userData/sessions/<id>.json)    │
│      ├── workspace        工作区文件守卫 + 目录读取 + 文本读取        │
│      ├── command-runner   本机命令执行(超时/输出上限/可取消)        │
│      └── agent            本地 Agent 循环(OpenAI 兼容端点 + 工具)    │
└──────────────────────────────────────────────────────────────────────┘
```

## 两种模式

| 模式     | 数据与执行位置                       | 入口                     |
| -------- | ------------------------------------ | ------------------------ |
| 本地电脑 | 本机:会话 JSON、工作区目录、本机命令 | 本应用(默认模式)         |
| 云端     | 服务端编排(BFF / orchestrator)       | 「打开云端控制台」按钮   |

模式切换只影响界面走哪套数据源;本地工作区与云端工作区互不干扰。

## 常用命令

```sh
npx nx run @beauty/desktop:dev          # 开发:Vite HMR + Electron
npx nx build @beauty/desktop            # 构建:dist/renderer + dist/main + dist/preload
npx nx run @beauty/desktop:start        # 运行已构建产物(需先 build)
npx nx typecheck @beauty/desktop        # 渲染进程 + 主进程两套 tsconfig
npx nx test @beauty/desktop -- --run    # vitest(renderer jsdom + node)
npx nx lint @beauty/desktop             # ESLint
npx nx run @beauty/desktop:package:dir  # electron-builder 目录产物(未签名)
```

冒烟启动(窗口起来 1.5s 后自动退出,适合 CI 检查主进程能否启动):

```sh
cd apps/desktop
BEAUTY_SMOKE=1 npx electron .
```

> 在受限沙箱(如 agent 环境)里 Chromium 自带沙箱可能起不来,报
> `sandbox initialization failed` / SIGTRAP。此时加 `--no-sandbox --disable-gpu`
> (或在 `dev` 时用 `BEAUTY_ELECTRON_ARGS="--no-sandbox --disable-gpu"`),
> 普通桌面环境不需要。

首次需要 Electron 二进制:若 `npm install` 时沙箱内下载失败,可手动放置到
`node_modules/electron/dist`(见文末「疑难」)。

## 目录

| 路径                  | 作用                                                     |
| --------------------- | -------------------------------------------------------- |
| `src/shared/`         | 主进程 ↔ 渲染进程契约(`contract.ts`)与 IPC 通道名(`ipc.ts`),三方只读同一份 |
| `src/main/main.ts`    | Electron 入口:窗口、生命周期、导航安全策略              |
| `src/main/ipc.ts`     | IPC handler 注册与事件推送                               |
| `src/main/local/`     | 可测试的本地运行时(不 import electron,依赖注入)      |
| `src/preload/index.ts`| `window.beautyDesktop` 桥接(CJS 输出,contextBridge)    |
| `src/renderer/`       | 界面:工作台 / 文件 / 设置 / 云端模式                    |
| `scripts/dev.mjs`     | 开发编排:Vite dev server + 构建主进程 + 起 Electron      |

## 模型配置

本地 Agent 走**任意 OpenAI 兼容端点**(设置页可改):

- 云端:例如 `https://api.deepseek.com/v1` + `deepseek-chat`
- 本机:`http://127.0.0.1:11434/v1` + `qwen2.5-coder`(Ollama,apiKey 留空)

> 注意:仓库内 `services/llm-gateway` 对外是自己的 `/v1/completions` 契约(非 OpenAI
> 兼容),要接它需要一个适配层;本轮先按通用 OpenAI 兼容协议实现。

Agent 每轮最多 12 步,可用工具:`list_files`、`read_file`、`write_file`、`run_command`。

## 安全边界

- 渲染进程:`contextIsolation: true`、`nodeIntegration: false`、`sandbox: true`,
  只通过预加载暴露的 `window.beautyDesktop` 访问本机能力。
- 文件访问:所有路径先经工作区守卫解析,拒绝绝对路径与 `..` / 符号链接逃逸;
  读取上限 256KB,目录列表上限 2000 项。
- 命令执行:一律在**工作区根目录**下执行,默认超时 120s,stdout/stderr 各 256KB 截断,
  可被会话取消。
- 外部链接:只允许 http(s),且一律交给系统浏览器打开,不在应用内导航。

## 数据落盘

- 设置:`<userData>/settings.json`
- 会话:`<userData>/sessions/<sessionId>.json`

`userData` 由 Electron 决定(macOS `~/Library/Application Support/beauty moon`)。

## 测试

```sh
cd apps/desktop
npx vitest run --config vitest.config.mts      # 渲染进程(jsdom + 假 bridge)
npx vitest run --config vitest.node.config.mts # 主进程本地运行时(纯 node、零网络)
```

## 已知限制(MVP)

- 主进程改动需要重启 `dev`(未做主进程热重启)。
- 未做自动更新、签名、公证;`package:dir` 只产出未签名目录包。
- 使用系统标题栏(未自绘);若要 `hiddenInset` 需在渲染进程补 `-webkit-app-region: drag` 与左上留白。
- 未设置 CSP:现阶段依赖 `sandbox + contextIsolation + 导航白名单` 隔离,后续补 `script-src 'self'` 策略。
- 云端模式只提供「打开云端控制台」跳转,未在同窗口嵌入 web 控制台。
- 本地 Agent 只带 4 个内置工具,没有 MCP / 技能 / 审批流(后续对齐云端能力)。

## 疑难:Electron 二进制没装上

npm 安装时 postinstall 可能因缓存目录不可写而失败(表现:`node_modules/electron/dist` 不存在,
运行时报 "Electron failed to install correctly")。手动补一次即可:

```sh
# 用国内镜像下载对应平台包,解压到 dist,再写 path.txt(结尾不要带换行)
curl -L -o /tmp/electron.zip \
  https://cdn.npmmirror.com/binaries/electron/44.9.0/electron-v44.9.0-darwin-arm64.zip
unzip -q -o /tmp/electron.zip -d node_modules/electron/dist
printf '%s' 'Electron.app/Contents/MacOS/Electron' > node_modules/electron/path.txt
node_modules/.bin/electron --version
```

Windows/Linux 的 zip 结构与 `path.txt` 内容不同(win 为 `electron.exe`,linux 为 `electron`),按平台替换。
