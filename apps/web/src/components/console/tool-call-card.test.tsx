import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { ToolCall } from '@/lib/presentation';

import { ToolCallCard } from './tool-call-card';

const LEGACY_CALL: ToolCall = {
  seq: 7,
  name: 'read_file',
  arguments: '{"path":"src/app.tsx"}',
  ok: true,
  detail: '读完了 42 行',
};

const SHA_BEFORE = '1'.repeat(64);
const SHA_AFTER = '2'.repeat(64);

describe('ToolCallCard', () => {
  it('旧数据只有核心字段时正常展示,不出现伪字段', () => {
    render(<ToolCallCard call={LEGACY_CALL} defaultOpen />);

    expect(screen.getByText('read_file')).toBeTruthy();
    expect(screen.getByText('读完了 42 行')).toBeTruthy();
    expect(screen.getByText(/完成/)).toBeTruthy();
    expect(screen.queryByText('只读')).toBeNull();
    expect(screen.queryByText('写入')).toBeNull();
    expect(screen.queryByText('危险')).toBeNull();
    expect(document.body.textContent ?? '').not.toContain('undefined');
  });

  it('risk=write 渲染「写入」徽标', () => {
    render(<ToolCallCard call={{ ...LEGACY_CALL, risk: 'write' }} />);

    expect(screen.getByText('写入')).toBeTruthy();
    expect(screen.queryByText('危险')).toBeNull();
  });

  it('失败时展示错误码与耗时', () => {
    render(
      <ToolCallCard
        call={{
          ...LEGACY_CALL,
          ok: false,
          errorCode: 'tool.confirmation_required',
          durationMs: 120,
        }}
        defaultOpen
      />,
    );

    expect(screen.getByText(/失败/)).toBeTruthy();
    expect(screen.getByText('tool.confirmation_required')).toBeTruthy();
    expect(screen.getByText(/120ms/)).toBeTruthy();
  });

  it('富字段:决策/输出/sha256/路径折叠', () => {
    render(
      <ToolCallCard
        call={{
          ...LEGACY_CALL,
          decision: 'approved',
          resultChars: 42,
          truncated: true,
          paths: ['src/a.ts', 'src/b.ts', 'src/c.ts', 'src/d.ts'],
          sha256Before: SHA_BEFORE,
          sha256After: SHA_AFTER,
        }}
        defaultOpen
      />,
    );

    expect(screen.getByText('已确认')).toBeTruthy();
    expect(screen.getByText(/42 字符/)).toBeTruthy();
    expect(screen.getByText(/已截断/)).toBeTruthy();
    expect(screen.getByText('src/a.ts')).toBeTruthy();
    expect(screen.getByText('src/c.ts')).toBeTruthy();
    expect(screen.getByText('+1')).toBeTruthy();
    expect(screen.getByText('11111111 → 22222222')).toBeTruthy();
    expect(screen.queryByText('src/d.ts')).toBeNull();
  });
});
