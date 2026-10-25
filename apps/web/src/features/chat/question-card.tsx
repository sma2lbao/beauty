import { useState } from 'react';

import { ConsoleButton, ToneBadge } from '@/components/console/ui';
import type { RunAnswerPayload } from '@/lib/runs-api';
import {
  QUESTION_LIMITS,
  questionErrorText,
  type QuestionState,
} from '@/lib/presentation';

/** 每题草稿:选中项 label 列表 + 自定义文本。 */
type Draft = {
  selections: Record<number, string[]>;
  customs: Record<number, string>;
};

/**
 * ask_question 的挂起提问卡片(P2-07,§10.7)。
 *
 * 数据流与「危险操作确认卡片」一致:由 run 事件流推导的 QuestionState 驱动,
 * 提交走 runsApi.answerRunQuestion。所有问题/选项按纯文本渲染(React 转义),
 * 不解析 markdown/HTML——载荷来自模型输出,按不可信处理。
 *
 * 提交成功后本地锁定(后端随后把 run 状态推回 running,事件流会再给一份回执);
 * 历史回放时直接以 answered/expired 锁定态展示答案摘要。
 */
export function QuestionCard({
  state,
  busy = false,
  onSubmit,
}: {
  state: QuestionState;
  busy?: boolean;
  onSubmit: (payload: RunAnswerPayload) => Promise<void>;
}) {
  const question = state.question;
  const [draft, setDraft] = useState<Draft>({
    selections: {},
    customs: {},
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submittedAction, setSubmittedAction] = useState<
    'answer' | 'decline' | null
  >(null);

  const questionId = question.question_id;
  const locked = state.phase !== 'active' || submittedAction !== null;
  const pending = busy || submitting;

  const hasAnswer = (index: number) =>
    (draft.selections[index]?.length ?? 0) > 0 ||
    (draft.customs[index] ?? '').trim().length > 0;
  const allAnswered =
    question.questions.length > 0 &&
    question.questions.every((_, index) => hasAnswer(index));

  const toggleOption = (index: number, label: string, multi: boolean) => {
    setDraft((current) => {
      const selected = current.selections[index] ?? [];
      const next = multi
        ? selected.includes(label)
          ? selected.filter((item) => item !== label)
          : [...selected, label]
        : [label];
      return {
        ...current,
        selections: { ...current.selections, [index]: next },
      };
    });
    setError(null);
  };

  const setCustom = (index: number, value: string) => {
    setDraft((current) => ({
      ...current,
      customs: { ...current.customs, [index]: value },
    }));
    setError(null);
  };

  const answersFromDraft = (): RunAnswerPayload['answers'] =>
    question.questions.map((_, index) => ({
      index,
      labels: (draft.selections[index] ?? []).slice(0, QUESTION_LIMITS.options),
      custom:
        (draft.customs[index] ?? '').trim().slice(0, QUESTION_LIMITS.custom) ||
        null,
    }));

  const submit = async (action: 'answer' | 'decline') => {
    if (pending) return;
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit({
        question_id: questionId,
        action,
        answers: action === 'answer' ? answersFromDraft() : [],
      });
      setSubmittedAction(action);
    } catch (cause) {
      setError(questionErrorText(cause));
    } finally {
      setSubmitting(false);
    }
  };

  const answersByIndex = new Map(
    state.answers.map((answer) => [answer.index, answer]),
  );

  const declined = state.action === 'decline' || submittedAction === 'decline';
  const answered = state.action === 'answer' || submittedAction === 'answer';
  const title =
    state.phase === 'expired'
      ? '提问已失效'
      : state.phase === 'active' && !locked
        ? '需要你回答'
        : declined
          ? '已跳过提问'
          : answered
            ? '已提交答案'
            : '提问已失效';
  const badge =
    state.phase === 'expired'
      ? { tone: 'neutral' as const, text: '已失效' }
      : state.phase === 'active' && !locked
        ? { tone: 'warning' as const, text: '等待回答' }
        : declined
          ? { tone: 'neutral' as const, text: '已跳过' }
          : { tone: 'success' as const, text: '已回答' };

  return (
    <section
      aria-label="等待你回答的问题"
      data-question-id={questionId}
      className="rounded-lg border border-ha-warning-border bg-ha-warning-bg/40"
    >
      <div className="flex items-center justify-between gap-3 border-b border-ha-warning-border/60 px-3 py-2.5">
        <span className="text-[12px] font-semibold text-ha-warning">
          {title}
        </span>
        <ToneBadge tone={badge.tone}>{badge.text}</ToneBadge>
      </div>

      {locked ? (
        <div className="space-y-1.5 px-3 py-2.5 text-[12px] leading-5 text-ha-text-2">
          {answered ? (
            question.questions.map((_, index) => {
              const answer = answersByIndex.get(index);
              const local = answersFromDraft().find(
                (item) => item.index === index,
              );
              const labels = answer?.labels ?? local?.labels ?? [];
              const custom = answer?.custom ?? local?.custom ?? null;
              const parts = [
                ...labels,
                ...(custom ? [`自定义:${custom}`] : []),
              ];
              return (
                <p key={index} className="break-words">
                  <span className="mr-1.5 font-mono text-[11px] text-ha-dim">
                    {index + 1}.
                  </span>
                  {parts.join('、') || '未回答'}
                </p>
              );
            })
          ) : (
            <p className="break-words">
              {state.phase === 'expired'
                ? state.timedOut
                  ? '提问已超时,任务会按跳过继续。'
                  : '提问已失效(任务已继续)。'
                : '已跳过提问,任务继续执行。'}
            </p>
          )}
          {submittedAction ? (
            <p className="text-[11px] text-ha-dim">已提交,等待任务继续…</p>
          ) : null}
        </div>
      ) : (
        <div className="space-y-3 px-3 py-2.5">
          {question.questions.map((item, index) => {
            const multi = item.multi_select === true;
            const selected = draft.selections[index] ?? [];
            return (
              <div key={index} className="space-y-1.5">
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                  <span className="font-mono text-[11px] text-ha-dim">
                    第 {index + 1} 题
                  </span>
                  {item.header ? (
                    <span className="font-mono text-[11px] text-ha-dim">
                      {item.header}
                    </span>
                  ) : null}
                </div>
                <p className="whitespace-pre-wrap break-words text-[13px] leading-5 text-ha-text-body">
                  {item.question}
                </p>
                {item.options.length > 0 ? (
                  <div className="space-y-1 pt-0.5">
                    {item.options.map((option, optionIndex) => {
                      const inputId = `${questionId}-${index}-${optionIndex}`;
                      return (
                        <div key={optionIndex}>
                          <label
                            htmlFor={inputId}
                            className="flex cursor-pointer items-center gap-2 text-[12px] text-ha-text-2"
                          >
                            <input
                              id={inputId}
                              type={multi ? 'checkbox' : 'radio'}
                              name={`${questionId}-${index}`}
                              checked={selected.includes(option.label)}
                              onChange={() =>
                                toggleOption(index, option.label, multi)
                              }
                              className="h-3.5 w-3.5 shrink-0 accent-ha-accent"
                            />
                            <span className="break-words">{option.label}</span>
                          </label>
                          {option.description ? (
                            <p className="ml-5 break-words pb-0.5 text-[11px] leading-4 text-ha-dim">
                              {option.description}
                            </p>
                          ) : null}
                        </div>
                      );
                    })}
                  </div>
                ) : null}
                {question.allow_custom ? (
                  <div className="space-y-1 pt-0.5">
                    <input
                      aria-label={`第 ${index + 1} 题自定义回答`}
                      value={draft.customs[index] ?? ''}
                      maxLength={QUESTION_LIMITS.custom}
                      onChange={(event) => setCustom(index, event.target.value)}
                      disabled={pending}
                      placeholder="也可以直接输入回答…"
                      className="h-7 w-full rounded-md border border-ha-border-strong bg-ha-panel px-2.5 text-[12px] text-ha-text-body outline-none placeholder:text-ha-dim focus:border-ha-accent"
                    />
                    <p className="text-[11px] text-ha-dim">
                      请勿输入密码或密钥
                    </p>
                  </div>
                ) : null}
              </div>
            );
          })}

          {error ? (
            <p role="alert" className="break-words text-[12px] text-ha-danger">
              {error}
            </p>
          ) : null}

          <div className="flex flex-wrap items-center gap-2 border-t border-ha-warning-border/60 pt-2.5">
            <ConsoleButton
              variant="primary"
              size="sm"
              onClick={() => void submit('answer')}
              disabled={pending || !allAnswered}
              title={allAnswered ? undefined : '请先回答每一道问题'}
            >
              {submitting ? '提交中…' : '提交回答'}
            </ConsoleButton>
            <ConsoleButton
              variant="secondary"
              size="sm"
              onClick={() => void submit('decline')}
              disabled={pending}
            >
              跳过并继续
            </ConsoleButton>
            {question.expires_at ? (
              <span className="ml-auto font-mono text-[11px] text-ha-dim">
                截止{' '}
                {new Date(question.expires_at).toLocaleTimeString('zh-CN', {
                  hour: '2-digit',
                  minute: '2-digit',
                  hour12: false,
                })}
              </span>
            ) : null}
          </div>
        </div>
      )}
    </section>
  );
}
