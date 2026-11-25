/**
 * 数据集只读目录:清单只回答"有哪些、是否可用",详情展开 manifest 与用例。
 *
 * 数据集随代码评审、随镜像发布,页面不提供上传/编辑;坏数据集也留在清单里,
 * 用状态徽标暴露 schema 错误,避免它静默消失。
 */
import { Link } from 'react-router';

import {
  StatusBadge,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@beauty/design';

import { ConsolePanel } from '@/components/console/ui';
import type {
  EvalDataset,
  EvalDatasetCase,
  EvalDatasetSummary,
} from '@/lib/eval-api';

import { formatNumber } from './format';

export function DatasetCatalog({
  items,
  selected,
  detail,
  detailLoading,
  detailError,
}: {
  items: EvalDatasetSummary[];
  selected: string;
  detail: EvalDataset | undefined;
  detailLoading: boolean;
  detailError: Error | null;
}) {
  return (
    <div className="space-y-4 px-6 pb-8 sm:px-8">
      <ConsolePanel className="overflow-hidden">
        <div className="flex items-center justify-between gap-3 border-b border-ha-border px-4 py-3">
          <div>
            <h2 className="text-[13px] font-semibold text-ha-text">
              数据集清单
            </h2>
            <p className="mt-0.5 text-[11px] text-ha-faint">
              随 git 评审、随镜像发布;门禁与运行都引用这里的 version
            </p>
          </div>
          <span className="font-mono text-[11px] text-ha-faint">
            {items.length} 个
          </span>
        </div>
        <div className="overflow-x-auto">
          <Table density="compact">
            <TableHeader>
              <TableRow>
                <TableHead className="min-w-[210px]">数据集</TableHead>
                <TableHead className="text-right">版本</TableHead>
                <TableHead className="text-right">用例</TableHead>
                <TableHead>分组</TableHead>
                <TableHead>owner</TableHead>
                <TableHead>标签</TableHead>
                <TableHead>状态</TableHead>
                <TableHead className="w-[72px] text-right">详情</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((item) => {
                const active = item.name === selected;
                return (
                  <TableRow
                    key={item.name}
                    className={active ? 'bg-ha-panel-2/60' : undefined}
                  >
                    <TableCell>
                      <Link
                        to={
                          '/evals/datasets?dataset=' +
                          encodeURIComponent(item.name)
                        }
                        className="text-[12px] font-medium text-ha-text hover:text-ha-accent-soft"
                      >
                        {item.name}
                      </Link>
                      <p className="mt-0.5 max-w-[360px] truncate text-[10px] text-ha-faint">
                        {item.description || '无描述'}
                      </p>
                    </TableCell>
                    <TableCell className="text-right font-mono text-[11px] text-ha-text-2">
                      v{item.version}
                    </TableCell>
                    <TableCell className="text-right font-mono text-[11px] text-ha-text-2">
                      {formatNumber(item.case_count)}
                    </TableCell>
                    <TableCell className="max-w-[220px] text-[11px] text-ha-muted">
                      {item.groups.length > 0 ? item.groups.join(', ') : '—'}
                    </TableCell>
                    <TableCell className="text-[11px] text-ha-muted">
                      {item.owner || '—'}
                    </TableCell>
                    <TableCell>
                      <TagList tags={item.tags} />
                    </TableCell>
                    <TableCell>
                      {item.valid ? (
                        <StatusBadge tone="success">可用</StatusBadge>
                      ) : (
                        <StatusBadge tone="danger">校验失败</StatusBadge>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <Link
                        to={
                          '/evals/datasets?dataset=' +
                          encodeURIComponent(item.name)
                        }
                        className="text-[11px] text-ha-muted hover:text-ha-text"
                      >
                        查看
                      </Link>
                    </TableCell>
                  </TableRow>
                );
              })}
              {items.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={8}
                    className="py-8 text-center text-[12px] text-ha-faint"
                  >
                    evals/datasets 下还没有数据集。
                  </TableCell>
                </TableRow>
              ) : null}
            </TableBody>
          </Table>
        </div>
      </ConsolePanel>

      {items.some((item) => !item.valid) ? (
        <p className="rounded-md border border-ha-warning-border bg-ha-warning-bg px-3 py-2.5 text-[11px] text-ha-warning">
          有数据集未通过 schema 校验,详情不可读;修正后重新运行 eval-service
          即可。
        </p>
      ) : null}

      <ConsolePanel>
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-ha-border px-4 py-3">
          <div>
            <h2 className="text-[13px] font-semibold text-ha-text">
              {selected || '数据集详情'}
            </h2>
            <p className="mt-0.5 text-[11px] text-ha-faint">
              用例是评测的输入与 grader 声明,运行结果在运行详情里看
            </p>
          </div>
          {detail ? (
            <span className="font-mono text-[10px] text-ha-faint">
              {detail.adapter}
            </span>
          ) : null}
        </div>

        {detailLoading ? (
          <p className="px-4 py-6 text-[12px] text-ha-muted">加载数据集…</p>
        ) : null}

        {detailError ? (
          <p className="m-4 rounded-md border border-ha-danger-border bg-ha-danger-bg px-3 py-2.5 text-[11px] text-ha-danger">
            数据集读取失败:{detailError.message}
          </p>
        ) : null}

        {detail ? <DatasetDetail dataset={detail} /> : null}

        {!detailLoading && !detailError && !detail ? (
          <p className="px-4 py-6 text-[12px] text-ha-faint">
            选择一个数据集查看用例。
          </p>
        ) : null}
      </ConsolePanel>
    </div>
  );
}

function DatasetDetail({ dataset }: { dataset: EvalDataset }) {
  return (
    <div>
      <div className="grid gap-3 border-b border-ha-border px-4 py-3 sm:grid-cols-2 xl:grid-cols-4">
        <Meta label="版本" value={'v' + dataset.version} />
        <Meta label="用例" value={formatNumber(dataset.case_count)} />
        <Meta label="分组" value={dataset.groups.join(', ') || '—'} />
        <Meta label="owner" value={dataset.owner || '—'} />
      </div>

      {dataset.description ? (
        <p className="border-b border-ha-border px-4 py-3 text-[12px] text-ha-text-2">
          {dataset.description}
        </p>
      ) : null}

      <div className="space-y-2 px-4 py-3">
        {dataset.cases.map((testCase) => (
          <DatasetCase key={testCase.id} testCase={testCase} />
        ))}
        {dataset.cases.length === 0 ? (
          <p className="py-4 text-[12px] text-ha-faint">这个数据集没有用例。</p>
        ) : null}
      </div>
    </div>
  );
}

function DatasetCase({ testCase }: { testCase: EvalDatasetCase }) {
  return (
    <details className="rounded-md border border-ha-border bg-ha-bg/40">
      <summary className="flex cursor-pointer flex-wrap items-center gap-2 px-3 py-2 text-[12px] text-ha-text">
        <span className="font-mono">{testCase.id}</span>
        <StatusBadge tone="neutral">{testCase.group}</StatusBadge>
        <TagList tags={testCase.tags} />
        <span className="ml-auto font-mono text-[10px] text-ha-faint">
          {testCase.graders.length} graders
        </span>
      </summary>
      <div className="grid gap-3 border-t border-ha-border px-3 py-3 lg:grid-cols-2">
        <Block title="input" value={testCase.input} />
        <Block title="graders" value={testCase.graders} />
      </div>
      {testCase.notes ? (
        <p className="border-t border-ha-border px-3 py-2 text-[11px] text-ha-muted">
          {testCase.notes}
        </p>
      ) : null}
    </details>
  );
}

function Block({ title, value }: { title: string; value: unknown }) {
  return (
    <div className="min-w-0">
      <p className="mb-1 font-mono text-[10px] uppercase tracking-wide text-ha-faint">
        {title}
      </p>
      <pre className="max-h-64 overflow-auto rounded-md border border-ha-border bg-ha-bg px-2.5 py-2 font-mono text-[11px] leading-relaxed text-ha-text-2">
        {JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}

function TagList({ tags }: { tags: string[] }) {
  if (tags.length === 0) {
    return <span className="text-[10px] text-ha-faint">—</span>;
  }
  return (
    <span className="flex flex-wrap gap-1">
      {tags.map((tag) => (
        <StatusBadge key={tag} tone="neutral">
          {tag}
        </StatusBadge>
      ))}
    </span>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wide text-ha-faint">
        {label}
      </p>
      <p className="mt-0.5 font-mono text-[12px] text-ha-text-2">{value}</p>
    </div>
  );
}
