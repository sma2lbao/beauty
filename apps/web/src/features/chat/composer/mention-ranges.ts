/** 正文里的 @token 区间;[start, end) 不含尾随空格。 */
export type TokenRange = {
  refId: string;
  start: number;
  end: number;
  token: string;
};

export type MentionMatch = {
  start: number;
  query: string;
};

export type CommandMatch = {
  start: number;
  query: string;
};

/** @ 前允许的边界:空白或常见中英文标点。 */
const MENTION_BOUNDARY = /[\s(（【，,;；]/;

/** 查询串上限:触发符之后最多 40 个字符。 */
const MAX_MENTION_QUERY = 40;

/**
 * 按一次文本变更重映射 token 区间。
 * 编辑命中区间内部时该 token 失效(删除任意字符即移除引用);
 * 区间之后的 token 跟随位移。
 */
export function remapRanges(
  previous: string,
  next: string,
  ranges: TokenRange[],
): TokenRange[] {
  if (previous === next) return ranges;
  const oldLength = previous.length;
  const newLength = next.length;
  let prefix = 0;
  while (
    prefix < oldLength &&
    prefix < newLength &&
    previous[prefix] === next[prefix]
  ) {
    prefix += 1;
  }
  let suffix = 0;
  while (
    suffix < oldLength - prefix &&
    suffix < newLength - prefix &&
    previous[oldLength - 1 - suffix] === next[newLength - 1 - suffix]
  ) {
    suffix += 1;
  }
  const delta = newLength - oldLength;
  const tailStart = oldLength - suffix;
  return ranges
    .map((range) => {
      if (range.end <= prefix) return range;
      if (range.start >= tailStart) {
        return {
          ...range,
          start: range.start + delta,
          end: range.end + delta,
        };
      }
      return null;
    })
    .filter((range): range is TokenRange => range !== null);
}

/**
 * 检测光标前的 @ 触发。
 * 粘贴与输入法组合中不触发;光标落在已有 token 内不触发。
 */
export function detectMention(
  value: string,
  caret: number,
  ranges: TokenRange[],
  inputType?: string,
): MentionMatch | null {
  if (inputType === 'insertFromPaste') return null;
  if (caret < 0 || caret > value.length) return null;
  const start = value.lastIndexOf('@', caret - 1);
  if (start < 0) return null;
  if (start > 0 && !MENTION_BOUNDARY.test(value[start - 1])) return null;
  const query = value.slice(start + 1, caret);
  if (query.includes('\n') || query.includes('@')) return null;
  if (query.length > MAX_MENTION_QUERY) return null;
  const insideToken = ranges.some(
    (range) => start < range.end && caret > range.start,
  );
  if (insideToken) return null;
  return { start, query };
}

/**
 * 检测消息开头的 / 触发。
 * 只在光标位于末尾、去掉前导空白后以 / 开头、且命令词不含空白时成立。
 */
export function detectCommand(
  value: string,
  caret: number,
  composing = false,
): CommandMatch | null {
  if (composing || caret !== value.length) return null;
  const trimmed = value.trimStart();
  if (!trimmed.startsWith('/')) return null;
  const query = trimmed.slice(1);
  if (query.includes('\n') || /\s/.test(query)) return null;
  return { start: value.length - trimmed.length, query };
}

/** 标题超过 32 字截断为前 31 字 + 省略号。 */
export function truncateTitle(title: string, max = 32): string {
  const chars = Array.from(title);
  if (chars.length <= max) return title;
  return `${chars.slice(0, max - 1).join('')}…`;
}
