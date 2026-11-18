/**
 * 看板筛选下拉:全部任务 / 进行中 / 待审核;选中项带强调点。
 */
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@beauty/design';
import { Filter } from 'lucide-react';

import { consoleButtonClass } from '@/components/console/button-class';
import { StatusDot } from '@/components/console/ui';
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
    <DropdownMenu open={filterOpen} onOpenChange={setFilterOpen}>
      <div className="relative">
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className={consoleButtonClass({
              variant: 'secondary',
              size: 'md',
              className: 'h-8',
            })}
          >
            <Filter className="h-3.5 w-3.5" />
            筛选
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          aria-label="任务筛选"
          align="end"
          size="sm"
          className="w-40"
        >
          {FILTERS.map((item) => (
            <DropdownMenuItem
              key={item.key}
              onSelect={() => setFilter(item.key)}
              className={cn(
                'h-7 justify-between',
                filter === item.key
                  ? 'bg-[#1c1d1f] text-ha-text'
                  : 'text-ha-muted hover:bg-ha-panel-2 hover:text-ha-text-2',
              )}
            >
              {item.label}
              {filter === item.key ? <StatusDot tone="accent" /> : null}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </div>
    </DropdownMenu>
  );
}
