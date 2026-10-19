/**
 * 设置存储:默认值、深合并、持久化与损坏文件回落。纯 node、零网络。
 */
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { SETTINGS_FILE_NAME, SettingsStore, createDefaultSettings } from './settings-store';

let userDataDir: string;

beforeEach(async () => {
  userDataDir = await mkdtemp(path.join(tmpdir(), 'beauty-settings-'));
});

afterEach(async () => {
  await rm(userDataDir, { recursive: true, force: true });
});

describe('SettingsStore', () => {
  it('首次读取返回默认值(本地模式 + 空工作区)', async () => {
    const store = new SettingsStore({ userDataDir });
    const settings = await store.read();

    expect(settings.mode).toBe('local');
    expect(settings.workspaceRoot).toBeNull();
    expect(settings.model.baseUrl).toBeTruthy();
    expect(settings.model.model).toBeTruthy();
  });

  it('update 写入文件并可被新实例读回', async () => {
    const store = new SettingsStore({ userDataDir });
    await store.update({ workspaceRoot: '/tmp/project' });

    const raw = await readFile(path.join(userDataDir, SETTINGS_FILE_NAME), 'utf8');
    expect(JSON.parse(raw)).toMatchObject({ workspaceRoot: '/tmp/project' });

    const reopened = new SettingsStore({ userDataDir });
    expect((await reopened.read()).workspaceRoot).toBe('/tmp/project');
  });

  it('部分 model 字段更新时保留其余默认值(深合并)', async () => {
    const store = new SettingsStore({ userDataDir });
    const defaults = createDefaultSettings();

    const updated = await store.update({ model: { ...defaults.model, model: 'qwen2.5-coder' } });
    expect(updated.model.model).toBe('qwen2.5-coder');
    expect(updated.model.baseUrl).toBe(defaults.model.baseUrl);
  });

  it('文件损坏时回落到默认值而不抛错', async () => {
    await writeFile(path.join(userDataDir, SETTINGS_FILE_NAME), '{ not json', 'utf8');
    const store = new SettingsStore({ userDataDir });
    await expect(store.read()).resolves.toMatchObject({ mode: 'local' });
  });

  it('并发 update 不会互相覆盖', async () => {
    const store = new SettingsStore({ userDataDir });
    await Promise.all([
      store.update({ mode: 'cloud' }),
      store.update({ workspaceRoot: '/tmp/a' }),
      store.update({ cloudConsoleUrl: 'http://127.0.0.1:5173/' }),
    ]);

    const settings = await store.read();
    expect(settings.mode).toBe('cloud');
    expect(settings.workspaceRoot).toBe('/tmp/a');
    expect(settings.cloudConsoleUrl).toBe('http://127.0.0.1:5173/');
  });
});
