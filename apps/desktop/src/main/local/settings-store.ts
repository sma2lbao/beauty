/**
 * 桌面设置存储(userData/settings.json)。
 *
 * 纯 Node 逻辑,不依赖 electron:目录由调用方注入,便于测试。
 * 读取时总是与默认值做深合并,这样旧版本写入的残缺配置也能安全升级;
 * 写入采用「临时文件 + rename」原子替换,避免进程崩溃时留下半截 JSON。
 */
import { mkdir, readFile, rename, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';

import type { DesktopSettings, LocalMode, ModelSettings } from '@shared/contract';

/** 设置文件名(位于 userData 目录下)。 */
export const SETTINGS_FILE_NAME = 'settings.json';

/** 模型端点的出厂默认值:DeepSeek 官方 OpenAI 兼容端点。 */
const DEFAULT_MODEL: Readonly<ModelSettings> = {
  baseUrl: 'https://api.deepseek.com/v1',
  model: 'deepseek-chat',
  apiKey: '',
};

/** 生成一份全新的默认设置(每次返回新对象,避免调用方误改共享常量)。 */
export function createDefaultSettings(): DesktopSettings {
  return {
    mode: 'local',
    cloudConsoleUrl: 'http://127.0.0.1:5173',
    model: { ...DEFAULT_MODEL },
    workspaceRoot: null,
  };
}

/** 只读的默认设置快照,供断言/文档使用;需要修改时请用 createDefaultSettings。 */
export const DEFAULT_SETTINGS: DesktopSettings = createDefaultSettings();

function isLocalMode(value: unknown): value is LocalMode {
  return value === 'local' || value === 'cloud';
}

/**
 * 把任意来源(磁盘文件、IPC 入参)的设置片段合并进基线设置。
 * 只接受类型正确的字段,非法值回落到基线,避免界面因脏数据崩溃。
 */
export function mergeSettings(
  base: DesktopSettings,
  patch: Partial<DesktopSettings> | Record<string, unknown> | null | undefined,
): DesktopSettings {
  const merged: DesktopSettings = {
    mode: base.mode,
    cloudConsoleUrl: base.cloudConsoleUrl,
    workspaceRoot: base.workspaceRoot,
    model: { ...base.model },
  };
  if (!patch || typeof patch !== 'object') return merged;

  if (isLocalMode(patch.mode)) merged.mode = patch.mode;

  if (typeof patch.cloudConsoleUrl === 'string' && patch.cloudConsoleUrl.trim()) {
    merged.cloudConsoleUrl = patch.cloudConsoleUrl.trim();
  }

  // workspaceRoot:undefined 表示「不改」,null 表示「清除」,字符串表示「切到该目录」。
  if (patch.workspaceRoot === null) {
    merged.workspaceRoot = null;
  } else if (typeof patch.workspaceRoot === 'string' && patch.workspaceRoot.trim()) {
    merged.workspaceRoot = patch.workspaceRoot.trim();
  }

  const patchModel = patch.model;
  if (patchModel && typeof patchModel === 'object') {
    const model = patchModel as Partial<Record<keyof ModelSettings, unknown>>;
    if (typeof model.baseUrl === 'string') merged.model.baseUrl = model.baseUrl.trim();
    if (typeof model.model === 'string') merged.model.model = model.model.trim();
    if (typeof model.apiKey === 'string') merged.model.apiKey = model.apiKey;
  }

  return merged;
}

export interface SettingsStoreOptions {
  /** Electron 的 app.getPath('userData');测试里换成临时目录。 */
  userDataDir: string;
}

/** 串行化的 Promise 队列:避免并发 update 互相覆盖(读-改-写竞态)。 */
function createTaskQueue(): <T>(task: () => Promise<T>) => Promise<T> {
  let tail: Promise<unknown> = Promise.resolve();
  return <T>(task: () => Promise<T>): Promise<T> => {
    const run = tail.then(task, task);
    // 队列只关心先后顺序,不关心单个任务成败。
    tail = run.then(
      () => undefined,
      () => undefined,
    );
    return run;
  };
}

export class SettingsStore {
  readonly filePath: string;

  private readonly enqueue = createTaskQueue();
  private cache: DesktopSettings | null = null;

  constructor(options: SettingsStoreOptions) {
    this.filePath = path.join(options.userDataDir, SETTINGS_FILE_NAME);
  }

  /** 读取设置;文件不存在或内容损坏时回落到默认值(不阻断应用启动)。 */
  async read(): Promise<DesktopSettings> {
    if (this.cache) return { ...this.cache, model: { ...this.cache.model } };

    const defaults = createDefaultSettings();
    try {
      const raw = await readFile(this.filePath, 'utf8');
      const parsed: unknown = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        return defaults;
      }
      const merged = mergeSettings(defaults, parsed as Partial<DesktopSettings>);
      this.cache = merged;
      return { ...merged, model: { ...merged.model } };
    } catch {
      // ENOENT / JSON 语法错误:都视为"还没有可用的设置",保持默认值。
      return defaults;
    }
  }

  /** 合并写入一份设置补丁,返回写入后的完整设置。 */
  async update(patch: Partial<DesktopSettings>): Promise<DesktopSettings> {
    return this.enqueue(async () => {
      const current = await this.read();
      const next = mergeSettings(current, patch);
      await this.write(next);
      this.cache = next;
      return { ...next, model: { ...next.model } };
    });
  }

  private async write(settings: DesktopSettings): Promise<void> {
    await mkdir(path.dirname(this.filePath), { recursive: true });
    const tmpPath = `${this.filePath}.${process.pid}.${Date.now()}.${Math.random()
      .toString(16)
      .slice(2)}.tmp`;
    try {
      await writeFile(tmpPath, `${JSON.stringify(settings, null, 2)}\n`, 'utf8');
      await rename(tmpPath, this.filePath);
    } catch (error) {
      await rm(tmpPath, { force: true }).catch(() => undefined);
      throw error;
    }
  }
}
