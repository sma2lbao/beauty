import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ChatPage } from '@/pages/chat';

import { conversationsApi } from '@/lib/conversations-api';
import { ApiError } from '@/lib/http';
import { queryKeys } from '@/lib/query-keys';
import { runsApi } from '@/lib/runs-api';

import {
  createRenderPage,
  event,
  makeMessage,
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

describe('ChatPage 消息、审批与交付物', () => {
  it('消息历史:user 与 assistant 内容都渲染', async () => {
    vi.mocked(conversationsApi.messages).mockResolvedValue([
      makeMessage(),
      makeMessage({
        id: 'a1',
        role: 'assistant',
        content: '# 周报\n\n- 日程已汇总',
      }),
    ]);
    renderPage('/?conversation=c1');

    expect(await screen.findByText('把本周日程整理成周报')).toBeTruthy();
    expect(
      await screen.findByRole('heading', { name: '周报' }),
    ).toBeInTheDocument();
    expect(screen.getByText('日程已汇总')).toBeInTheDocument();
  });

  it('待审批:显示放行/拒绝,放行调用 approve', async () => {
    setRunEvents([
      event(1, 'token.delta.v1', { text: '# 执行计划', phase: 'plan' }),
    ]);
    vi.mocked(runsApi.getPlan).mockResolvedValue({
      run_id: 'run-1',
      status: 'awaiting_approval',
      plan_markdown: '# 执行计划',
    });
    renderPage('/?conversation=c1');

    const approveButtons = await screen.findAllByRole('button', {
      name: '放行执行',
    });
    // 等审批时 run 并没有在跑:实时正文气泡收起,正文交给审批卡片
    expect(screen.queryByRole('status', { name: '任务实时输出' })).toBeNull();
    fireEvent.click(approveButtons[0]);

    await waitFor(() => expect(runsApi.approve).toHaveBeenCalledWith('run-1'));
  });

  it('待确认危险操作:确认按钮调用 confirm(approve)', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(
      makeRun({ status: 'awaiting_confirmation' }),
    );
    renderPage('/?conversation=c1');

    fireEvent.click(await screen.findByRole('button', { name: '确认并执行' }));

    await waitFor(() =>
      expect(runsApi.confirm).toHaveBeenCalledWith(
        'run-1',
        'approve',
        undefined,
        'once',
      ),
    );
  });

  it('待确认危险操作:可选「本会话」并把 grant 透传给 confirm', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(
      makeRun({ status: 'awaiting_confirmation' }),
    );
    renderPage('/?conversation=c1');

    fireEvent.click(
      await screen.findByRole('button', { name: '本会话允许这些工具' }),
    );
    fireEvent.click(screen.getByRole('button', { name: '确认并执行' }));

    await waitFor(() =>
      expect(runsApi.confirm).toHaveBeenCalledWith(
        'run-1',
        'approve',
        undefined,
        'session',
      ),
    );
  });

  it('待审批:计划正文为空时给出占位说明,而不是空白卡片', async () => {
    vi.mocked(runsApi.getPlan).mockResolvedValue({
      run_id: 'run-1',
      status: 'awaiting_approval',
      plan_markdown: '',
    });
    renderPage('/?conversation=c1');

    expect(await screen.findByText('待放行的计划')).toBeTruthy();
    expect(screen.getByText(/计划正文暂未生成/)).toBeTruthy();
  });

  it('规划进入待审批时重新拉取计划:卡片带出正文而不是空壳', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(makeRun({ status: 'planning' }));
    const { queryClient } = renderPage('/?conversation=c1');
    // 规划期接口返回空计划(此时还没有计划卡)
    expect(await screen.findByText('实时输出')).toBeTruthy();

    vi.mocked(runsApi.getPlan).mockResolvedValue({
      run_id: 'run-1',
      status: 'awaiting_approval',
      plan_markdown: '# 重取回来的计划',
    });
    await act(async () => {
      queryClient.setQueryData(
        queryKeys.run('run-1'),
        makeRun({ status: 'awaiting_approval' }),
      );
    });

    expect(await screen.findByText('重取回来的计划')).toBeTruthy();
    expect(screen.queryByText(/计划正文暂未生成/)).toBeNull();
  });

  it('待确认危险操作:列出待确认的操作明细', async () => {
    setRunEvents([
      event(1, 'run.confirmation.required.v1', {
        pending_writes: [
          {
            id: 'call-1',
            name: 'publish_post',
            arguments: '{"target":"官方账号"}',
          },
          {
            id: 'call-2',
            name: 'delete_file',
            arguments: '{"path":"report.md"}',
          },
        ],
      }),
    ]);
    vi.mocked(runsApi.get).mockResolvedValue(
      makeRun({ status: 'awaiting_confirmation' }),
    );
    renderPage('/?conversation=c1');

    expect(await screen.findByText('有 2 个危险操作待确认')).toBeTruthy();
    expect(screen.getByText('publish_post')).toBeTruthy();
    expect(screen.getByText('target=官方账号')).toBeTruthy();
    expect(screen.getByText('delete_file')).toBeTruthy();
    expect(screen.getByText('path=report.md')).toBeTruthy();
  });

  it('把 run 事件翻成工具卡与状态播报', async () => {
    setRunEvents([
      event(1, 'run.created.v1', { title: '整理本周日程成周报' }),
      event(2, 'run.planned.v1', { attempt: 1 }),
      event(3, 'run.tool.called.v1', {
        name: 'search_knowledge',
        arguments: '{"query":"本周日程"}',
        ok: true,
      }),
      event(4, 'run.delivered.v1', { title: '周报' }),
      event(5, 'run.completed.v1', {}),
    ]);
    vi.mocked(runsApi.get).mockResolvedValue(makeRun({ status: 'completed' }));
    renderPage('/?conversation=c1');

    expect(
      (await screen.findAllByText('search_knowledge')).length,
    ).toBeGreaterThan(0);
    expect(screen.getByText(/已交付《周报》/)).toBeTruthy();
    expect(screen.getByText('任务完成 ✅')).toBeTruthy();
  });

  it('任务执行时按 token.delta 渲染实时正文,run 终态后让位给落库消息', async () => {
    setRunEvents([
      event(1, 'token.delta.v1', { text: '正在汇总', phase: 'execute' }),
      event(2, 'token.delta.v1', { text: '本周日程…', phase: 'execute' }),
    ]);
    vi.mocked(runsApi.get).mockResolvedValue(makeRun({ status: 'running' }));
    const { queryClient } = renderPage('/?conversation=c1');

    // 任务模式收不到 delta 帧,正文只能来自 run 事件;阶段标签跟着 phase 走
    const live = await screen.findByRole('status', {
      name: '任务实时输出',
    });
    expect(within(live).getByText('正在汇总本周日程…')).toBeTruthy();
    expect(within(live).getByText('执行中')).toBeTruthy();

    // run 进入终态:落库的 assistant 消息经 invalidate 出现,实时气泡必须消失,正文不重复
    vi.mocked(runsApi.get).mockResolvedValue(makeRun({ status: 'completed' }));
    vi.mocked(conversationsApi.messages).mockResolvedValue([
      makeMessage(),
      makeMessage({
        id: 'm2',
        role: 'assistant',
        content: '本周日程已汇总成周报',
      }),
    ]);
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['run', 'run-1'] });
    });

    expect(await screen.findByText('本周日程已汇总成周报')).toBeTruthy();
    expect(screen.queryByRole('status', { name: '任务实时输出' })).toBeNull();
    expect(screen.queryByText('正在汇总本周日程…')).toBeNull();
  });

  it('失败事件以错误语气展示,并提示去任务编排处理', async () => {
    setRunEvents([
      event(1, 'run.failed.v1', {
        stage: 'execute',
        reason: 'UpstreamError: 上游 500',
      }),
    ]);
    vi.mocked(runsApi.get).mockResolvedValue(
      makeRun({ status: 'failed', status_reason: 'UpstreamError: 上游 500' }),
    );
    renderPage('/?conversation=c1');

    expect(
      await screen.findByText(/执行失败\(execute\):UpstreamError: 上游 500/),
    ).toBeTruthy();
    expect(
      await screen.findByText('失败原因:UpstreamError: 上游 500'),
    ).toBeTruthy();
    expect(screen.getByRole('link', { name: '任务编排' })).toHaveAttribute(
      'href',
      '/runs',
    );
  });

  it('完成后按需列出全部交付物文件', async () => {
    vi.mocked(runsApi.get).mockResolvedValue(makeRun({ status: 'completed' }));
    vi.mocked(runsApi.artifacts).mockResolvedValue([
      {
        id: 'a1',
        run_id: 'run-1',
        kind: 'markdown',
        title: '周报',
        filename: '本周周报.md',
        content_type: 'text/markdown',
        size_bytes: 120,
        source: 'report',
        created_at: '2026-10-13T10:05:00+00:00',
      },
      {
        id: 'a2',
        run_id: 'run-1',
        kind: 'pdf',
        title: 'report.pdf',
        filename: 'report.pdf',
        content_type: 'application/pdf',
        size_bytes: 2048,
        source: 'mcp',
        created_at: '2026-10-13T10:05:01+00:00',
      },
    ]);
    renderPage('/?conversation=c1');

    fireEvent.click(
      (await screen.findAllByRole('button', { name: '查看成品' }))[0],
    );

    await waitFor(() =>
      expect(runsApi.artifacts).toHaveBeenCalledWith('run-1'),
    );
    expect(await screen.findByText('本周周报.md')).toBeInTheDocument();
    expect(await screen.findByText('report.pdf')).toBeInTheDocument();
  });
});

