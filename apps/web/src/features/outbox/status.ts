/**
 * outbox 状态 → 中文文案/徽标色调的映射(计数卡与事件列表共用)。
 */
import type { OutboxStatus } from '@/lib/outbox-api';

export const OUTBOX_STATUSES: OutboxStatus[] = [
  'pending',
  'dispatched',
  'failed',
];

export const STATUS_LABELS: Record<OutboxStatus, string> = {
  pending: '待投递',
  dispatched: '已投递',
  failed: '已失败',
};

export const STATUS_COLORS: Record<
  OutboxStatus,
  'warning' | 'success' | 'danger'
> = {
  pending: 'warning',
  dispatched: 'success',
  failed: 'danger',
};
