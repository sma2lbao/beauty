import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  RECENT_LIMIT,
  loadRecentKeys,
  pushRecentKey,
  saveRecentKeys,
} from './recent-items';

const STORAGE_KEY = 'beauty:composer-recent';

beforeEach(() => {
  window.localStorage.clear();
});

describe('最近使用记录', () => {
  it('读不到或数据损坏时返回空列表', () => {
    expect(loadRecentKeys()).toEqual([]);
    window.localStorage.setItem(STORAGE_KEY, 'not-json');
    expect(loadRecentKeys()).toEqual([]);
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ a: 1 }));
    expect(loadRecentKeys()).toEqual([]);
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(['k1', 2, 'k2']));
    expect(loadRecentKeys()).toEqual(['k1', 'k2']);
  });

  it('新选中的提到最前,重复只保留一条,超出上限截断', () => {
    expect(pushRecentKey(['a', 'b'], 'b')).toEqual(['b', 'a']);
    expect(pushRecentKey([], 'a')).toEqual(['a']);

    let keys: string[] = [];
    for (let index = 0; index < RECENT_LIMIT + 3; index += 1) {
      keys = pushRecentKey(keys, `k${index}`);
    }
    expect(keys).toHaveLength(RECENT_LIMIT);
    expect(keys[0]).toBe(`k${RECENT_LIMIT + 2}`);
    expect(keys).not.toContain('k0');
  });

  it('写入后能读回;存储抛错时不抛出', () => {
    saveRecentKeys(['x', 'y']);
    expect(loadRecentKeys()).toEqual(['x', 'y']);

    const spy = vi
      .spyOn(Storage.prototype, 'setItem')
      .mockImplementation(() => {
        throw new Error('quota exceeded');
      });
    expect(() => saveRecentKeys(['z'])).not.toThrow();
    expect(loadRecentKeys()).toEqual(['x', 'y']);
    spy.mockRestore();
  });
});
