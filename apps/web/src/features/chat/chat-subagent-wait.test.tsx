import { screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ChatPage } from '@/pages/chat';

import type { Subagent } from '@/lib/runs-api';
import { runsApi } from '@/lib/runs-api';

import {
  createRenderPage,
  makeRun,
  setRunEvents,
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
vi.mock('@/features/runs/use-run-events', async () =>
  (await import('./chat-test-harness')).useRunEventsModuleMock(),
);

const renderPage = createRenderPage(ChatPage);

beforeEach(async () => {
  await setupChatTest();
});

afterEach(() => {
  setRunEvents([]);
});

function makeSubagent(overrides: Partial<Subagent> = {}): Subagent {
  return {
    run_id: 'run-child-1',
    title: '收集竞品定价',
    agent_name: '调研',
    status: 'running',
    status_reason: null,
    parent_run_id: 'run-1',
    created_at: '2026-10-13T10:02:00+00:00',
    usage: {
      calls: 2,
      prompt_tokens: 100,
      completion_tokens: 50,
      total_tokens: 150,
    },
    summary: null,
    ...overrides,
  };
}

describe('ChatPage 等待子代理(L2)', () => {
  it('父 run 等子代理时列出子 run、状态与人工介入提示', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(
      makeRun({ status: 'awaiting_subagent' }),
    );
    vi.mocked(runsApi.subagents).mockResolvedValue([
      makeSubagent(),
      makeSubagent({
        run_id: 'run-child-2',
        title: '核对引用',
        agent_name: '审校',
        status: 'awaiting_approval',
      }),
    ]);
    renderPage('/?conversation=c1');

    expect(await screen.findByTestId('chat-subagent-wait')).toBeTruthy();
    expect(await screen.findByText('调研 · 收集竞品定价')).toBeTruthy();
    expect(screen.getByText('审校 · 核对引用')).toBeTruthy();
    // 子 run 停在待审批:必须点明是人等,不是卡住
    expect(screen.getByText('子代理待你审批计划')).toBeTruthy();
    expect(screen.getByTestId('chat-subagent-run-child-1').dataset.status).toBe(
      'running',
    );
    expect(screen.getByTestId('chat-subagent-run-child-2').dataset.status).toBe(
      'awaiting_approval',
    );
  });

  it('打开子 run 的链接指向 /runs?run=<子 run>', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(
      makeRun({ status: 'awaiting_subagent' }),
    );
    vi.mocked(runsApi.subagents).mockResolvedValue([makeSubagent()]);
    renderPage('/?conversation=c1');

    const link = await screen.findByRole('link', {
      name: '打开子代理「收集竞品定价」',
    });
    expect(link.getAttribute('href')).toBe('/runs?run=run-child-1');
  });

  it('不在等子代理时不拉子 run 清单', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(makeRun({ status: 'running' }));
    renderPage('/?conversation=c1');

    expect(await screen.findByText(/已执行/)).toBeTruthy();
    expect(screen.queryByTestId('chat-subagent-wait')).toBeNull();
    expect(runsApi.subagents).not.toHaveBeenCalled();
  });
});
