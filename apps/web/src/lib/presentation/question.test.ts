import { describe, expect, it } from 'vitest';

import { ApiError } from '@/lib/http';
import type { RunEvent } from '@/lib/runs-api';
import {
  QUESTION_LIMITS,
  cleanText,
  parseRunQuestion,
  questionErrorText,
  questionStateFrom,
} from '@/lib/presentation';

function required(seq = 1, overrides: Record<string, unknown> = {}): RunEvent {
  return {
    seq,
    type: 'run.question.required.v1',
    payload: {
      question_id: 'q-1',
      questions: [
        {
          header: '范围',
          question: '要整理哪些内容?',
          options: [
            { label: '本周文档', description: '含会议记录' },
            { label: '全部文档' },
          ],
          multi_select: false,
        },
      ],
      allow_custom: true,
      expires_at: '2026-10-13T11:00:00+00:00',
      ...overrides,
    },
  };
}

describe('parseRunQuestion', () => {
  it('解析事件载荷并归一化选项', () => {
    expect(parseRunQuestion(required().payload)).toEqual({
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
      expires_at: '2026-10-13T11:00:00+00:00',
    });
  });

  it('旧数据/非法载荷返回 null(不渲染空块)', () => {
    expect(parseRunQuestion(undefined)).toBeNull();
    expect(parseRunQuestion(null)).toBeNull();
    expect(parseRunQuestion({})).toBeNull();
    expect(parseRunQuestion({ questions: [] })).toBeNull();
    expect(parseRunQuestion({ question_id: '', questions: [{}] })).toBeNull();
    expect(
      parseRunQuestion({ question_id: 'q-1', questions: [{ question: '' }] }),
    ).toBeNull();
  });

  it('丢弃非法选项,问题与选项各最多 4 条', () => {
    const parsed = parseRunQuestion({
      question_id: 'q-1',
      questions: [
        {
          question: '选一个',
          options: [
            { label: 'A' },
            { label: '' },
            'not-an-object',
            { label: 'B' },
            { label: 'C' },
            { label: 'D' },
            { label: 'E' },
          ],
        },
        { question: '第二题' },
        { question: '第三题' },
        { question: '第四题' },
        { question: '第五题' },
      ],
    });
    expect(parsed?.questions).toHaveLength(4);
    expect(parsed?.questions[0].options.map((option) => option.label)).toEqual([
      'A',
      'B',
      'C',
      'D',
    ]);
  });

  it('长度硬顶:问题/标题/选项/描述截断并去掉控制字符', () => {
    expect(cleanText('a\u0000b\u0007c', 10)).toBe('abc');
    expect(cleanText('  多行\n文本  ', 100)).toBe('多行\n文本');
    expect(cleanText(undefined, 10)).toBe('');

    const parsed = parseRunQuestion({
      question_id: 'q-1',
      questions: [
        {
          header: 'h'.repeat(QUESTION_LIMITS.header + 20),
          question: '长'.repeat(QUESTION_LIMITS.question + 200),
          options: [
            {
              label: 'l'.repeat(QUESTION_LIMITS.label + 20),
              description: 'd'.repeat(QUESTION_LIMITS.description + 100),
            },
          ],
        },
      ],
    });
    const item = parsed?.questions[0];
    expect(item?.header).toHaveLength(QUESTION_LIMITS.header);
    expect(item?.question).toHaveLength(QUESTION_LIMITS.question);
    expect(item?.question.endsWith('…')).toBe(true);
    expect(item?.options[0].label).toHaveLength(QUESTION_LIMITS.label);
    expect(item?.options[0].description).toHaveLength(
      QUESTION_LIMITS.description,
    );
  });
});

describe('questionStateFrom', () => {
  it('required 事件 + awaiting_input → 可作答', () => {
    const state = questionStateFrom([required()], 'awaiting_input');
    expect(state).toMatchObject({
      phase: 'active',
      action: null,
      timedOut: false,
      answers: [],
    });
    expect(state?.question.question_id).toBe('q-1');
  });

  it('answered 事件 → 锁定并带答案摘要', () => {
    const state = questionStateFrom(
      [
        required(),
        {
          seq: 2,
          type: 'run.question.answered.v1',
          payload: {
            question_id: 'q-1',
            action: 'answer',
            answers: [{ index: 0, labels: ['本周文档'], custom: '只看周一' }],
          },
        },
      ],
      'running',
    );
    expect(state).toMatchObject({ phase: 'answered', action: 'answer' });
    expect(state?.answers).toEqual([
      { index: 0, labels: ['本周文档'], custom: '只看周一' },
    ]);
  });

  it('decline 回执 → 已回答但答案为空', () => {
    const state = questionStateFrom(
      [
        required(),
        {
          seq: 2,
          type: 'run.question.answered.v1',
          payload: { question_id: 'q-1', action: 'decline', answers: [] },
        },
      ],
      'running',
    );
    expect(state).toMatchObject({
      phase: 'answered',
      action: 'decline',
      answers: [],
    });
  });

  it('question_id 不符的旧回执不会误锁新提问', () => {
    const state = questionStateFrom(
      [
        required(),
        {
          seq: 2,
          type: 'run.question.answered.v1',
          payload: { question_id: 'q-old', action: 'answer', answers: [] },
        },
      ],
      'awaiting_input',
    );
    expect(state?.phase).toBe('active');
  });

  it('timeout 事件 → 过期', () => {
    const state = questionStateFrom(
      [
        required(),
        {
          seq: 2,
          type: 'run.question.timeout.v1',
          payload: { question_id: 'q-1' },
        },
      ],
      'running',
    );
    expect(state).toMatchObject({
      phase: 'expired',
      action: 'decline',
      timedOut: true,
    });
  });

  it('任务已越过挂起点且无回执 → 锁定失效(旧会话回放)', () => {
    const state = questionStateFrom([required()], 'completed');
    expect(state).toMatchObject({ phase: 'expired', timedOut: false });
  });

  it('没有挂起提问时返回 null', () => {
    expect(questionStateFrom([], 'awaiting_input')).toBeNull();
    expect(
      questionStateFrom(
        [{ seq: 1, type: 'run.question.required.v1', payload: {} }],
        'awaiting_input',
      ),
    ).toBeNull();
  });

  it('新一轮 required 覆盖上一轮的回执', () => {
    const state = questionStateFrom(
      [
        required(1),
        {
          seq: 2,
          type: 'run.question.answered.v1',
          payload: { question_id: 'q-1', action: 'answer', answers: [] },
        },
        required(3, { question_id: 'q-2' }),
      ],
      'awaiting_input',
    );
    expect(state).toMatchObject({ phase: 'active', action: null });
    expect(state?.question.question_id).toBe('q-2');
  });
});

describe('questionErrorText', () => {
  it('映射三个业务错误码', () => {
    expect(
      questionErrorText(new ApiError(409, 'run.question_mismatch', 'x')),
    ).toContain('已更新');
    expect(
      questionErrorText(new ApiError(409, 'run.question_expired', 'x')),
    ).toContain('超时');
    expect(
      questionErrorText(new ApiError(409, 'run.invalid_state', 'x')),
    ).toContain('状态已变化');
    expect(
      questionErrorText(new ApiError(404, 'run.not_found', 'x')),
    ).toContain('不存在');
  });

  it('未知错误回退到错误文案或通用提示', () => {
    expect(questionErrorText(new Error('网络抖动'))).toBe('网络抖动');
    expect(questionErrorText(undefined)).toBe('提交回答失败,请稍后重试。');
    expect(questionErrorText({ code: 'weird' })).toBe(
      '提交回答失败,请稍后重试。',
    );
  });
});
