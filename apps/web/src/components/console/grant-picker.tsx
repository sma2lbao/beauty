/**
 * 授权范围分段控件(P1-A §7.2):本次 / 本会话 / 始终。
 *
 * 小尺寸、互斥选项,移动端与窄屏都不折叠成菜单;调用方可以覆写文案
 * (对话写确认用「这次/本会话/始终允许该工具」,run 危险确认用
 * 「本次允许/本会话允许这些工具/始终允许这些工具」)。
 */
import type { ApprovalGrant } from '@/lib/approval-api';
import { cn } from '@/lib/utils';

import { DEFAULT_GRANT_LABELS } from './grant-labels';

const ORDER: ApprovalGrant[] = ['once', 'session', 'always'];

export function GrantPicker({
  value,
  onChange,
  labels,
  disabled,
  ariaLabel = '授权范围',
}: {
  value: ApprovalGrant;
  onChange: (grant: ApprovalGrant) => void;
  /** 只覆写需要不同的文案,未提供的沿用默认。 */
  labels?: Partial<Record<ApprovalGrant, string>>;
  disabled?: boolean;
  ariaLabel?: string;
}) {
  const text = { ...DEFAULT_GRANT_LABELS, ...labels };
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className="inline-flex h-7 items-center rounded-md border border-ha-border bg-ha-panel-2 p-0.5"
    >
      {ORDER.map((grant) => (
        <button
          key={grant}
          type="button"
          aria-pressed={value === grant}
          disabled={disabled}
          onClick={() => onChange(grant)}
          className={cn(
            'h-6 whitespace-nowrap rounded-[4px] px-2 text-[11px] transition-colors disabled:opacity-50',
            value === grant
              ? 'bg-ha-accent text-white'
              : 'text-ha-muted hover:text-ha-text',
          )}
        >
          {text[grant]}
        </button>
      ))}
    </div>
  );
}
