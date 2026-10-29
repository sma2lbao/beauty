/**
 * 计划过程项的正文区:Markdown 正文 +「旧版本」可折叠组。
 *
 * 计划修订 = 新版本,被 supersedes 取代的旧版本不单独占一条(列表层已过滤),
 * 折叠组由本组件渲染;展开状态由卡片持有,这里只接收开关回调。
 */
import { ChevronDown, ChevronRight } from 'lucide-react';

import { Markdown } from '@/components/console/markdown';
import { ToneBadge } from '@/components/console/ui';
import type { RunItem } from '@/lib/runs-api';

import { booleanOf, planVersion, textOf } from './run-item-meta';

/* ── 计划正文 + 旧版本组 ──────────────────────────────────────────────── */

export function PlanBody({
  item,
  history,
  historyOpen,
  onToggleHistory,
}: {
  item: RunItem;
  history: readonly RunItem[];
  historyOpen: boolean;
  onToggleHistory: () => void;
}) {
  const content = textOf(item.data.content);
  const truncated = booleanOf(item.data.truncated) === true;

  return (
    <div className="border-t border-ha-border px-3 py-2">
      {content ? (
        <div
          data-testid={`run-plan-content-${item.item_id}`}
          className="max-h-72 overflow-auto rounded-md border border-ha-border bg-ha-panel-2 px-2.5 py-2"
        >
          <Markdown content={content} />
        </div>
      ) : (
        <p className="rounded-md border border-dashed border-ha-border px-3 py-2 text-[11px] leading-5 text-ha-dim">
          计划正文缺失,可在「执行步骤」查看节点记录。
        </p>
      )}
      {truncated ? (
        <p className="mt-1.5 font-mono text-[10px] text-ha-warning">
          正文已截断,完整内容见计划快照
        </p>
      ) : null}

      {history.length > 0 ? (
        <div className="mt-2">
          <button
            type="button"
            onClick={onToggleHistory}
            aria-expanded={historyOpen}
            aria-label={`${historyOpen ? '收起' : '展开'}旧版本计划`}
            data-testid="run-plan-history-toggle"
            className="inline-flex h-6 items-center gap-1 rounded-md px-1.5 font-mono text-[11px] text-ha-muted transition-colors hover:bg-ha-panel-2 hover:text-ha-text-2"
          >
            {historyOpen ? (
              <ChevronDown size={12} />
            ) : (
              <ChevronRight size={12} />
            )}
            旧版本({history.length})
          </button>
          {historyOpen ? (
            <ol aria-label="计划旧版本" className="mt-1.5 space-y-1.5">
              {history.map((plan) => {
                const oldVersion = planVersion(plan);
                const oldContent = textOf(plan.data.content);
                return (
                  <li
                    key={plan.item_id}
                    data-testid={`run-plan-history-${plan.item_id}`}
                    className="rounded-md border border-ha-border bg-ha-panel-2"
                  >
                    <div className="flex items-center gap-2 px-2.5 py-1.5">
                      <span className="min-w-0 flex-1 truncate text-[11px] text-ha-dim">
                        {plan.title}
                      </span>
                      {oldVersion !== null ? (
                        <ToneBadge tone="neutral">第 {oldVersion} 版</ToneBadge>
                      ) : null}
                    </div>
                    {oldContent ? (
                      <div className="max-h-48 overflow-auto border-t border-ha-border px-2.5 py-2 opacity-80">
                        <Markdown content={oldContent} />
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ol>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
