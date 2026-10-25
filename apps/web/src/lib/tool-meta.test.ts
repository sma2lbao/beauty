import { describe, expect, it } from 'vitest';

import { formatDuration } from '@/lib/presentation';
import { toolCallMetaFrom } from '@/lib/tool-meta';

describe('toolCallMetaFrom', () => {
  it('归一化后端 snake_case 富字段', () => {
    const meta = toolCallMetaFrom({
      name: 'write_file',
      tool_call_id: 'tc_1',
      risk: 'destructive',
      decision: 'confirmed',
      error_code: 'tool.confirmation_required',
      duration_ms: '1200',
      result_chars: 42,
      truncated: true,
      paths: ['src/a.ts', '', 'src/b.ts'],
      sha256_before: 'a'.repeat(64),
      sha256_after: 'b'.repeat(64),
    });

    expect(meta).toEqual({
      toolCallId: 'tc_1',
      risk: 'danger',
      decision: 'approved',
      errorCode: 'tool.confirmation_required',
      durationMs: 1200,
      resultChars: 42,
      truncated: true,
      paths: ['src/a.ts', 'src/b.ts'],
      sha256Before: 'a'.repeat(64),
      sha256After: 'b'.repeat(64),
    });
  });

  it('旧数据缺字段时不产出键,非法值被丢弃', () => {
    expect(toolCallMetaFrom({ name: 'read_file', ok: true })).toEqual({});

    const invalid = toolCallMetaFrom({
      risk: 'root',
      decision: 'denied',
      duration_ms: -1,
      result_chars: 'abc',
      paths: [],
      truncated: false,
      error_code: '   ',
    });
    expect(invalid).toEqual({});
  });
});

describe('formatDuration', () => {
  it('毫秒按量级格式化', () => {
    expect(formatDuration(120)).toBe('120ms');
    expect(formatDuration(1200)).toBe('1.2s');
    expect(formatDuration(65_000)).toBe('1m5s');
    expect(formatDuration(null)).toBe('');
    expect(formatDuration(Number.NaN)).toBe('');
  });
});
