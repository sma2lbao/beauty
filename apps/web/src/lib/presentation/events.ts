/**
 * run 事件流 → 会话/执行面板的展示文案。
 *
 * 只做展示映射,不改状态语义;事件顺序由订阅层保证(不再排序)。
 */
import type { RunEvent } from '@/lib/runs-api';
import { asText, type Tone } from './format';

/**
 * run 事件流 → 任务执行期的实时正文(会话里唯一的正文增量来源)。
 *
 * 后端把模型正文写成 `token.delta.v1` 事件(payload = `{ text, phase }`,text 是
 * **增量**片段),这里只认这类事件,按事件顺序拼接;phase 一变就视为进入新阶段、
 * 丢掉上一段累积文本——每个阶段都是一次新的正文输出,旧阶段的字不该再挂在新气泡上。
 * 事件顺序由订阅层保证(`mergeEvents` 按 seq 升序合并),本函数不再排序。
 * 没有任何增量时返回 null,页面对应渲染「正在执行…」的兜底。
 */
export function liveRunText(
  events: readonly RunEvent[],
): { phase: string; text: string } | null {
  let phase: string | null = null;
  let text = '';
  for (const event of events) {
    if (event.type !== 'token.delta.v1') continue;
    const nextPhase = asText(event.payload.phase, 'execute');
    if (nextPhase !== phase) {
      phase = nextPhase;
      text = '';
    }
    text += asText(event.payload.text);
  }
  return phase === null ? null : { phase, text };
}

/** 事件流 → 会话里的一行系统播报(只做展示映射,不改状态语义)。 */
export function eventLine(
  event: RunEvent,
): { text: string; tone: Tone } | null {
  const payload = event.payload ?? {};
  switch (event.type) {
    case 'run.created.v1':
      return {
        text: `已建立任务《${asText(payload.title, '未命名')}》,开始规划…`,
        tone: 'neutral',
      };
    case 'run.planned.v1':
      return {
        text: `计划已就绪(第 ${payload.attempt ?? 1} 次尝试),等你确认后执行。`,
        tone: 'neutral',
      };
    case 'run.approved.v1':
      return { text: '已放行,开始执行。', tone: 'success' };
    case 'run.confirmation.required.v1':
      return {
        text: '有危险操作需要你确认(例如对外发布),确认后才会执行。',
        tone: 'warning',
      };
    case 'run.confirmation.received.v1':
      return {
        text: `危险操作确认:${asText(payload.action, '已回复')}`,
        tone: 'neutral',
      };
    case 'run.question.required.v1':
      return {
        text: 'Agent 需要你回答几个问题,才能继续。',
        tone: 'warning',
      };
    case 'run.question.answered.v1':
      return {
        text:
          payload.action === 'decline'
            ? '已跳过提问,任务继续执行。'
            : '已提交回答,任务继续执行。',
        tone: payload.action === 'decline' ? 'neutral' : 'success',
      };
    case 'run.question.timeout.v1':
      return {
        text: '提问超时未答,任务已按跳过继续。',
        tone: 'warning',
      };
    case 'run.instruction.queued.v1':
      return {
        text: '已记下你的补充,会在下一个边界并入执行。',
        tone: 'neutral',
      };
    case 'run.instruction.consumed.v1':
      return { text: '补充指令已并入本次执行。', tone: 'neutral' };
    case 'run.rejected.v1':
      return {
        text: `计划被拒绝:${asText(payload.reason, '未填原因')}`,
        tone: 'warning',
      };
    case 'run.resumed.v1':
      return { text: '已重新进入审批。', tone: 'neutral' };
    case 'run.retry.requested.v1':
      return { text: '已另起一跑重试(原任务历史保留)。', tone: 'neutral' };
    case 'run.failed.v1':
      return {
        text: `执行失败(${asText(payload.stage, '未知阶段')}):${asText(payload.reason, '未知原因')}`,
        tone: 'danger',
      };
    case 'run.blocked.v1':
      return {
        text: `任务中断:${asText(payload.reason, '需要人工处理')}`,
        tone: 'warning',
      };
    case 'run.delivered.v1':
      return {
        text: `已交付《${asText(payload.title, '成品')}》。`,
        tone: 'success',
      };
    case 'run.completed.v1':
      return { text: '任务完成 ✅', tone: 'success' };
    default:
      return null;
  }
}
