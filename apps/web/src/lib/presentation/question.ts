/**
 * ask_question 挂起提问的展示层:防御性解析、状态推导与错误文案。
 *
 * 后端 v1 不建问题表,载荷只在 run 事件里(§10.4):
 * - `run.question.required.v1` {question_id, questions, allow_custom, expires_at}
 * - `run.question.answered.v1` {question_id, answers, action}
 * - `run.question.timeout.v1` {question_id, expires_at}
 *
 * 事件来自不可信模型输出,这里一律按纯文本处理:去控制字符、硬顶长度、
 * 非法项丢弃;渲染层只用 React 文本节点(禁 markdown/HTML)。
 */
import { ApiError } from '@/lib/http';
import type {
  RunAnswer,
  RunEvent,
  RunQuestion,
  RunQuestionItem,
  RunQuestionOption,
  RunStatus,
} from '@/lib/runs-api';

/** 安全硬顶(§10.8):前端截断与后端校验保持一致。 */
export const QUESTION_LIMITS = {
  question: 1000,
  header: 12,
  label: 80,
  description: 300,
  custom: 1000,
  answerTotal: 2000,
  questions: 4,
  options: 4,
} as const;

/**
 * 纯文本清洗:去 C0 控制字符(保留换行/制表),压缩连续空白,trim,超长截断。
 *
 * 截断保留总长不超过 max(末尾用省略号占位),避免界面被超长文本撑开。
 */
/** 去 C0 控制字符(保留换行与制表)与 DEL;逐字符处理避免 no-control-regex 告警。 */
function stripControlChars(value: string): string {
  let result = '';
  for (const char of value) {
    const code = char.codePointAt(0) ?? 0;
    const keep =
      code === 0x0a || code === 0x09 || (code >= 0x20 && code !== 0x7f);
    if (keep) result += char;
  }
  return result;
}