describe('PlanApprovalCard 审批前编辑计划(interaction-closure §3)', () => {
  function seedPlan(): void {
    setRunEvents([
      event(1, 'token.delta.v1', { text: '# 执行计划', phase: 'plan' }),
    ]);
    vi.mocked(runsApi.getPlan).mockResolvedValue({
      run_id: 'run-1',
      status: 'awaiting_approval',
      plan_markdown: '# 执行计划',
    });
  }

  it('编辑计划:textarea 预填正文,保存调 PATCH /plan 并收起编辑态', async () => {
    seedPlan();
    vi.mocked(runsApi.updatePlan).mockResolvedValue({
      run_id: 'run-1',
      status: 'awaiting_approval',
      plan_markdown: '# 改过的计划',
    });
    renderPage('/?conversation=c1');

    fireEvent.click(await screen.findByRole('button', { name: '编辑计划' }));
    const textarea = screen.getByLabelText('编辑计划内容');
    expect(textarea).toHaveValue('# 执行计划');

    fireEvent.change(textarea, { target: { value: '# 改过的计划' } });
    fireEvent.click(screen.getByRole('button', { name: '保存计划' }));

    await waitFor(() =>
      expect(runsApi.updatePlan).toHaveBeenCalledWith('run-1', '# 改过的计划'),
    );
    await waitFor(() =>
      expect(screen.queryByLabelText('编辑计划内容')).toBeNull(),
    );
    // 保存后仍在待审批:放行/拒绝与「编辑计划」入口都在
    expect(
      await screen.findAllByRole('button', { name: '放行执行' }),
    ).not.toHaveLength(0);
    expect(
      screen.getByRole('button', { name: '编辑计划' }),
    ).toBeInTheDocument();
  });

  it('409 保存失败就地中文提示,保持编辑态可重试', async () => {
    seedPlan();
    vi.mocked(runsApi.updatePlan).mockRejectedValueOnce(
      new ApiError(409, 'run.invalid_state', '状态不允许'),
    );
    renderPage('/?conversation=c1');

    fireEvent.click(await screen.findByRole('button', { name: '编辑计划' }));
    fireEvent.change(screen.getByLabelText('编辑计划内容'), {
      target: { value: '# 改过的计划' },
    });
    fireEvent.click(screen.getByRole('button', { name: '保存计划' }));

    expect(
      await screen.findByText('当前状态已不允许编辑计划,请刷新后再试。'),
    ).toBeInTheDocument();
    expect(screen.getByLabelText('编辑计划内容')).toBeInTheDocument();
  });

  it('空内容不提交请求,就地提示长度要求', async () => {
    seedPlan();
    renderPage('/?conversation=c1');

    fireEvent.click(await screen.findByRole('button', { name: '编辑计划' }));
    fireEvent.change(screen.getByLabelText('编辑计划内容'), {
      target: { value: '   ' },
    });
    fireEvent.click(screen.getByRole('button', { name: '保存计划' }));

    expect(
      await screen.findByText('计划内容需为 1–20000 字。'),
    ).toBeInTheDocument();
    expect(runsApi.updatePlan).not.toHaveBeenCalled();
  });
});
