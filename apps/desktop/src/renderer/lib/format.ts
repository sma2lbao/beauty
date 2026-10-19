/**
 * 界面用的纯函数格式化工具:时间、体积、路径、错误文案。
 * 与 apps/web 的 presentation 工具同源,这里只保留桌面端用得到的最小集。
 */

/** 相对时间:刚刚 / 3 分钟前 / 2 小时前 / 昨天 / 10月13日。 */
export function relativeTime(value?: string | null): string {
  if (!value) return '';
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) return '';
  const diff = Date.now() - timestamp;
  const abs = Math.abs(diff);
  const format = new Intl.RelativeTimeFormat('zh-CN', { numeric: 'auto' });
  if (abs < 60_000) return '刚刚';
  if (abs < 3_600_000) {
    return format.format(-Math.round(diff / 60_000), 'minute');
  }
  if (abs < 86_400_000) {
    return format.format(-Math.round(diff / 3_600_000), 'hour');
  }
  if (abs < 7 * 86_400_000) {
    return format.format(-Math.round(diff / 86_400_000), 'day');
  }
  return new Date(timestamp).toLocaleDateString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
  });
}

/** 时:分,用于消息与事件的时间戳。 */
export function clockTime(value?: string | null): string {
  if (!value) return '';
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) return '';
  return new Date(timestamp).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** 文件体积:B / KB / MB,保留一位小数。 */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** 路径末段(兼容 POSIX 与 Windows 分隔符)。 */
export function baseName(path: string): string {
  const normalized = path.replace(/[\\/]+$/, '');
  const index = normalized.lastIndexOf('/');
  return index === -1 ? normalized : normalized.slice(index + 1);
}

/** 把绝对路径拆成面包屑片段,根段(盘符或首段)保留原样。 */
export function pathSegments(path: string): string[] {
  const normalized = path.replace(/\\/g, '/').replace(/\/+$/, '');
  if (!normalized) return [];
  const segments = normalized.split('/');
  return segments[0] === ''
    ? segments.slice(1).map((item) => `/${item}`)
    : segments;
}

/** 统一把未知错误转成可读中文文案。 */
export function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === 'string' && error.trim()) return error;
  return '发生未知错误,请重试';
}

/** 文本是否像二进制内容(用于预览时的可读提示)。 */
export function looksBinary(content: string): boolean {
  const sample = content.slice(0, 4096);
  for (let index = 0; index < sample.length; index += 1) {
    const code = sample.charCodeAt(index);
    if (code === 0) return true;
    if (code < 9 || (code > 13 && code < 32)) return true;
  }
  return false;
}
