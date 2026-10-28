/**
 * 展示格式化:状态色阶与「值 → 文案」的纯函数(时间/字节/文本)。
 *
 * 设计稿(ardot《Harness Agent Web 控制台》)把状态色限定在小面积标签上:
 * 信息 #2EA7FF、成功 #13DDC4、警告 #FFB454、危险 #FF6B81;
 * 这里把后端状态映射成同一套语义,页面只消费 tone,不再各自写颜色。
 *
 * 本文件不依赖领域类型;run/event/tool/diff 的展示映射分别在
 * ./run.ts、./events.ts、./tools.ts、./diff.ts。
 */

export type Tone =
  | 'neutral'
  | 'accent'
  | 'info'
  | 'success'
  | 'warning'
  | 'danger';

type ToneClasses = {
  /** 小面积底(标签/状态点背景) */
  solid: string;
  /** 同色 12% 底 + 同色文字 */
  soft: string;
  /** 同色描边(选中卡片/运行中步骤) */
  outline: string;
  /** 纯文字色 */
  text: string;
  /** 圆点 */
  dot: string;
};

export function toneClasses(tone: Tone): ToneClasses {
  switch (tone) {
    case 'accent':
      return {
        solid: 'bg-ha-accent text-white',
        soft: 'bg-ha-accent/15 text-ha-accent-soft',
        outline: 'border-ha-accent/60 bg-ha-accent/10',
        text: 'text-ha-accent-soft',
        dot: 'bg-ha-accent-soft',
      };
    case 'info':
      return {
        solid: 'bg-ha-info text-[#04121c]',
        soft: 'bg-ha-info-bg text-ha-info',
        outline: 'border-ha-info-border bg-ha-info-bg',
        text: 'text-ha-info',
        dot: 'bg-ha-info',
      };
    case 'success':
      return {
        solid: 'bg-ha-success text-[#04140f]',
        soft: 'bg-ha-success-bg text-ha-success',
        outline: 'border-ha-success-border bg-ha-success-bg',
        text: 'text-ha-success',
        dot: 'bg-ha-success',
      };
    case 'warning':
      return {
        solid: 'bg-ha-warning text-[#1a1103]',
        soft: 'bg-ha-warning-bg text-ha-warning',
        outline: 'border-ha-warning-border bg-ha-warning-bg',
        text: 'text-ha-warning',
        dot: 'bg-ha-warning',
      };
    case 'danger':
      return {
        solid: 'bg-ha-danger text-[#1a070c]',
        soft: 'bg-ha-danger-bg text-ha-danger',
        outline: 'border-ha-danger-border bg-ha-danger-bg',
        text: 'text-ha-danger',
        dot: 'bg-ha-danger',
      };
    default:
      return {
        solid: 'bg-ha-panel-2 text-ha-text-2',
        soft: 'bg-ha-panel-2 text-ha-muted',
        outline: 'border-ha-border bg-ha-panel',
        text: 'text-ha-muted',
        dot: 'bg-ha-ghost',
      };
  }
}

/**
 * 本地化日期时间(zh-CN,与页面原有 formatTime 输出一致)。
 *
 * 空值返回 fallback(设置页用 '—'、MCP 页用 '未测试'),非法字符串原样返回;
 * 默认 fallback 为 '' 以保持 outbox/知识库/工作区原实现在空串上的行为。
 */
export function formatDateTime(
  value: string | null | undefined,
  fallback = '',
): string {
  if (!value) return fallback;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString('zh-CN');
}

/** 相对时间:设计稿里元信息统一用「运行中 · 12 分钟前」这种写法。 */
export function relativeTime(value?: string | null): string {
  if (!value) return '';
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) return '';
  const diff = Date.now() - timestamp;
  const abs = Math.abs(diff);
  const formatter = new Intl.RelativeTimeFormat('zh-CN', { numeric: 'auto' });
  if (abs < 60_000) return '刚刚';
  if (abs < 3_600_000)
    return formatter.format(-Math.round(diff / 60_000), 'minute');
  if (abs < 86_400_000) {
    return formatter.format(-Math.round(diff / 3_600_000), 'hour');
  }
  if (abs < 7 * 86_400_000) {
    return formatter.format(-Math.round(diff / 86_400_000), 'day');
  }
  return new Date(timestamp).toLocaleDateString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
  });
}

export function clockTime(value?: string | null): string {
  if (!value) return '';
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) return '';
  return new Date(timestamp).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

/**
 * 毫秒 → 人类可读耗时(120ms / 1.2s / 1m05s)。
 *
 * 步骤时间线与工具调用共用;空值/非法值返回空串,调用方直接拼进元信息。
 */
export function formatDuration(ms: number | null | undefined): string {
  if (typeof ms !== 'number' || !Number.isFinite(ms) || ms < 0) return '';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60_000)}m${Math.round((ms % 60_000) / 1000)}s`;
}

/**
 * token 数 → 紧凑人类可读文案(999 / 1.2K / 1.2M)。
 *
 * 子代理卡片与用量汇总共用;空值/非法值/负数一律显示 '0'(后端无用量给零值,
 * 不显示空白以免布局跳动)。
 */
export function formatTokens(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) {
    return '0';
  }
  if (value < 1000) return String(Math.round(value));
  if (value < 1_000_000) return `${(value / 1000).toFixed(1)}K`;
  return `${(value / 1_000_000).toFixed(1)}M`;
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * 只到 KB 的旧版大小格式化(知识库文档、工作区交付物沿用)。
 *
 * 与 formatBytes 的差异:≥1MB 时仍按 KB 显示(如 1024.0 KB)。页面可见文案
 * 要求逐字不变,因此暂保留独立实现;后续统一单位时同步改文案与测试。
 */
export function formatSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  return `${(bytes / 1024).toFixed(1)} KB`;
}

export function asText(value: unknown, fallback = ''): string {
  return typeof value === 'string' && value ? value : fallback;
}
