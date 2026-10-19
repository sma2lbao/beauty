/**
 * Electron 主进程入口。
 *
 * 职责边界:
 * - 建窗口、生命周期、导航安全策略、应用菜单(平台能力);
 * - 组装 DesktopService(设置/会话/工作区/Agent)并交给 ipc.ts 注册通道;
 * - 业务逻辑一律在 service.ts / local/*,本文件不写业务分支。
 */
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { BrowserWindow, Menu, app, session, shell } from 'electron';

import { createDesktopService, registerIpcHandlers, unregisterIpcHandlers } from './ipc';

const currentDir = path.dirname(fileURLToPath(import.meta.url));

/** 开发期由 scripts/dev.mjs 注入;生产为空,走本地构建产物。 */
const devServerUrl = process.env.BEAUTY_DEV_SERVER_URL?.trim() ?? '';

/** 热重载/二次启动时复用同一个窗口。 */
let mainWindow: BrowserWindow | null = null;
let service: ReturnType<typeof createDesktopService> | null = null;

// userData 路径由应用名决定;显式定名,避免落到 "@beauty/desktop" 这种包名目录。
app.setName('beauty moon');

/** 只允许导航到应用自身的页面:dev server 或本地构建产物。 */
function isInternalUrl(rawUrl: string): boolean {
  try {
    const target = new URL(rawUrl);
    if (devServerUrl) {
      const dev = new URL(devServerUrl);
      return target.origin === dev.origin;
    }
    return target.protocol === 'file:';
  } catch {
    return false;
  }
}

function resolveRendererIndex(): string {
  // 构建产物:dist/main/main.js → dist/renderer/index.html
  return path.join(currentDir, '../renderer/index.html');
}

function resolvePreloadScript(): string {
  // 构建产物:dist/main/main.js → dist/preload/index.cjs(CJS,兼容 sandbox)
  return path.join(currentDir, '../preload/index.cjs');
}

/**
 * 统一收敛 webContents 的安全策略:
 * 不做应用内新窗口、不把外部页面装进应用、外部 http(s) 一律交给系统浏览器。
 */
function hardenWebContents(contents: Electron.WebContents): void {
  contents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http://') || url.startsWith('https://')) {
      void shell.openExternal(url);
    }
    return { action: 'deny' };
  });

  contents.on('will-navigate', (event, url) => {
    if (isInternalUrl(url)) return;
    event.preventDefault();
    if (url.startsWith('http://') || url.startsWith('https://')) {
      void shell.openExternal(url);
    }
  });

  contents.on('will-attach-webview', (event) => {
    // 应用不使用 <webview>:直接拒绝,避免绕过上面的导航策略。
    event.preventDefault();
  });
}

function createMainWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1180,
    height: 760,
    minWidth: 940,
    minHeight: 600,
    show: false,
    backgroundColor: '#08090a',
    title: 'beauty moon',
    // macOS 用内嵌红绿灯,让界面自己画顶栏;其它平台用系统标题栏。
    // 用系统标题栏:MVP 不做自绘标题栏,避免「隐藏红绿灯后没有拖拽区」的可用性问题。
    // (后续若要换成 hiddenInset,需要在渲染进程里补 -webkit-app-region: drag 与左上留白。)
    webPreferences: {
      preload: resolvePreloadScript(),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
      // 预加载脚本在 sandbox 下拿不到 app 版本,用启动参数带过去。
      additionalArguments: [`--beauty-app-version=${app.getVersion()}`],
    },
  });

  hardenWebContents(window.webContents);

  window.once('ready-to-show', () => {
    window.show();
  });

  window.on('closed', () => {
    if (mainWindow === window) mainWindow = null;
  });

  if (devServerUrl) {
    void window.loadURL(devServerUrl);
  } else {
    void window.loadFile(resolveRendererIndex());
  }

  if (process.env.BEAUTY_DEVTOOLS === '1') {
    window.webContents.openDevTools({ mode: 'detach' });
  }

  return window;
}

/** 最小应用菜单:保留 macOS 必需项与编辑快捷键(复制/粘贴/全选)。 */
function installApplicationMenu(): void {
  const isMac = process.platform === 'darwin';
  const template: Electron.MenuItemConstructorOptions[] = [
    ...(isMac
      ? [
          {
            label: 'beauty moon',
            submenu: [
              { role: 'about' as const },
              { type: 'separator' as const },
              { role: 'hide' as const },
              { role: 'hideOthers' as const },
              { role: 'unhide' as const },
              { type: 'separator' as const },
              { role: 'quit' as const },
            ],
          },
        ]
      : []),
    {
      label: '编辑',
      submenu: [
        { role: 'undo' },
        { role: 'redo' },
        { type: 'separator' },
        { role: 'cut' },
        { role: 'copy' },
        { role: 'paste' },
        { role: 'selectAll' },
      ],
    },
    {
      label: '视图',
      submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        ...(process.env.BEAUTY_DEVTOOLS === '1'
          ? [{ role: 'toggleDevTools' as const }]
          : []),
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
    { label: '窗口', submenu: [{ role: 'minimize' }, { role: 'close' }] },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

/** 权限策略:本地模式只需要剪贴板写入等少量能力,其余一律拒绝。 */
function configureSessionPermissions(): void {
  session.defaultSession.setPermissionRequestHandler((_contents, permission, callback) => {
    callback(permission === 'clipboard-sanitized-write');
  });
}

const hasSingleInstanceLock = app.requestSingleInstanceLock();
if (!hasSingleInstanceLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  });

  void app.whenReady().then(() => {
    configureSessionPermissions();
    installApplicationMenu();

    // service 依赖 app.getPath('userData'),必须等 ready 之后再建。
    service = createDesktopService();
    registerIpcHandlers(service);

    mainWindow = createMainWindow();

    // 冒烟测试入口:CI/本地验证时用 BEAUTY_SMOKE=1 启动,窗口起来后自动退出。
    if (process.env.BEAUTY_SMOKE === '1') {
      setTimeout(() => {
        console.warn('[desktop] 冒烟测试完成,自动退出。');
        app.quit();
      }, 1500);
    }

    app.on('activate', () => {
      // macOS:点 dock 图标且没有窗口时重开一个。
      if (BrowserWindow.getAllWindows().length === 0) {
        mainWindow = createMainWindow();
      }
    });
  }).catch((error: unknown) => {
    console.error('[desktop] 启动失败:', error);
    app.quit();
  });
}

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

/** 退出时先中止本地会话(可能还有子进程),结束再真正退出。 */
let shuttingDown = false;
app.on('before-quit', (event) => {
  if (shuttingDown || !service) return;
  event.preventDefault();
  shuttingDown = true;
  unregisterIpcHandlers();
  void service.shutdown().finally(() => {
    app.quit();
  });
});

process.on('uncaughtException', (error) => {
  console.error('[desktop] 未捕获异常:', error);
});
process.on('unhandledRejection', (reason) => {
  console.error('[desktop] 未处理的 Promise 拒绝:', reason);
});
