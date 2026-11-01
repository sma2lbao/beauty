/**
 * 危险操作确认(`awaiting_confirmation`)→ 待确认操作清单。
 *
 * 后端只在 `run.confirmation.required.v1` 的事件载荷里带 `pending_writes`
 * (graph/nodes/execute.py 收集的 DANGER 工具调用:`{id, name, arguments}`),
 * run 本身不落这份明细,所以卡片的内容必须从事件流里取。
 * 载荷来自外部模型输出,这里按不可信数据防御式解析:任何异常项直接丢弃。
 */
import type { RunEvent } from '@/lib/runs-api';
import { asText } from './format';

export type PendingWrite = {
  /** 工具调用 id(仅用于列表 key,旧事件可能为空) */
  id: string;
  /** 工具名(write_file / publish_post …),空名的项没有展示价值,丢弃 */
  name: string;
  /** 原始参数 JSON 文本;展示层再提炼成一行摘要 */
  arguments: string;
};

/** 事件流 → 最近一次危险确认中断里待确认的操作清单(没有则空数组)。 */
export function pendingWritesFrom(events: readonly RunEvent[]): PendingWrite[] {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event?.type !== 'run.confirmation.required.v1') continue;
    return parsePendingWrites(event.payload?.pending_writes);
  }
  return [];
}

function parsePendingWrites(raw: unknown): PendingWrite[] {
  if (!Array.isArray(raw)) return [];
  const writes: PendingWrite[] = [];
  raw.forEach((entry, index) => {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return;
    const record = entry as Record<string, unknown>;
    const name = asText(record.name).trim();
    if (!name) return;
    writes.push({
      id: asText(record.id) || `pending-${index}`,
      name,
      arguments: asText(record.arguments),
    });
  });
  return writes;
}
