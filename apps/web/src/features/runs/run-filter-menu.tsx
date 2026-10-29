/**
 * 看板筛选下拉:全部任务 / 进行中 / 待审核;选中项带强调点。
 */
import { Filter } from 'lucide-react';

import { ConsoleButton, StatusDot } from '@/components/console/ui';
import { cn } from '@/lib/utils';
import type { RunFilterKey } from '@/stores/run-board-store';

import type { RunsModel } from './use-runs-model';

const FILTERS: { key: RunFilterKey; label: string }[] = [
  { key: 'all', label: '全部任务' },
  { key: 'active', label: '进行中' },
  { key: 'review', label: '待审核' },
];

export function RunFilterMenu({ model }: { model: RunsModel }) {
  const { filter, filterOpen, setFilter, setFilterOpen } = model;
  return (
    <div className="relative">
      <ConsoleButton
        variant="secondary"
        size="md"
        aria-expanded={filterOpen}
        onClick={() => setFilterOpen((value) => !value)}
        className="h-8"
      >
        <Filter className="h-3.5 w-3.5" />
        筛选
      </ConsoleButton>
      {filterOpen ? (
        <div className="absolute right-0 top-9 z-20 w-40 rounded-lg border border-ha-border bg-ha-sidebar p-1 shadow-[0_16px_40px_rgba(0,0,0,0.5)]">
          {FILTERS.map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={() => {
                setFilter(item.key);
                setFilterOpen(false);
              }}
              className={cn(
                'flex h-7 w-full items-center justify-between rounded-md px-2 text-left text-[12px] transition-colors',
                filter === item.key
                  ? 'bg-[#1c1d1f] text-ha-text'
                  : 'text-ha-muted hover:bg-ha-panel-2 hover:text-ha-text-2',
              )}
            >
              {item.label}
              {filter === item.key ? <StatusDot tone="accent" /> : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
