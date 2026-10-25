import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { QuestionCard } from '@/features/chat/question-card';
import { ApiError } from '@/lib/http';
import type { RunQuestion } from '@/lib/runs-api';
import type { QuestionState } from '@/lib/presentation';

afterEach(cleanup);

function makeQuestion(overrides: Partial<RunQuestion> = {}): RunQuestion {
  return {
    question_id: 'q-1',
    questions: [
      {
        header: '范围',
        question: '要整理哪些内容?',
        options: [
          { label: '本周文档', description: '含会议记录' },
          { label: '全部文档', description: null },
        ],
        multi_select: false,
      },
    ],
    allow_custom: true,
    expires_at: null,
    ...overrides,
  };
}

function makeState(overrides: Partial<QuestionState> = {}): QuestionState {
  return {
    phase: 'active',
    question: makeQuestion(),
    answers: [],
    action: null,
    timedOut: false,
    ...overrides,
  };
}

describe('QuestionCard', () => {
  it('单选题选择后提交回答并锁定', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<QuestionCard state={makeState()} onSubmit={onSubmit} />);

    expect(screen.getByText('需要你回答')).toBeDefined();
    const submit = screen.getByRole('button', { name: '提交回答' });
    expect(submit).toBeDisabled();

    fireEvent.click(screen.getByLabelText('本周文档'));
    expect(submit).not.toBeDisabled();
    fireEvent.click(submit);

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({
        question_id: 'q-1',
        action: 'answer',
        answers: [{ index: 0, labels: ['本周文档'], custom: null }],
      }),
    );
    expect(await screen.findByText('已提交答案')).toBeDefined();
    expect(screen.getByText('已提交,等待任务继续…')).toBeDefined();
    expect(screen.queryByRole('button', { name: '提交回答' })).toBeNull();
  });

  it('多选题叠加自定义回答一起提交', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const question = makeQuestion({
      questions: [
        {
          header: null,
          question: '要整理哪些内容?',
          options: [
            { label: 'A', description: null },
            { label: 'B', description: null },
            { label: 'C', description: null },
          ],
          multi_select: true,
        },
      ],
    });
    render(
      <QuestionCard state={makeState({ question })} onSubmit={onSubmit} />,
    );

    fireEvent.click(screen.getByRole('checkbox', { name: 'A' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'C' }));
    fireEvent.change(screen.getByLabelText('第 1 题自定义回答'), {
      target: { value: '  补充一点说明  ' },
    });
    fireEvent.click(screen.getByRole('button', { name: '提交回答' }));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({
        question_id: 'q-1',
        action: 'answer',
        answers: [{ index: 0, labels: ['A', 'C'], custom: '补充一点说明' }],
      }),
    );
  });

  it('跳过提问提交 decline 并显示锁定文案', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<QuestionCard state={makeState()} onSubmit={onSubmit} />);

    fireEvent.click(screen.getByRole('button', { name: '跳过并继续' }));

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({
        question_id: 'q-1',
        action: 'decline',
        answers: [],
      }),
    );
    expect(await screen.findByText('已跳过提问')).toBeDefined();
    expect(screen.getByText('已跳过提问,任务继续执行。')).toBeDefined();
  });

  it('历史已回答状态直接锁定并展示答案摘要', () => {
    const state = makeState({
      phase: 'answered',
      action: 'answer',
      answers: [{ index: 0, labels: ['本周文档'], custom: '只看周一' }],
    });
    render(<QuestionCard state={state} onSubmit={vi.fn()} />);

    expect(screen.getByText('已提交答案')).toBeDefined();
    expect(screen.getByText(/本周文档/)).toBeDefined();
    expect(screen.getByText(/自定义:只看周一/)).toBeDefined();
    expect(screen.queryByRole('button', { name: '提交回答' })).toBeNull();
  });

  it('超时提问显示失效文案', () => {
    const state = makeState({
      phase: 'expired',
      action: 'decline',
      timedOut: true,
    });
    render(<QuestionCard state={state} onSubmit={vi.fn()} />);

    expect(screen.getByText('提问已失效')).toBeDefined();
    expect(screen.getByText('提问已超时,任务会按跳过继续。')).toBeDefined();
    expect(screen.queryByRole('button', { name: '提交回答' })).toBeNull();
  });

  it('提交失败展示可读错误且保持可编辑', async () => {
    const onSubmit = vi
      .fn()
      .mockRejectedValue(new ApiError(409, 'run.question_expired', 'expired'));
    render(<QuestionCard state={makeState()} onSubmit={onSubmit} />);

    fireEvent.click(screen.getByLabelText('本周文档'));
    fireEvent.click(screen.getByRole('button', { name: '提交回答' }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('提问已超时');
    expect(screen.getByRole('button', { name: '提交回答' })).toBeDefined();
  });

  it('问题文本按纯文本渲染,不解析 HTML', () => {
    const question = makeQuestion({
      questions: [
        {
          question: '<img src=x onerror="alert(1)">要做什么?',
          options: [{ label: 'A', description: null }],
        },
      ],
    });
    render(<QuestionCard state={makeState({ question })} onSubmit={vi.fn()} />);

    expect(
      screen.getByText('<img src=x onerror="alert(1)">要做什么?'),
    ).toBeDefined();
    expect(document.querySelector('img')).toBeNull();
  });
});
