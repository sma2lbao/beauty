/**
 * W2 R-21:执行面板渲染契约。
 * - 三个 tab(执行步骤/变更/终端)存在且可切换;
 * - 执行步骤列出工具调用及其成功/失败状态;
 * - 变更 tab 统计工具调用次数;
 * - 终端 tab 显示事件流水与 SSE 连接状态。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { Run, RunEvent, RunStatus } from '@/lib/runs-api';
import type { ToolCall } from '@/lib/presentation';

import { ExecutionPanel } from './execution-panel';

const run: Run = {
  id: 'run-1',
  title: '修一个 bug',
  goal: '修一个 bug',
  status: 'running',
  rounds: 1,
  attempts: 1,
  acceptance_criteria: [],
  status_reason: null,
  parent_run_id: null,
  conversation_id: null,
  created_at: '2026-10-01T00:00:00Z',
};

const events: RunEvent[] = [
  { seq: 1, type: 'run.started', payload: {} },
  { seq: 2, type: 'token.delta', payload: { text: 'hi' } },
];

// ToolCall.arguments 是 JSON 文本字符串,detail 为展示明细。
const toolCalls: ToolCall[] = [
  { seq: 1, name: 'list_files', ok: true, arguments: '{}', detail: '列目录' },
  {
    seq: 2,
    name: 'read_file',
    ok: false,
    arguments: '{"path":"src/app.tsx"}',
    detail: '读文件',
  },
];

function renderPanel(status: RunStatus = 'running', connected = true) {
  // 变更 tab 里的 RunArtifacts 走 react-query,需要 Provider(拿不到数据时空态)。
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ExecutionPanel
        run={run}
        status={status}
        events={events}
        toolCalls={toolCalls}
        connected={connected}
        runId="run-1"
      />
    </QueryClientProvider>,
  );
}

describe('ExecutionPanel', () => {
  it('渲染三个 tab,执行步骤列出工具调用与状态', () => {
    renderPanel();
    expect(screen.getByRole('tab', { name: '执行步骤' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: '变更' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: '终端' })).toBeTruthy();

    expect(screen.getByText('list_files')).toBeTruthy();
    expect(screen.getByText('read_file')).toBeTruthy();
    // 1 成功 1 失败的状态文案
    expect(screen.getByText(/完成/)).toBeTruthy();
    expect(screen.getByText(/失败/)).toBeTruthy();
  });

  it('变更 tab 统计工具调用次数并列出清单', () => {
    renderPanel();
    fireEvent.click(screen.getByRole('tab', { name: '变更' }));
    expect(screen.getByText('2 次')).toBeTruthy();
    expect(screen.getByText('ok')).toBeTruthy();
    expect(screen.getByText('failed')).toBeTruthy();
  });

  it('终端 tab 显示事件流水与 SSE 状态', () => {
    renderPanel();
    fireEvent.click(screen.getByRole('tab', { name: '终端' }));
    expect(screen.getByText('SSE 已连接')).toBeTruthy();
    expect(screen.getByText(/run\.started/)).toBeTruthy();
    expect(screen.getByText(/token\.delta/)).toBeTruthy();
  });

  it('断开时终端显示未连接', () => {
    renderPanel('running', false);
    fireEvent.click(screen.getByRole('tab', { name: '终端' }));
    expect(screen.getByText('SSE 未连接')).toBeTruthy();
  });
});
