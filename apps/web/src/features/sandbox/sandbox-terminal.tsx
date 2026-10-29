/**
 * 执行面板「终端」页签:会话沙箱里的持久 shell 终端。
 *
 * 挂载时开一个持久会话(真 TTY),输出走 SSE 流式追加,输入原样写回 stdin——
 * 所以 cd / 环境变量 / 逐行交互都能跨命令保留。这是沙箱里的 shell,不是本机终端。
 *
 * 能力不可用时(旧后端 / 会话被禁用)降级为单命令模式(每次 sh -lc 一条独立命令):
 * 界面上诚实标出「已降级」,而不是静默换一种行为。
 *
 * 实现拆分:流协议纯函数在 ./sandbox-terminal-stream,会话状态在
 * ./use-sandbox-terminal-session,持久终端界面在 ./sandbox-terminal-session,
 * 降级界面在 ./single-command-terminal;本文件只做重开会话的组装与对外导出。
 */
import { useState } from 'react';

import { TerminalSession } from './sandbox-terminal-session';

// 纯函数保持从原路径可导入(既有引用方与测试零改动)。
export {
  parseSessionEvent,
  stripTerminalControls,
} from './sandbox-terminal-stream';

/**
 * 外层只负责「重开会话」的换 key:key 一变内层整块重挂载,状态自然归零,
 * 不在 effect 里同步 setState 重置(避免级联渲染)。
 */
export function SandboxTerminal({ sandboxId }: { sandboxId: string }) {
  const [restartNonce, setRestartNonce] = useState(0);
  return (
    <TerminalSession
      key={sandboxId + ':' + String(restartNonce)}
      sandboxId={sandboxId}
      onRestart={() => setRestartNonce((value) => value + 1)}
    />
  );
}
