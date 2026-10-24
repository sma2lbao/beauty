/**
 * 工具调用展示:从 run 事件提取,并把参数 JSON 压成一行摘要。
 */
import type { RunEvent } from '@/lib/runs-api';
import { asText } from './format';

export type ToolCall = {
  seq: number;
  name: string;
  arguments: string;
  ok: boolean;
  detail: string;
  occurredAt?: string;
};

export function toolCallsFrom(events: readonly RunEvent[]): ToolCall[] {
  return events
    .filter((event) => event.type === 'run.tool.called.v1')
    .map((event) => ({
      seq: event.seq,
      name: asText(event.payload.name, 'tool'),
      arguments: asText(event.payload.arguments),
      ok: event.payload.ok !== false,
      detail: asText(event.payload.detail),
      occurredAt: event.occurred_at,
    }));
}

/** 从工具参数 JSON 里提炼一行摘要(read_file 的路径、对外发布的目标…)。 */
export function summarizeArguments(argumentsText: string): string {
  try {
    const parsed = JSON.parse(argumentsText || '{}') as unknown;
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const entries = Object.entries(parsed as Record<string, unknown>);
      if (entries.length === 0) return '';
      return entries
        .slice(0, 2)
        .map(([key, value]) => {
          const text =
            typeof value === 'string' ? value : (JSON.stringify(value) ?? '');
          const short = text.length > 48 ? `${text.slice(0, 48)}…` : text;
          return `${key}=${short}`;
        })
        .join(' · ');
    }
    return String(parsed);
  } catch {
    return argumentsText.length > 64
      ? `${argumentsText.slice(0, 64)}…`
      : argumentsText;
  }
}
