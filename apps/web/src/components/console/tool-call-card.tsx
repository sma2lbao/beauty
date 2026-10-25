/**
 * 工具调用卡:工作台里 read_file / edit_file 这类条目的真实数据版。
 *
 * 事件 run.tool.called.v1 的核心字段是 name/arguments/ok/detail(见后端 ToolCallRecord),
 * detail 里若是 unified diff 就按增删行渲染,否则按结果文本展示。
 * P1 受控写起后端还会补 risk/decision/error_code/duration_ms/paths/sha256 等富字段;
 * 富字段全部可缺省——旧数据只显示核心信息,不渲染空块。
 */
import { ChevronDown, ChevronRight, Terminal } from 'lucide-react';
import { useState } from 'react';

import { DiffRows } from '@/components/console/diff-view';
import {
  formatDuration,
  parseUnifiedDiff,
  summarizeArguments,
  toneClasses,
  type ToolCall,
  type ToolDecision,
  type ToolRisk,
} from '@/lib/presentation';
import { cn } from '@/lib/utils';

/** 风险徽标:read 最克制、write 用警告色、danger 用危险色(靠色阶而不是描边堆叠)。 */
const RISK_BADGES: Record<
  ToolRisk,
  { label: string; tone: 'neutral' | 'warning' | 'danger' }
> = {
  read: { label: '只读', tone: 'neutral' },
  write: { label: '写入', tone: 'warning' },
  danger: { label: '危险', tone: 'danger' },
};

const DECISION_LABELS: Record<ToolDecision, string> = {
  auto: '自动',
  approved: '已确认',
};

/** paths 展示上限:多出的折叠成「+N」,避免长路径列表把卡片撑高。 */
const MAX_VISIBLE_PATHS = 3;

/** sha256 只展示前 8 位,够人眼比对又不挤占布局。 */
function shortSha(value: string): string {
  return value.slice(0, 8);
}

export function ToolCallCard({
  call,
  live = false,
  defaultOpen = false,
}: {
  call: ToolCall;
  live?: boolean;
  defaultOpen?: boolean;
}) {
  const [override, setOverride] = useState<boolean | null>(null);
  const open = override ?? (defaultOpen || live);
  const rows = call.detail ? parseUnifiedDiff(call.detail) : null;
  const status = live ? '运行中' : call.ok ? '完成' : '失败';
  const duration = formatDuration(call.durationMs);
  const risk = call.risk ? RISK_BADGES[call.risk] : null;
  const paths = call.paths ?? [];
  const visiblePaths = paths.slice(0, MAX_VISIBLE_PATHS);
  const hiddenPathCount = paths.length - visiblePaths.length;
  const sha256Before = call.sha256Before ? shortSha(call.sha256Before) : '';
  const sha256After = call.sha256After ? shortSha(call.sha256After) : '';
  const hasMeta = Boolean(
    call.decision ||
      call.errorCode ||
      call.resultChars !== undefined ||
      call.truncated ||
      paths.length > 0 ||
      sha256Before ||
      sha256After,
  );
  const statusNote = live ? '执行中' : duration;
  const outputPieces = [
    call.resultChars !== undefined ? call.resultChars + ' 字符' : '',
    call.truncated ? '已截断' : '',
  ].filter(Boolean);

  return (
    <div
      className={cn(
        'overflow-hidden rounded-lg border bg-ha-panel',
        live ? 'border-ha-info-border' : 'border-ha-border',
      )}
    >
      <button
        type="button"
        onClick={() => setOverride((prev) => !(prev ?? open))}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-ha-panel-2"
      >
        <span className="shrink-0 text-ha-faint">
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
        <Terminal size={13} className="shrink-0 text-ha-muted" />
        <span className="shrink-0 font-mono text-[12px] font-medium text-ha-text-body">
          {call.name}
        </span>
        {risk ? (
          <span
            className={cn(
              'inline-flex h-[18px] shrink-0 items-center rounded-[4px] px-1.5 font-mono text-[10px] leading-none',
              toneClasses(risk.tone).soft,
            )}
          >
            {risk.label}
          </span>
        ) : null}
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-ha-dim">
          {summarizeArguments(call.arguments)}
        </span>
        <span
          className={cn(
            'shrink-0 font-mono text-[11px] font-medium',
            live
              ? 'text-ha-info'
              : call.ok
                ? 'text-ha-success'
                : 'text-ha-danger',
          )}
        >
          {status}
          {statusNote ? ' · ' + statusNote : ''}
        </span>
      </button>

      {open && (call.detail || hasMeta) ? (
        <div className="border-t border-ha-border">
          <div className="flex items-center justify-between gap-3 px-3 py-2">
            <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-ha-faint">
              {call.detail ? '结果' : '详情'}
            </span>
            <span className="font-mono text-[11px] text-ha-faint">
              #{call.seq}
            </span>
          </div>

          {hasMeta ? (
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 px-3 pb-2 font-mono text-[11px] leading-[16px]">
              {call.decision ? (
                <>
                  <dt className="text-ha-faint">决策</dt>
                  <dd className="text-ha-muted">
                    {DECISION_LABELS[call.decision]}
                  </dd>
                </>
              ) : null}
              {call.errorCode ? (
                <>
                  <dt className="text-ha-faint">错误码</dt>
                  <dd className="break-all text-ha-danger">{call.errorCode}</dd>
                </>
              ) : null}
              {outputPieces.length > 0 ? (
                <>
                  <dt className="text-ha-faint">输出</dt>
                  <dd className="text-ha-muted">{outputPieces.join(' · ')}</dd>
                </>
              ) : null}
              {sha256Before || sha256After ? (
                <>
                  <dt className="text-ha-faint">sha256</dt>
                  <dd className="truncate text-ha-muted">
                    {(sha256Before || '-') + ' → ' + (sha256After || '-')}
                  </dd>
                </>
              ) : null}
              {paths.length > 0 ? (
                <>
                  <dt className="text-ha-faint">路径</dt>
                  <dd className="flex min-w-0 flex-wrap items-center gap-1">
                    {visiblePaths.map((path, index) => (
                      <span
                        key={path + ':' + index}
                        title={path}
                        className="max-w-[240px] truncate rounded-[3px] bg-ha-panel-2 px-1 py-0.5 text-ha-muted"
                      >
                        {path}
                      </span>
                    ))}
                    {hiddenPathCount > 0 ? (
                      <span className="text-ha-faint">
                        {'+' + hiddenPathCount}
                      </span>
                    ) : null}
                  </dd>
                </>
              ) : null}
            </dl>
          ) : null}

          {call.detail ? (
            rows ? (
              <DiffRows rows={rows} className="max-h-72 overflow-auto pb-2" />
            ) : (
              <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words px-3 pb-3 font-mono text-[12px] leading-[20px] text-ha-muted">
                {call.detail}
              </pre>
            )
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
