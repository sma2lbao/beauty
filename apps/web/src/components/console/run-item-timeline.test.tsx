/**
 * P1-D 统一 item 渲染器(P1-D 事件分类学)契约:
 * - 每个过程项默认折叠结果详情,展开显示结构化 data(aria-expanded 同步);
 * - plan 是带版本的一等项:显示「第 N 版」与 Markdown 正文,被 supersedes
 *   取代的旧版本折进「旧版本」组,不单独占一条;
 * - file_change 显示路径与 sha256 前 8 位;mcp_call 显示 server/tool;
 *   search 显示查询参数;todo 复用任务清单;
 * - 空 items 显示空态,不渲染列表。
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { RunItem } from '@/lib/runs-api';

import { RunItemTimeline } from './run-item-timeline';

function makeItem(
  overrides: Pick<RunItem, 'item_id' | 'kind'> & Partial<RunItem>,
): RunItem {
  const base: Omit<RunItem, 'item_id' | 'kind'> = {
    thread_id: 'run-1',
    turn_id: 'turn-1',
    phase: 'completed',
    status: 'completed',
    node: null,
    step_id: null,
    attempt: 1,
    title: '过程项',
    seq: 1,
    started_at: null,
    finished_at: null,
    duration_ms: null,
    data: {},
  };
  return { ...base, ...overrides };
}

function renderTimeline(items: RunItem[]) {
  return render(<RunItemTimeline items={items} />);
}

describe('RunItemTimeline', () => {
  it('没有过程项时显示空态而不是空列表', () => {
    renderTimeline([]);
    expect(screen.getByTestId('run-item-empty')).toBeTruthy();
    expect(screen.getByText(/还没有过程项记录/)).toBeTruthy();
    expect(screen.queryByTestId('run-item-timeline')).toBeNull();
  });

  it('过程项默认折叠详情,点击后展开工具参数与结果', () => {
    renderTimeline([
      makeItem({
        item_id: 'tool-1',
        kind: 'tool',
        title: '读取配置',
        duration_ms: 12,
        data: {
          name: 'read_file',
          arguments: '{"path":"a.ts"}',
          ok: true,
          detail: '文件内容',
        },
      }),
    ]);

    const toggle = screen.getByTestId('run-item-toggle-tool-1');
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(toggle.getAttribute('aria-label')).toBe('展开工具「读取配置」详情');
    expect(screen.queryByTestId('run-item-detail-tool-1')).toBeNull();
    expect(screen.getByText('12ms')).toBeTruthy();

    fireEvent.click(toggle);

    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(toggle.getAttribute('aria-label')).toBe('收起工具「读取配置」详情');
    const detail = screen.getByTestId('run-item-detail-tool-1');
    expect(within(detail).getByText('read_file')).toBeTruthy();
    expect(within(detail).getByText('参数')).toBeTruthy();
    expect(within(detail).getByText('{"path":"a.ts"}')).toBeTruthy();
    expect(within(detail).getByText('文件内容')).toBeTruthy();
  });

  it('plan 显示版本徽标与正文,旧版本折进可折叠的「旧版本」组', () => {
    renderTimeline([
      makeItem({
        item_id: 'run-1:plan:1',
        kind: 'plan',
        title: '执行计划',
        seq: 1,
        data: {
          version: 1,
          supersedes: null,
          content: '旧计划正文',
          chars: 5,
        },
      }),
      makeItem({
        item_id: 'run-1:plan:2',
        kind: 'plan',
        title: '执行计划',
        seq: 2,
        data: {
          version: 2,
          supersedes: 'run-1:plan:1',
          content: '当前计划正文',
          chars: 6,
        },
      }),
    ]);

    // 旧版本不单独占一条,当前版本正文直接可见。
    expect(screen.queryByTestId('run-item-run-1:plan:1')).toBeNull();
    expect(screen.getByTestId('run-item-run-1:plan:2')).toBeTruthy();
    expect(screen.getByText('第 2 版')).toBeTruthy();
    const content = screen.getByTestId('run-plan-content-run-1:plan:2');
    expect(within(content).getByText('当前计划正文')).toBeTruthy();

    fireEvent.click(screen.getByTestId('run-plan-history-toggle'));

    expect(screen.getByTestId('run-plan-history-run-1:plan:1')).toBeTruthy();
    expect(screen.getByText('旧版本(1)')).toBeTruthy();
    expect(screen.getByText('旧计划正文')).toBeTruthy();
    expect(screen.getByText('第 1 版')).toBeTruthy();
  });

  it('file_change 展开后显示路径与 sha256 前 8 位', () => {
    renderTimeline([
      makeItem({
        item_id: 'patch-1',
        kind: 'file_change',
        title: 'apply_patch',
        data: {
          name: 'apply_patch',
          paths: ['src/a.ts', 'src/b.ts'],
          sha256_before: '1234567890abcdef',
          sha256_after: 'abcdef1234567890',
          ok: true,
        },
      }),
    ]);

    expect(screen.queryByText('src/a.ts')).toBeNull();
    fireEvent.click(screen.getByTestId('run-item-toggle-patch-1'));
    expect(screen.getByText('src/a.ts')).toBeTruthy();
    expect(screen.getByText('src/b.ts')).toBeTruthy();
    expect(screen.getByText('sha256: 12345678 → abcdef12')).toBeTruthy();
  });

  it('mcp_call 显示 server 与 tool', () => {
    renderTimeline([
      makeItem({
        item_id: 'mcp-1',
        kind: 'mcp_call',
        title: 'github.search_issues',
        duration_ms: 1200,
        data: {
          server: 'github',
          tool: 'search_issues',
          name: 'github.search_issues',
          arguments: '{}',
          ok: true,
        },
      }),
    ]);

    fireEvent.click(screen.getByTestId('run-item-toggle-mcp-1'));
    const detail = screen.getByTestId('run-item-detail-mcp-1');
    expect(within(detail).getByText('github')).toBeTruthy();
    expect(within(detail).getByText('search_issues')).toBeTruthy();
    expect(screen.getByText('1.2s')).toBeTruthy();
  });

  it('search 显示查询参数', () => {
    renderTimeline([
      makeItem({
        item_id: 'search-1',
        kind: 'search',
        title: 'grep TODO',
        data: { name: 'grep', arguments: '{"pattern":"TODO"}', ok: true },
      }),
    ]);

    fireEvent.click(screen.getByTestId('run-item-toggle-search-1'));
    expect(screen.getByText('{"pattern":"TODO"}')).toBeTruthy();
  });

  it('todo 展开后复用任务清单并显示清单版本', () => {
    renderTimeline([
      makeItem({
        item_id: 'todo-1',
        kind: 'todo',
        title: '任务清单快照',
        data: {
          todos: [
            { content: '读取配置', status: 'completed' },
            { content: '实现工具', status: 'pending' },
          ],
          counts: { pending: 1, in_progress: 0, completed: 1 },
          revision: 2,
        },
      }),
    ]);

    fireEvent.click(screen.getByTestId('run-item-toggle-todo-1'));
    expect(screen.getByTestId('run-todo-list')).toBeTruthy();
    expect(screen.getByText('1/2')).toBeTruthy();
    expect(screen.getByText('清单版本 r2')).toBeTruthy();
  });

  it('状态徽标覆盖进行中/失败/已拒绝/已完成', () => {
    renderTimeline([
      makeItem({
        item_id: 'running-1',
        kind: 'tool',
        title: '运行中的工具',
        phase: 'started',
        status: null,
      }),
      makeItem({
        item_id: 'completed-1',
        kind: 'tool',
        title: '完成的工具',
        status: 'completed',
      }),
      makeItem({
        item_id: 'failed-1',
        kind: 'tool',
        title: '失败工具',
        status: 'failed',
        phase: 'failed',
      }),
      makeItem({
        item_id: 'denied-1',
        kind: 'tool',
        title: '被拒绝工具',
        status: 'denied',
      }),
    ]);

    // started 相位投影响应为进行中;其余按 status 直接映射。
    expect(
      screen.getByTestId('run-item-running-1').getAttribute('data-status'),
    ).toBe('running');
    expect(screen.getByText('进行中')).toBeTruthy();
    expect(screen.getByText('已完成')).toBeTruthy();
    expect(screen.getByText('失败')).toBeTruthy();
    expect(screen.getByText('已拒绝')).toBeTruthy();
  });
});
