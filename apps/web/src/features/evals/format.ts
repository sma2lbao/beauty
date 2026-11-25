/**
 * 评测页展示格式化:百分比、耗时、时间与状态色。
 */
import type { EvalTotals } from '@/lib/eval-api';

export function formatPercent(rate: number): string {
  if (!Number.isFinite(rate)) return '—';
  const percent = rate * 100;
  return (
    (Number.isInteger(percent) ? percent.toFixed(0) : percent.toFixed(1)) + '%'
  );
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat('zh-CN').format(value);
}

export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms)) return '—';
  if (ms < 1000) return Math.round(ms) + ' ms';
  if (ms < 60_000) return (ms / 1000).toFixed(1) + ' s';
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return minutes + 'm ' + seconds + 's';
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date);
}

export function formatShortSha(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 7) : '—';
}

export function passTone(totals: EvalTotals): 'success' | 'danger' | 'warning' {
  if (totals.failed > 0) return 'danger';
  if (totals.pass_rate < 1) return 'warning';
  return 'success';
}

/** 对比页的带符号数值:0 不带符号,避免把"没变"读成涨跌。 */
export function formatSigned(value: number, digits = 0): string {
  if (!Number.isFinite(value) || value === 0) return '0';
  const sign = value > 0 ? '+' : '';
  const absolute = Math.abs(value);
  const body =
    digits === 0
      ? new Intl.NumberFormat('zh-CN').format(absolute)
      : absolute.toFixed(digits);
  return sign + (value < 0 ? '-' : '') + body;
}

export function formatSignedPercent(delta: number): string {
  if (!Number.isFinite(delta) || delta === 0) return '0%';
  return (delta > 0 ? '+' : '-') + formatPercent(Math.abs(delta));
}

export function formatSignedDuration(delta: number): string {
  if (!Number.isFinite(delta) || delta === 0) return '0 ms';
  return (delta > 0 ? '+' : '-') + formatDuration(Math.abs(delta));
}

/** 差值语义色:lowerIsBetter 用于 tokens/耗时,其余按"越大越好"处理。 */
export function deltaTone(
  delta: number,
  lowerIsBetter = false,
): 'success' | 'danger' | 'neutral' {
  if (!Number.isFinite(delta) || delta === 0) return 'neutral';
  const improved = lowerIsBetter ? delta < 0 : delta > 0;
  return improved ? 'success' : 'danger';
}
