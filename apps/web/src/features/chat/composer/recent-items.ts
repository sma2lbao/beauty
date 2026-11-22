/**
 * @ 菜单的「最近使用」记忆。
 *
 * 只存菜单项 key(如 `knowledge:k1`),不存标题或正文;纯 localStorage,
 * 按 MRU 排序、最多 12 条。存储被禁用(隐私模式/配额满)时静默降级为空列表,
 * 不影响菜单本身的可用性。
 */

const STORAGE_KEY = 'beauty:composer-recent';

export const RECENT_LIMIT = 12;

/** 读取最近使用 key;数据损坏或不可用时返回空列表。 */
export function loadRecentKeys(): string[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((key): key is string => typeof key === 'string')
      .slice(0, RECENT_LIMIT);
  } catch {
    return [];
  }
}

/** 写回最近使用 key;写入失败只吞掉异常。 */
export function saveRecentKeys(keys: readonly string[]): void {
  try {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify(keys.slice(0, RECENT_LIMIT)),
    );
  } catch {
    // 存储不可用(隐私模式/配额满)时不影响输入
  }
}

/** 把 key 提到最前并去重,长度截到上限。 */
export function pushRecentKey(keys: readonly string[], key: string): string[] {
  return [key, ...keys.filter((item) => item !== key)].slice(0, RECENT_LIMIT);
}
