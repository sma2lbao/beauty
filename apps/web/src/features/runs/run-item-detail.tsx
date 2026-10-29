/**
 * 运行项展开后的结构化详情:两列字段行 + 路径/sha256 + 长文本块;
 * todo 复用任务清单,subagent 另外渲染结论摘要与失败原因。
 */
import { Markdown } from '@/components/console/markdown';
import { RunTodoList } from '@/features/runs/run-todo-list';
import type { RunItem } from '@/lib/runs-api';

import {
  type DetailBlock,
  type DetailRow,
  detailBlocks,
  detailRows,
} from './run-item-detail-data';
import {
  listOfText,
  numberOf,
  subagentOf,
  textOf,
  todoItemsOf,
} from './run-item-meta';

/* ── 展开后的结构化详情 ───────────────────────────────────────────────── */

export function RunItemDetail({ item }: { item: RunItem }) {
  if (item.kind === 'todo') {
    return (
      <div
        data-testid={`run-item-detail-${item.item_id}`}
        className="border-t border-ha-border px-3 py-2"
      >
        <TodoDetail item={item} />
      </div>
    );
  }

  if (item.kind === 'subagent') {
    return <SubagentDetail item={item} />;
  }

  const rows = detailRows(item);
  const blocks = detailBlocks(item);
  const paths = item.kind === 'file_change' ? listOfText(item.data.paths) : [];
  const shaBefore = textOf(item.data.sha256_before);
  const shaAfter = textOf(item.data.sha256_after);

  return (
    <div
      data-testid={`run-item-detail-${item.item_id}`}
      className="space-y-2 border-t border-ha-border px-3 py-2"
    >
      <DetailRowsView rows={rows} />

      {paths.length > 0 ? (
        <div>
          <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-ha-faint">
            路径
          </span>
          <ul className="mt-1 space-y-0.5">
            {paths.map((path) => (
              <li
                key={path}
                className="break-all font-mono text-[11px] text-ha-text-2"
              >
                {path}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {shaBefore || shaAfter ? (
        <p className="font-mono text-[11px] text-ha-dim">
          sha256: {shortSha(shaBefore)} → {shortSha(shaAfter)}
        </p>
      ) : null}

      {blocks.map((block) => (
        <DetailBlockView key={block.label} block={block} />
      ))}
    </div>
  );
}

/** 结构化 data 的两列 dl(通用项与子代理项共用,空 rows 不渲染)。 */
function DetailRowsView({ rows }: { rows: DetailRow[] }) {
  if (rows.length === 0) return null;
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 font-mono text-[11px] leading-[17px]">
      {rows.map((row) => (
        <div key={row.label} className="contents">
          <dt className="text-ha-faint">{row.label}</dt>
          <dd className="break-all text-ha-muted">{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * 子代理过程项展开后的详情(P1-E §7/§10):结构化行 + 摘要 + 失败原因。
 *
 * 摘要按 Markdown 渲染(模型输出),失败原因用纯文本标红;started 相位还没有
 * 摘要时给出占位文案,而不是渲染空白块。
 */
function SubagentDetail({ item }: { item: RunItem }) {
  const subagent = subagentOf(item);
  const rows = detailRows(item);
  return (
    <div
      data-testid={`run-item-detail-${item.item_id}`}
      className="space-y-2 border-t border-ha-border px-3 py-2"
    >
      <DetailRowsView rows={rows} />
      {subagent?.summary ? (
        <div>
          <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-ha-faint">
            摘要
          </span>
          <div
            data-testid={`run-item-subagent-summary-${item.item_id}`}
            className="mt-1 max-h-48 overflow-auto rounded-md border border-ha-border bg-ha-panel-2 px-2.5 py-2"
          >
            <Markdown content={subagent.summary} />
          </div>
        </div>
      ) : (
        <p className="text-[11px] leading-5 text-ha-dim">
          子代理还没有给出结论摘要。
        </p>
      )}
      {subagent?.reason ? (
        <p className="text-[11px] leading-5 text-ha-danger">
          失败原因:{subagent.reason}
        </p>
      ) : null}
    </div>
  );
}

function DetailBlockView({ block }: { block: DetailBlock }) {
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-ha-faint">
          {block.label}
        </span>
        <span className="font-mono text-[10px] text-ha-faint">
          {block.content.length} 字符
        </span>
      </div>
      <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md border border-ha-border bg-ha-panel-2 px-2.5 py-2 font-mono text-[11px] leading-[17px] text-ha-muted">
        {block.content}
      </pre>
    </div>
  );
}

function TodoDetail({ item }: { item: RunItem }) {
  const todos = todoItemsOf(item.data.todos);
  const revision = numberOf(item.data.revision);
  return (
    <div className="space-y-2">
      {revision !== null ? (
        <p className="font-mono text-[10px] text-ha-faint">
          清单版本 r{revision}
        </p>
      ) : null}
      {todos.length > 0 ? (
        <RunTodoList todos={todos} />
      ) : (
        <p className="text-[11px] leading-5 text-ha-dim">
          这份快照里还没有任务条目。
        </p>
      )}
    </div>
  );
}

/** sha256 只展示前 8 位,够人眼比对又不挤占布局(与 ToolCallCard 一致)。 */
function shortSha(value: string | null): string {
  return value ? value.slice(0, 8) : '—';
}
