/**
 * P1-C 恢复元信息:超时/优先级的展示文案与超时失败判定。
 *
 * 纯函数放这里而不是散在卡片/详情里,一是看板卡片与摘要要显示同一套文案,
 * 二是超时判定依赖后端固定的 status_reason 文案,集中一处便于对齐后端。
 */
import type { Run } from '@/lib/runs-api';

/** 后端超时收尾的固定文案前缀(design/recovery §3.4:「执行超时（n 秒），可重试」)。 */
const TIMEOUT_REASON_TEXT = '执行超时';

/** 是否「因执行超时而失败」:只认 failed + 固定文案,不看 timeout_seconds(平台默认超时也能命中)。 */
export function isTimeoutFailure(
  run: Pick<Run, 'status' | 'status_reason'>,
): boolean {
  return (
    run.status === 'failed' &&
    (run.status_reason ?? '').includes(TIMEOUT_REASON_TEXT)
  );
}

/** 超时配置文案:null/undefined = 平台默认,0 = 不限,其余为秒数。 */
export function timeoutLabel(timeoutSeconds: number | null | undefined): string {
  if (timeoutSeconds === null || timeoutSeconds === undefined) {
    return '超时 平台默认';
  }
  if (timeoutSeconds === 0) return '不限超时';
  return '超时 ' + timeoutSeconds + ' 秒';
}

/** 优先级文案:缺省/0 = 普通,正负号显式标出方向。 */
export function priorityLabel(priority: number | null | undefined): string {
  if (priority === null || priority === undefined || priority === 0) {
    return '优先级 普通';
  }
  return '优先级 ' + (priority > 0 ? '+' + priority : String(priority));
}
