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
