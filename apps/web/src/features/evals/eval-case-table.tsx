/**
 * 用例表:按套件与状态过滤;整行可点开右侧抽屉。
 */
import {
  StatusBadge,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@beauty/design';

import { ConsolePanel, EmptyPanel } from '@/components/console/ui';
import type { EvalCaseWithSuite } from '@/lib/eval-api';

import { formatDuration, formatNumber } from './format';

export function EvalCaseTable({
  cases,
  selectedCaseId,
  onSelect,
  page = 1,
  pageSize = 50,
  total,
  onPageChange,
}: {
  cases: EvalCaseWithSuite[];
  selectedCaseId?: string;
  onSelect: (testCase: EvalCaseWithSuite) => void;
  page?: number;
  pageSize?: number;
  total?: number;
  onPageChange?: (page: number) => void;
}) {
  const count = total ?? cases.length;
  const pages = Math.max(1, Math.ceil(count / pageSize));

  return (
    <ConsolePanel className="overflow-hidden">
      <div className="flex items-center justify-between gap-3 border-b border-ha-border px-4 py-3">
        <div>
          <h2 className="text-[13px] font-semibold text-ha-text">用例</h2>
          <p className="mt-0.5 text-[11px] text-ha-faint">
            点一行查看评分器、工具轨迹与逐轮输入输出
          </p>
        </div>
        <span className="font-mono text-[11px] text-ha-faint">
          {count} 条
        </span>
      </div>

      {cases.length === 0 ? (
        <EmptyPanel
          className="m-4 border-0"
          title="没有匹配的用例"
          description="换一个套件或状态筛选。"
        />
      ) : (
        <div className="overflow-x-auto">
          <Table density="compact">
            <TableHeader>
              <TableRow>
                <TableHead className="min-w-[220px]">用例</TableHead>
                <TableHead>分组</TableHead>
                <TableHead>结果</TableHead>
                <TableHead className="text-right">得分</TableHead>
                <TableHead className="text-right">tokens</TableHead>
                <TableHead className="text-right">耗时</TableHead>
                <TableHead className="min-w-[180px]">失败原因</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {cases.map((testCase) => {
                const selected = testCase.id === selectedCaseId;
                return (
                  <TableRow
                    key={testCase.id}
                    tabIndex={0}
                    aria-selected={selected}
                    onClick={() => onSelect(testCase)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        onSelect(testCase);
                      }
                    }}
                    className={
                      'cursor-pointer outline-none transition-colors hover:bg-ha-panel-2/60 focus-visible:bg-ha-panel-2/60 ' +
                      (selected ? 'bg-ha-panel-2/80' : '')
                    }
                  >
                    <TableCell>
                      <span className="font-mono text-[11px] text-ha-text">
                        {testCase.id}
                      </span>
                      <p className="mt-0.5 flex flex-wrap gap-1 text-[10px] text-ha-faint">
                        {testCase.tags.slice(0, 3).map((tag) => (
                          <span key={tag}>#{tag}</span>
                        ))}
                      </p>
                    </TableCell>
                    <TableCell className="font-mono text-[10px] text-ha-muted">
                      {testCase.group}
                    </TableCell>
                    <TableCell>
                      <StatusBadge tone={testCase.ok ? 'success' : 'danger'}>
                        {testCase.ok ? '通过' : '失败'}
                      </StatusBadge>
                    </TableCell>
                    <TableCell className="text-right font-mono text-[11px] text-ha-text-2">
                      {testCase.score.toFixed(2)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-[11px] text-ha-muted">
                      {formatNumber(testCase.tokens)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-[11px] text-ha-muted">
                      {formatDuration(testCase.duration_ms)}
                    </TableCell>
                    <TableCell>
                      <span className="line-clamp-2 text-[11px] text-ha-danger">
                        {testCase.failures[0] ?? testCase.error ?? '—'}
                      </span>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}

      {onPageChange && count > pageSize ? (
        <div className="flex items-center justify-between gap-3 border-t border-ha-border px-4 py-2.5">
          <span className="font-mono text-[11px] text-ha-faint">
            第 {page} / {pages} 页 · 每页 {pageSize} 条
          </span>
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => onPageChange(page - 1)}
              className="rounded-md border border-ha-border px-2 py-1 text-[11px] text-ha-muted transition-colors hover:border-ha-accent hover:text-ha-text disabled:cursor-not-allowed disabled:opacity-40"
            >
              上一页
            </button>
            <button
              type="button"
              disabled={page >= pages}
              onClick={() => onPageChange(page + 1)}
              className="rounded-md border border-ha-border px-2 py-1 text-[11px] text-ha-muted transition-colors hover:border-ha-accent hover:text-ha-text disabled:cursor-not-allowed disabled:opacity-40"
            >
              下一页
            </button>
          </div>
        </div>
      ) : null}
    </ConsolePanel>
  );
}
