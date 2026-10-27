import { describe, expect, it } from 'vitest';

import type { SandboxTreeEntry } from './sandbox-api';
import {
  fileTreeFrom,
  fileTreeUnder,
  flattenTree,
  hasTouchedUnder,
  mergeTreeEntries,
  normalizeSandboxPath,
  parentSandboxPath,
  relativeSandboxPath,
  SANDBOX_ROOT,
  toSandboxPath,
  touchedPathSet,
} from './sandbox-tree';

function entry(
  path: string,
  kind: SandboxTreeEntry['kind'] = 'file',
): SandboxTreeEntry {
  return {
    path,
    name: path.replace(/\/+$/, '').split('/').pop() ?? path,
    kind,
    sizeBytes: kind === 'file' ? 1 : 0,
    modified: null,
  };
}

describe('normalizeSandboxPath', () => {
  it('只接受绝对路径,去掉多余斜杠', () => {
    expect(normalizeSandboxPath('/workspace//src/')).toBe('/workspace/src');
    expect(normalizeSandboxPath('/')).toBe('/');
    expect(normalizeSandboxPath('src/a.ts')).toBeNull();
    expect(normalizeSandboxPath('/workspace/../etc')).toBeNull();
    expect(normalizeSandboxPath(null)).toBeNull();
  });
});

describe('toSandboxPath', () => {
  it('相对路径夹到 /workspace 下', () => {
    expect(toSandboxPath('src/a.ts')).toBe('/workspace/src/a.ts');
    expect(toSandboxPath('/workspace/src/a.ts')).toBe('/workspace/src/a.ts');
  });

  it('根外的绝对路径也放进沙箱根,非法路径给 null', () => {
    expect(toSandboxPath('/tmp/x')).toBe('/workspace/tmp/x');
    expect(toSandboxPath('/workspace/../etc/passwd')).toBeNull();
    expect(toSandboxPath('')).toBeNull();
  });
});

describe('relativeSandboxPath / parentSandboxPath', () => {
  it('换算相对路径', () => {
    expect(relativeSandboxPath(SANDBOX_ROOT)).toBe('');
    expect(relativeSandboxPath('/workspace/src/a.ts')).toBe('src/a.ts');
    expect(relativeSandboxPath('/elsewhere/x')).toBe('/elsewhere/x');
  });

  it('父目录', () => {
    expect(parentSandboxPath('/workspace/src/a.ts')).toBe('/workspace/src');
    expect(parentSandboxPath('/workspace')).toBe('/');
    expect(parentSandboxPath('/')).toBe('/');
  });
});

describe('fileTreeFrom', () => {
  it('扁平 entries 变层级,目录优先再按名字排序', () => {
    const roots = fileTreeFrom([
      entry('/workspace/b.ts'),
      entry('/workspace/src', 'dir'),
      entry('/workspace/src/a.ts'),
      entry('/workspace/a.ts'),
    ]);

    expect(roots).toHaveLength(1);
    const workspace = roots[0];
    expect(workspace.path).toBe('/workspace');
    expect(workspace.kind).toBe('dir');
    expect(workspace.children.map((node) => node.name)).toEqual(['src', 'a.ts', 'b.ts']);
    expect(workspace.children[0]?.children.map((node) => node.name)).toEqual(['a.ts']);
  });

  it('缺失的祖先补成目录节点', () => {
    const roots = fileTreeFrom([entry('/workspace/deep/nested/a.ts')]);
    const workspace = roots[0];
    const deep = workspace?.children[0];
    const nested = deep?.children[0];

    expect(deep).toMatchObject({ path: '/workspace/deep', kind: 'dir' });
    expect(nested).toMatchObject({ path: '/workspace/deep/nested', kind: 'dir' });
    expect(nested?.children[0]?.path).toBe('/workspace/deep/nested/a.ts');
  });
});

describe('fileTreeUnder', () => {
  it('渲染 /workspace 的直接内容而不是 workspace 自身', () => {
    const nodes = fileTreeUnder([
      entry('/workspace/src', 'dir'),
      entry('/workspace/src/a.ts'),
      entry('/workspace/README.md'),
    ]);

    expect(nodes.map((node) => node.name)).toEqual(['src', 'README.md']);
  });

  it('指定目录时只取该目录的子项', () => {
    const nodes = fileTreeUnder(
      [entry('/workspace/src/a.ts'), entry('/workspace/src/b.ts')],
      '/workspace/src',
    );

    expect(nodes.map((node) => node.name)).toEqual(['a.ts', 'b.ts']);
  });

  it('根节点不存在时退回整棵树,不丢信息', () => {
    const nodes = fileTreeUnder([entry('/tmp/x')]);

    expect(nodes.map((node) => node.path)).toEqual(['/tmp']);
  });
});

describe('mergeTreeEntries', () => {
  it('按 path 去重且后到的覆盖先到的,顺序稳定', () => {
    const merged = mergeTreeEntries(
      [entry('/workspace/src', 'dir'), entry('/workspace/src/a.ts')],
      [entry('/workspace/src/a.ts'), entry('/workspace/src/b.ts')],
    );

    expect(merged.map((item) => item.path)).toEqual([
      '/workspace/src',
      '/workspace/src/a.ts',
      '/workspace/src/b.ts',
    ]);
    expect(merged.filter((item) => item.path === '/workspace/src/a.ts')).toHaveLength(1);
  });
});

describe('flattenTree', () => {
  it('只展开 expanded 里的目录', () => {
    const roots = fileTreeFrom([
      entry('/workspace/src', 'dir'),
      entry('/workspace/src/a.ts'),
      entry('/workspace/README.md'),
    ]);

    expect(flattenTree(roots, new Set()).map((row) => row.node.path)).toEqual([
      '/workspace',
    ]);
    expect(flattenTree(roots, new Set(['/workspace'])).map((row) => row.node.path)).toEqual([
      '/workspace',
      '/workspace/src',
      '/workspace/README.md',
    ]);
  });
});

describe('触碰路径', () => {
  it('touchedPathSet 把绝对路径归一成相对路径', () => {
    const touched = touchedPathSet([
      '/workspace/src/a.ts',
      'src/b.ts',
      '/workspace/../etc',
      '/workspace/src',
    ]);

    expect([...touched].sort()).toEqual(['src', 'src/a.ts', 'src/b.ts']);
  });

  it('hasTouchedUnder 判断目录下是否有触碰文件,根目录特判', () => {
    const touched = touchedPathSet(['/workspace/src/a.ts']);

    expect(hasTouchedUnder(touched, '/workspace')).toBe(true);
    expect(hasTouchedUnder(touched, '/workspace/src')).toBe(true);
    expect(hasTouchedUnder(touched, '/workspace/docs')).toBe(false);
    expect(hasTouchedUnder(new Set(), '/workspace')).toBe(false);
  });
});
