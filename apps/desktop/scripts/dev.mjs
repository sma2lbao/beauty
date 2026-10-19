/**
 * 本地开发:起渲染进程 dev server(热更新),构建一次主进程/预加载脚本,
 * 再用 Electron 打开窗口并指向 dev server 地址。
 *
 * 改了 src/main 或 src/preload 需要重启本命令(MVP 不做主进程热重启);
 * 改了 src/renderer 由 Vite HMR 直接生效。
 */
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

import electron from 'electron';
import { build, createServer } from 'vite';

const projectRoot = fileURLToPath(new URL('..', import.meta.url));
process.chdir(projectRoot);

const renderer = await createServer({ configFile: 'vite.renderer.config.mts' });
await renderer.listen();
const devUrl =
  renderer.resolvedUrls?.local?.[0] ?? 'http://127.0.0.1:5174/';

await build({ configFile: 'vite.main.config.mts', mode: 'development' });
await build({ configFile: 'vite.preload.config.mts', mode: 'development' });

// 受限环境(如 agent 沙箱)里 Chromium 沙箱可能起不来,可用
// BEAUTY_ELECTRON_ARGS="--no-sandbox --disable-gpu" 透传启动参数。
const extraArgs = (process.env.BEAUTY_ELECTRON_ARGS ?? '')
  .trim()
  .split(/\s+/)
  .filter(Boolean);

const child = spawn(electron, ['.', ...extraArgs], {
  stdio: 'inherit',
  env: { ...process.env, BEAUTY_DEV_SERVER_URL: devUrl },
});

let shuttingDown = false;
const shutdown = async (code) => {
  if (shuttingDown) return;
  shuttingDown = true;
  await renderer.close();
  process.exit(code ?? 0);
};

child.on('close', (code) => {
  void shutdown(code ?? 0);
});
process.on('SIGINT', () => {
  child.kill('SIGINT');
  void shutdown(0);
});
process.on('SIGTERM', () => {
  child.kill('SIGTERM');
  void shutdown(0);
});
