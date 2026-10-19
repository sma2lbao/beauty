/**
 * 工作区文件守卫与读写:重点验证「越界一律拒绝」。
 * 纯 node 测试,用 os.tmpdir() 下的临时目录,零网络。
 */
import { mkdir, mkdtemp, realpath, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  MAX_TEXT_FILE_BYTES,
  WorkspaceError,
  listDirectory,
  readTextFile,
  resolveWithin,
  writeTextFile,
} from './workspace';

let root: string;

beforeEach(async () => {
  root = await mkdtemp(path.join(tmpdir(), 'beauty-workspace-'));
});

afterEach(async () => {
  await rm(root, { recursive: true, force: true });
});

describe('resolveWithin', () => {
  it('把相对路径解析到工作区内', async () => {
    await mkdir(path.join(root, 'src'));
    // 守卫会先在 realpath 上解析(macOS 的 /var 是指向 /private/var 的符号链接),
    // 所以期望值也按真实根目录拼接。
    const realRoot = await realpath(root);
    const resolved = await resolveWithin(root, 'src/app.ts');
    expect(resolved).toBe(path.join(realRoot, 'src/app.ts'));
  });

  it('拒绝绝对路径', async () => {
    await expect(resolveWithin(root, '/etc/passwd')).rejects.toMatchObject({
      code: 'invalid-path',
    });
  });

  it('拒绝 .. 逃逸', async () => {
    await expect(resolveWithin(root, '../outside.txt')).rejects.toMatchObject({
      code: 'outside-root',
    });
    await expect(resolveWithin(root, 'src/../../outside.txt')).rejects.toMatchObject({
      code: 'outside-root',
    });
  });

  it('拒绝指向工作区之外的符号链接', async () => {
    const outside = await mkdtemp(path.join(tmpdir(), 'beauty-outside-'));
    try {
      await writeFile(path.join(outside, 'secret.txt'), 'top secret', 'utf8');
      await symlink(outside, path.join(root, 'link'));
      await expect(resolveWithin(root, 'link/secret.txt')).rejects.toMatchObject({
        code: 'outside-root',
      });
    } finally {
      await rm(outside, { recursive: true, force: true });
    }
  });

  it('工作区目录不存在时给出可读错误', async () => {
    await expect(resolveWithin(path.join(root, 'missing'), '')).rejects.toBeInstanceOf(
      WorkspaceError,
    );
  });

  it('拒绝空白根目录', async () => {
    await expect(resolveWithin('   ', '')).rejects.toMatchObject({ code: 'root-missing' });
  });
});

describe('listDirectory', () => {
  it('目录在前、文件在后,并返回相对路径', async () => {
    await mkdir(path.join(root, 'src'));
    await writeFile(path.join(root, 'README.md'), '# hi', 'utf8');

    const entries = await listDirectory(root, '');

    expect(entries.map((entry) => entry.name)).toEqual(['src', 'README.md']);
    expect(entries[0]).toMatchObject({ path: 'src', kind: 'directory' });
    expect(entries[1]).toMatchObject({ path: 'README.md', kind: 'file' });
  });

  it('按上限截断', async () => {
    await Promise.all(
      Array.from({ length: 5 }, (_value, index) =>
        writeFile(path.join(root, `file-${index}.txt`), 'x', 'utf8'),
      ),
    );
    const entries = await listDirectory(root, '', { maxEntries: 3 });
    expect(entries).toHaveLength(3);
  });

  it('对文件路径报「不是目录」', async () => {
    await writeFile(path.join(root, 'a.txt'), 'x', 'utf8');
    await expect(listDirectory(root, 'a.txt')).rejects.toMatchObject({ code: 'not-directory' });
  });
});

describe('readTextFile', () => {
  it('返回文本内容', async () => {
    await writeFile(path.join(root, 'a.txt'), 'hello 世界', 'utf8');
    const file = await readTextFile(root, 'a.txt');
    expect(file).toMatchObject({ path: 'a.txt', content: 'hello 世界', truncated: false });
  });

  it('拒绝二进制文件', async () => {
    await writeFile(path.join(root, 'bin.dat'), Buffer.from([0x00, 0x01, 0x02]));
    await expect(readTextFile(root, 'bin.dat')).rejects.toMatchObject({ code: 'binary' });
  });

  it('超过上限时截断并标记', async () => {
    const huge = 'a'.repeat(MAX_TEXT_FILE_BYTES + 1024);
    await writeFile(path.join(root, 'huge.txt'), huge, 'utf8');
    const file = await readTextFile(root, 'huge.txt');
    expect(file.truncated).toBe(true);
    expect(file.content.length).toBeLessThanOrEqual(MAX_TEXT_FILE_BYTES);
    expect(file.size).toBe(Buffer.byteLength(huge, 'utf8'));
  });

  it('拒绝目录', async () => {
    await mkdir(path.join(root, 'dir'));
    await expect(readTextFile(root, 'dir')).rejects.toMatchObject({ code: 'not-file' });
  });
});

describe('writeTextFile', () => {
  it('自动创建父目录并写盘', async () => {
    const result = await writeTextFile(root, 'deep/nested/file.ts', 'export const a = 1;\n');
    expect(result.path).toBe('deep/nested/file.ts');
    const file = await readTextFile(root, 'deep/nested/file.ts');
    expect(file.content).toBe('export const a = 1;\n');
  });

  it('写入路径同样受守卫保护', async () => {
    await expect(writeTextFile(root, '../escape.txt', 'x')).rejects.toMatchObject({
      code: 'outside-root',
    });
  });
});
