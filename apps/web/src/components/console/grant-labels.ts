/**
 * 授权范围文案(P1-A §7.2):默认用于对话写确认,run 危险确认另用一套动词。
 *
 * 放在独立文件里,避免 grant-picker 同时导出组件与常量触发 react-refresh 规则。
 */
import type { ApprovalGrant } from '@/lib/approval-api';

export const DEFAULT_GRANT_LABELS: Record<ApprovalGrant, string> = {
  once: '本次',
  session: '本会话',
  always: '始终允许该工具',
};

/** run 危险确认的文案(§7.2):与对话写确认的「工具」措辞区分开。 */
export const RUN_GRANT_LABELS: Partial<Record<ApprovalGrant, string>> = {
  once: '本次允许',
  session: '本会话允许这些工具',
  always: '始终允许这些工具',
};
