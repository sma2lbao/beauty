import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ChatPage } from '@/pages/chat';
import { runsApi } from '@/lib/runs-api';

import {
  createRenderPage,
  event,
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
vi.mock('@/components/runs/use-run-events', async () =>
  (await import('./chat-test-harness')).useRunEventsModuleMock(),
);

const renderPage = createRenderPage(ChatPage);

const requiredPayload = {
  question_id: 'q1',
  questions: [
    {
      header: '范围',
      question: '要整理哪些内容?',
      options: [
        { label: '本周文档', description: '含会议记录' },
        { label: '全部文档', description: '含历史归档' },
      ],
      multi_select: false,
    },
  ],
  allow_custom: true,
};

beforeEach(async () => {
  await setupChatTest();
});

afterEach(() => {
  setRunEvents([]);
});

describe('ChatPage 挂起提问卡片', () => {
  it('等待回答时渲染问题并提交 answer 请求', async () => {
    setRunEvents([event(1, 'run.question.required.v1', requiredPayload)]);
    vi.mocked(runsApi.get).mockResolvedValue(
      makeRun({ status: 'awaiting_input' }),
    );
    renderPage('/?conversation=c1');

    expect(await screen.findByText('需要你回答')).toBeTruthy();
    expect(screen.getAllByText('等待你的回答').length).toBeGreaterThan(0);

    fireEvent.click(await screen.findByRole('radio', { name: '本周文档' }));
    fireEvent.click(screen.getByRole('button', { name: '提交回答' }));

    await waitFor(() =>
      expect(runsApi.answerRunQuestion).toHaveBeenCalledWith('run-1', {
        question_id: 'q1',
        action: 'answer',
        answers: [{ index: 0, labels: ['本周文档'], custom: null }],
      }),
    );
    expect(await screen.findByText('已提交答案')).toBeTruthy();
  });

  it('跳过提问提交 decline', async () => {
    setRunEvents([event(1, 'run.question.required.v1', requiredPayload)]);
    vi.mocked(runsApi.get).mockResolvedValue(
      makeRun({ status: 'awaiting_input' }),
    );
    renderPage('/?conversation=c1');

    fireEvent.click(await screen.findByRole('button', { name: '跳过并继续' }));

    await waitFor(() =>
      expect(runsApi.answerRunQuestion).toHaveBeenCalledWith('run-1', {
        question_id: 'q1',
        action: 'decline',
        answers: [],
      }),
    );
  });

  it('历史已回答时展示锁定摘要,不再渲染提交按钮', async () => {
    setRunEvents([
      event(1, 'run.question.required.v1', requiredPayload),
      event(2, 'run.question.answered.v1', {
        question_id: 'q1',
        action: 'answer',
        answers: [{ index: 0, labels: ['本周文档'], custom: null }],
      }),
    ]);
    vi.mocked(runsApi.get).mockResolvedValue(makeRun({ status: 'running' }));
    renderPage('/?conversation=c1');

    expect(await screen.findByText('已提交答案')).toBeTruthy();
    expect(screen.getByText(/本周文档/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: '提交回答' })).toBeNull();
  });

  it('提问超时后展示失效锁定态', async () => {
    setRunEvents([
      event(1, 'run.question.required.v1', requiredPayload),
      event(2, 'run.question.timeout.v1', { question_id: 'q1' }),
    ]);
    vi.mocked(runsApi.get).mockResolvedValue(makeRun({ status: 'running' }));
    renderPage('/?conversation=c1');

    expect(await screen.findByText('提问已失效')).toBeTruthy();
    expect(screen.getByText('提问已超时,任务会按跳过继续。')).toBeTruthy();
  });
});
