/**
 * 会话存储:增删改查、排序、消息追加与状态更新。纯 node、零网络。
 */
import { mkdtemp, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { DEFAULT_SESSION_TITLE, SessionStore } from './session-store';

let directory: string;

beforeEach(async () => {
  directory = await mkdtemp(path.join(tmpdir(), 'beauty-sessions-'));
});

afterEach(async () => {
  await rm(directory, { recursive: true, force: true });
});

describe('SessionStore', () => {
  it('空目录返回空列表', async () => {
    const store = new SessionStore(path.join(directory, 'missing'));
    await expect(store.list()).resolves.toEqual([]);
  });

  it('创建会话:默认标题、绑定工作区、写入 JSON 文件', async () => {
    const store = new SessionStore(directory);
    const session = await store.create({ workspaceRoot: '/tmp/project' });

    expect(session.title).toBe(DEFAULT_SESSION_TITLE);
    expect(session.workspaceRoot).toBe('/tmp/project');
    expect(session.messages).toEqual([]);

    const files = await readdir(directory);
    expect(files).toContain(`${session.id}.json`);
  });

  it('列表按 updatedAt 倒序且不返回消息体', async () => {
    const store = new SessionStore(directory);
    const first = await store.create({ title: '早的' });
    const second = await store.create({ title: '晚的' });
    await store.appendMessage(first.id, { role: 'user', content: '更新一下' });

    const list = await store.list();
    expect(list).toHaveLength(2);
    expect(list[0]?.id).toBe(first.id);
    expect(list[1]?.id).toBe(second.id);
    expect(list[0]).not.toHaveProperty('messages');
    expect(list[0]?.messageCount).toBe(1);
  });

  it('追加消息并更新工具状态', async () => {
    const store = new SessionStore(directory);
    const session = await store.create();

    const appended = await store.appendMessage(session.id, {
      role: 'tool',
      content: '正在执行 list_files…',
      toolName: 'list_files',
      toolStatus: 'running',
    });
    expect(appended.message.toolStatus).toBe('running');

    const updated = await store.updateMessage(session.id, appended.message.id, {
      content: '共 3 项',
      toolStatus: 'ok',
    });
    expect(updated.messages.at(-1)).toMatchObject({
      content: '共 3 项',
      toolName: 'list_files',
      toolStatus: 'ok',
    });
    expect(updated.messageCount).toBe(1);
  });

  it('重命名、删除与非法 id 校验', async () => {
    const store = new SessionStore(directory);
    const session = await store.create();

    const renamed = await store.rename(session.id, '登录页改造');
    expect(renamed.title).toBe('登录页改造');

    await store.remove(session.id);
    await expect(store.get(session.id)).resolves.toBeNull();
    await expect(store.rename(session.id, 'x')).rejects.toThrow();
    await expect(store.get('../escape')).rejects.toThrow();
  });

  it('单个损坏文件不影响列表', async () => {
    const store = new SessionStore(directory);
    const session = await store.create({ title: '正常' });
    await writeFile(path.join(directory, 'broken.json'), '{ 坏数据', 'utf8');

    const list = await store.list();
    expect(list.map((item) => item.id)).toEqual([session.id]);
  });

  it('读取损坏的会话给出可读错误', async () => {
    const store = new SessionStore(directory);
    await writeFile(path.join(directory, '00000000-0000-4000-8000-000000000000.json'), '{', 'utf8');
    await expect(store.get('00000000-0000-4000-8000-000000000000')).rejects.toThrow(
      '会话数据已损坏',
    );
  });
});
