import { screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ChatPage } from '@/pages/chat';

import { runsApi } from '@/lib/runs-api';

import {
  createRenderPage,
  event,
  setRunEvents,
  setRunStreamConnected,
  setupChatTest,
} from './chat-test-harness';

vi.mock('@/lib/conversations-api', async () =>
  (await import('./chat-test-harness')).conversationsModuleMock(),
);
vi.mock('@/lib/skills-api', async () =>
  (await import('./chat-test-harness')).skillsModuleMock(),
);
vi.mock('@/lib/mcp-api', async () =>
  (await import('./chat-test-harness')).mcpModuleMock(),
);
vi.mock('@/lib/workspace-api', async () =>
  (await import('./chat-test-harness')).workspaceModuleMock(),
);
vi.mock('@/lib/runs-api', async () =>
  (await import('./chat-test-harness')).runsModuleMock(),
);
vi.mock('@/lib/models-api', async () =>
  (await import('./chat-test-harness')).modelsModuleMock(),
);
vi.mock('@/lib/connector-api', async () =>
  (await import('./chat-test-harness')).connectorModuleMock(),
);
vi.mock('@/lib/knowledge-api', async () =>
  (await import('./chat-test-harness')).knowledgeModuleMock(),
);

vi.mock('@/lib/memory-api', async () =>
  (await import('./chat-test-harness')).memoryModuleMock(),
);
vi.mock('@/features/runs/use-run-events', async () =>
  (await import('./chat-test-harness')).useRunEventsModuleMock(),
);

const renderPage = createRenderPage(ChatPage);

beforeEach(async () => {
  await setupChatTest();
});

afterEach(() => {
  setRunEvents([]);
  setRunStreamConnected(true);
});

describe('读模型来源:事件优先、断线回落(A/B)', () => {
  it('SSE 连上时步骤与清单来自事件流,不再请求投影端点', async () => {
    setRunEvents([
      event(1, 'run.planned.v1', { attempt: 1 }),
      event(4, 'run.step.v1', {
        step_id: 'run-1:1:execute:1',
        node: 'execute',
        phase: 'execute',
        attempt: 1,
        round: 1,
        status: 'completed',
        started_at: '2026-10-13T10:02:00+00:00',
        finished_at: '2026-10-13T10:02:01+00:00',
        duration_ms: 1000,
      }),
      event(5, 'run.todo.updated.v1', {
        todos: [{ content: '收集本周日程', status: 'completed' }],
        counts: { pending: 0, in_progress: 0, completed: 1 },
        revision: 1,
      }),
    ]);
    renderPage('/?conversation=c1');

    // 步骤面板直接吃事件流里的步骤:面板标题上的数量就是它。
    expect(await screen.findByText('1 个')).toBeTruthy();
    expect(runsApi.steps).not.toHaveBeenCalled();
    expect(runsApi.todos).not.toHaveBeenCalled();
  });

  it('SSE 未连上时回落到读模型端点(断线不能让界面冻住)', async () => {
    setRunStreamConnected(false);
    vi.mocked(runsApi.steps).mockResolvedValue([]);
    renderPage('/?conversation=c1');

    await vi.waitFor(() => expect(runsApi.steps).toHaveBeenCalledWith('run-1'));
    expect(runsApi.todos).toHaveBeenCalledWith('run-1');
  });
});
