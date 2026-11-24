/**
 * 用例抽屉:从用例表打开,保留 URL(?case=&tab=)。
 *
 * 结果优先:先看评分器与失败原因,再下钻工具轨迹和逐轮输入输出。
 * 大字段只在抽屉里按需读取;列表页不渲染 prompt。
 */
import { X } from 'lucide-react';
import { useEffect } from 'react';

import { StatusBadge } from '@beauty/design';

import { ConsolePanel, ConsoleTabs, EmptyPanel } from '@/components/console/ui';
import type { EvalCaseWithSuite } from '@/lib/eval-api';

import type { EvalCaseTab } from './use-evals-model';
import { formatDuration, formatNumber } from './format';
import { GraderList } from './grader-list';
import { ToolTrajectory } from './tool-trajectory';
import type { ToolCallDetail } from './tool-trajectory';

export function EvalCaseDrawer({
  testCase,
  tab,
  onTabChange,
  onClose,
}: {
  testCase: EvalCaseWithSuite;
  tab: EvalCaseTab;
  onTabChange: (tab: EvalCaseTab) => void;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = previous;
    };
  }, [onClose]);

  return (
    <aside
      aria-label="评测用例详情"
      className="flex h-full w-full flex-col border-l border-ha-border bg-ha-sidebar sm:w-[680px]"
    >
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-ha-border px-4">
        <div className="min-w-0 flex-1">
          <p className="truncate font-mono text-[12px] text-ha-text">
            {testCase.id}
          </p>
          <p className="truncate text-[10px] text-ha-faint">
            {testCase.suite} · {testCase.group}
          </p>
        </div>
        <StatusBadge tone={testCase.ok ? 'success' : 'danger'}>
          {testCase.ok ? '通过' : '失败'}
        </StatusBadge>
        <button
          type="button"
          aria-label="关闭用例详情"
          onClick={onClose}
          className="grid h-7 w-7 place-items-center rounded-md text-ha-muted transition-colors hover:bg-ha-panel-2 hover:text-ha-text"
        >
          <X className="h-4 w-4" />
        </button>
      </header>

      <div className="flex shrink-0 items-center border-b border-ha-border px-4 py-2">
        <ConsoleTabs
          items={[
            { value: 'result', label: '结果' },
            { value: 'trajectory', label: '轨迹' },
            { value: 'io', label: '输入输出' },
            { value: 'raw', label: '原始数据' },
          ]}
          value={tab}
          onChange={onTabChange}
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {tab === 'result' ? <ResultTab testCase={testCase} /> : null}
        {tab === 'trajectory' ? <TrajectoryTab testCase={testCase} /> : null}
        {tab === 'io' ? <IoTab testCase={testCase} /> : null}
        {tab === 'raw' ? <RawTab testCase={testCase} /> : null}
      </div>
    </aside>
  );
}