export function cleanText(value: unknown, max: number): string {
  if (typeof value !== 'string') return '';
  const stripped = stripControlChars(value)
    .replace(/\r\n?/g, '\n')
    .replace(/[ \t]+\n/g, '\n')
    .trim();
  if (stripped.length <= max) return stripped;
  if (max <= 1) return stripped.slice(0, max);
  return `${stripped.slice(0, max - 1)}…`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function parseOption(raw: unknown): RunQuestionOption | null {
  if (!isRecord(raw)) return null;
  const label = cleanText(raw.label, QUESTION_LIMITS.label);
  if (!label) return null;
  const description = cleanText(raw.description, QUESTION_LIMITS.description);
  return { label, description: description || null };
}

function parseItem(raw: unknown): RunQuestionItem | null {
  if (!isRecord(raw)) return null;
  const question = cleanText(raw.question, QUESTION_LIMITS.question);
  if (!question) return null;
  const options = (Array.isArray(raw.options) ? raw.options : [])
    .map(parseOption)
    .filter((option): option is RunQuestionOption => option !== null)
    .slice(0, QUESTION_LIMITS.options);
  return {
    header: cleanText(raw.header, QUESTION_LIMITS.header) || null,
    question,
    options,
    multi_select: raw.multi_select === true,
  };
}

/**
 * 事件载荷 → 挂起提问;缺 `question_id`/问题列表时返回 null(旧数据不渲染空块)。
 */
export function parseRunQuestion(payload: unknown): RunQuestion | null {
  if (!isRecord(payload)) return null;
  const questionId = cleanText(payload.question_id, 128);
  if (!questionId) return null;
  const questions = (Array.isArray(payload.questions) ? payload.questions : [])
    .map(parseItem)
    .filter((item): item is RunQuestionItem => item !== null)
    .slice(0, QUESTION_LIMITS.questions);
  if (questions.length === 0) return null;
  return {
    question_id: questionId,
    questions,
    allow_custom: payload.allow_custom === true,
    expires_at: cleanText(payload.expires_at, 64) || null,
  };
}

function parseAnswers(raw: unknown): RunAnswer[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((entry): RunAnswer | null => {
      if (!isRecord(entry)) return null;
      const index = entry.index;
      if (typeof index !== 'number' || !Number.isInteger(index) || index < 0) {
        return null;
      }
      const labels = (Array.isArray(entry.labels) ? entry.labels : [])
        .map((label) => cleanText(label, QUESTION_LIMITS.label))
        .filter(Boolean);
      const custom = cleanText(entry.custom, QUESTION_LIMITS.custom);
      return { index, labels, custom: custom || null };
    })
    .filter((answer): answer is RunAnswer => answer !== null);
}

export type QuestionPhase = 'active' | 'answered' | 'expired';

export type QuestionState = {
  phase: QuestionPhase;
  question: RunQuestion;
  /** 已回答时的答案摘要(active/过期无回执时为空)。 */
  answers: RunAnswer[];
  /** 回执动作:answer(提交)/ decline(跳过或超时)。 */
  action: 'answer' | 'decline' | null;
  /** 是否因超时/失效结束。 */
  timedOut: boolean;
};

/**
 * 事件流 + 运行状态 → 提问卡片状态;没有挂起提问时返回 null。
 *
 * 只认「最近一次 required」:多轮 ask 时历史卡片不重复渲染,只展示当前一问;
 * 回执必须 question_id 对得上(旧回执不会误锁新提问)。
 */
export function questionStateFrom(
  events: readonly RunEvent[],
  status?: RunStatus | null,
): QuestionState | null {
  let question: RunQuestion | null = null;
  let answered: RunAnswer[] | null = null;
  let action: 'answer' | 'decline' | null = null;
  let timedOut = false;

  for (const event of events) {
    const payload = event.payload ?? {};
    if (event.type === 'run.question.required.v1') {
      const next = parseRunQuestion(payload);
      if (!next) continue;
      question = next;
      answered = null;
      action = null;
      timedOut = false;
      continue;
    }
    if (!question) continue;
    const questionId = cleanText(payload.question_id, 128);
    if (questionId !== question.question_id) continue;
    if (event.type === 'run.question.answered.v1') {
      action = payload.action === 'decline' ? 'decline' : 'answer';
      answered = action === 'answer' ? parseAnswers(payload.answers) : [];
    } else if (event.type === 'run.question.timeout.v1') {
      timedOut = true;
      action = 'decline';
      answered = [];
    }
  }

  if (!question) return null;
  if (action === 'answer') {
    return {
      phase: 'answered',
      question,
      answers: answered ?? [],
      action,
      timedOut: false,
    };
  }
  if (timedOut) {
    return {
      phase: 'expired',
      question,
      answers: [],
      action: 'decline',
      timedOut: true,
    };
  }
  if (action === 'decline') {
    return { phase: 'answered', question, answers: [], action, timedOut };
  }
  if (status === 'awaiting_input') {
    return {
      phase: 'active',
      question,
      answers: [],
      action: null,
      timedOut: false,
    };
  }
  if (status == null)
    return {
      phase: 'active',
      question,
      answers: [],
      action: null,
      timedOut: false,
    };
  // 任务已越过挂起点(旧会话回放缺回执或已被取消):锁定并提示失效。
  return {
    phase: 'expired',
    question,
    answers: [],
    action: null,
    timedOut: false,
  };
}

/** submit 失败 → 用户可读提示;映射后端 §10.5 的三个业务错误码。 */
export function questionErrorText(error: unknown): string {
  const code =
    error instanceof ApiError
      ? error.code
      : isRecord(error) && typeof error.code === 'string'
        ? error.code
        : '';
  switch (code) {
    case 'run.question_mismatch':
      return '这个提问已更新,请刷新页面后再回答。';
    case 'run.question_expired':
      return '提问已超时,任务会按跳过继续。';
    case 'run.invalid_state':
      return '任务状态已变化,请刷新页面。';
    case 'run.not_found':
      return '任务不存在或已删除。';
    default:
      break;
  }
  const message =
    error instanceof Error
      ? error.message
      : isRecord(error) && typeof error.message === 'string'
        ? error.message
        : '';
  return message || '提交回答失败,请稍后重试。';
}