function ResultTab({ testCase }: { testCase: EvalCaseWithSuite }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-4 gap-2">
        <Metric label="得分" value={testCase.score.toFixed(2)} />
        <Metric label="tokens" value={formatNumber(testCase.tokens)} />
        <Metric label="耗时" value={formatDuration(testCase.duration_ms)} />
        <Metric label="评分器" value={formatNumber(testCase.graders.length)} />
      </div>

      {testCase.error ? (
        <p className="rounded-md border border-ha-danger-border bg-ha-danger-bg px-3 py-2 text-[11px] leading-5 text-ha-danger">
          {testCase.error}
        </p>
      ) : null}

      {testCase.failures.length > 0 ? (
        <section>
          <h3 className="mb-2 text-[12px] font-semibold text-ha-text-2">
            失败原因
          </h3>
          <ul className="space-y-1 rounded-md border border-ha-danger-border bg-ha-danger-bg px-3 py-2">
            {testCase.failures.map((failure, index) => (
              <li key={index} className="text-[11px] leading-5 text-ha-danger">
                {failure}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section>
        <h3 className="mb-2 text-[12px] font-semibold text-ha-text-2">
          评分器
        </h3>
        <GraderList graders={testCase.graders} />
      </section>

      {testCase.tags.length > 0 ? (
        <section>
          <h3 className="mb-2 text-[12px] font-semibold text-ha-text-2">
            标签
          </h3>
          <div className="flex flex-wrap gap-1.5">
            {testCase.tags.map((tag) => (
              <span
                key={tag}
                className="rounded bg-ha-panel-2 px-1.5 py-0.5 font-mono text-[10px] text-ha-muted"
              >
                #{tag}
              </span>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}

function TrajectoryTab({ testCase }: { testCase: EvalCaseWithSuite }) {
  return (
    <ToolTrajectory
      events={testCase.events}
      details={toolDetails(testCase.fields)}
    />
  );
}

function IoTab({ testCase }: { testCase: EvalCaseWithSuite }) {
  const prompts = textEntries(testCase.fields, 'prompt_');
  const replies = textEntries(testCase.fields, 'reply_');
  const output = testCase.output;

  return (
    <div className="space-y-4">
      <section>
        <h3 className="mb-2 text-[12px] font-semibold text-ha-text-2">
          最终输出
        </h3>
        <ConsolePanel className="p-3">
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words font-mono text-[10px] leading-5 text-ha-text-2">
            {output || '（空）'}
          </pre>
        </ConsolePanel>
      </section>

      {prompts.length > 0 ? (
        <section>
          <h3 className="mb-2 text-[12px] font-semibold text-ha-text-2">
            逐轮输入
          </h3>
          <div className="space-y-2">
            {prompts.map(([key, value]) => (
              <details key={key} className="rounded-md border border-ha-border">
                <summary className="cursor-pointer px-3 py-2 font-mono text-[11px] text-ha-muted">
                  {key}
                </summary>
                <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words border-t border-ha-border p-3 font-mono text-[10px] leading-5 text-ha-text-2">
                  {value}
                </pre>
              </details>
            ))}
          </div>
        </section>
      ) : null}

      {replies.length > 0 ? (
        <section>
          <h3 className="mb-2 text-[12px] font-semibold text-ha-text-2">
            逐轮回复
          </h3>
          <div className="space-y-2">
            {replies.map(([key, value]) => (
              <div key={key}>
                <p className="mb-1 font-mono text-[10px] text-ha-faint">
                  {key}
                </p>
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md border border-ha-border p-3 font-mono text-[10px] leading-5 text-ha-text-2">
                  {value}
                </pre>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {prompts.length === 0 && replies.length === 0 ? (
        <EmptyPanel
          className="border-0 py-6"
          title="这是任务型用例"
          description="输入来自 run 的计划与执行上下文,可在「原始数据」里看完整字段。"
        />
      ) : null}
    </div>
  );
}

function RawTab({ testCase }: { testCase: EvalCaseWithSuite }) {
  return (
    <pre className="overflow-x-auto rounded-md border border-ha-border bg-ha-bg p-3 font-mono text-[10px] leading-5 text-ha-text-2">
      {JSON.stringify(testCase, null, 2)}
    </pre>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <ConsolePanel className="p-3">
      <p className="text-[10px] text-ha-faint">{label}</p>
      <p className="mt-1 font-mono text-[14px] font-semibold text-ha-text">
        {value}
      </p>
    </ConsolePanel>
  );
}

function toolDetails(fields: Record<string, unknown>): ToolCallDetail[] {
  const raw = fields.tool_call_details;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter(
      (item): item is Record<string, unknown> =>
        typeof item === 'object' && item !== null,
    )
    .map((item) => ({
      name: String(item.name ?? 'unknown'),
      arguments: item.arguments,
      ok: Boolean(item.ok),
      risk: item.risk == null ? null : String(item.risk),
      decision: item.decision == null ? null : String(item.decision),
      error_code: item.error_code == null ? null : String(item.error_code),
      duration_ms:
        typeof item.duration_ms === 'number' ? item.duration_ms : null,
    }));
}

function textEntries(
  fields: Record<string, unknown>,
  prefix: string,
): [string, string][] {
  return Object.entries(fields)
    .filter(
      ([key, value]) => key.startsWith(prefix) && typeof value === 'string',
    )
    .sort(
      ([left], [right]) =>
        Number(left.slice(prefix.length)) - Number(right.slice(prefix.length)),
    )
    .map(([key, value]) => [key, String(value)]);
}
