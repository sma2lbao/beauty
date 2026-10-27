/**
 * 任务清单:消费 `GET /runs/{id}/todos` 的投影。
 *
 * 与「节点步骤 / 工具调用」的分工:那两块回答「怎么执行的」,任务清单回答
 * 「交代的事做到哪了」——content 由模型经 todo 工具写入,status 只有三态
 * (pending / in_progress / completed)。空清单是合法结果(旧 run 或从未调用
 * todo 工具),整块不渲染、不占位。
 */
import { Check, Circle, Loader2 } from 'lucide-react';

import { ToneBadge } from '@/components/console/ui';
import type { RunTodoItem } from '@/lib/runs-api';
import { cn } from '@/lib/utils';

export function RunTodoList({
  todos,
  className,
}: {
  todos: readonly RunTodoItem[];
  className?: string;
}) {
  if (todos.length === 0) {
    return null;
  }
  const completed = todos.filter((todo) => todo.status === 'completed').length;

  return (
    <div className={cn('space-y-1.5', className)}>
      <div className="flex items-center justify-between px-0.5">
        <span className="text-[11px] font-semibold text-ha-dim">任务清单</span>
        <ToneBadge tone={completed === todos.length ? 'success' : 'neutral'}>
          {completed}/{todos.length}
        </ToneBadge>
      </div>
      <ul
        aria-label="任务清单"
        data-testid="run-todo-list"
        className="space-y-1 rounded-md border border-ha-border bg-ha-panel px-2.5 py-2"
      >
        {todos.map((todo, index) => (
          <li
            key={`${index}:${todo.content}`}
            className="flex items-start gap-2"
            data-status={todo.status}
          >
            <span className="mt-0.5 grid h-4 w-4 shrink-0 place-items-center">
              <TodoIcon status={todo.status} />
            </span>
            <span
              className={cn(
                'min-w-0 flex-1 text-[12px] leading-4',
                todo.status === 'in_progress'
                  ? 'font-medium text-ha-text'
                  : 'text-ha-dim',
                todo.status === 'completed' && 'line-through',
              )}
            >
              {todo.content}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function TodoIcon({ status }: { status: RunTodoItem['status'] }) {
  if (status === 'completed') {
    return <Check aria-hidden="true" className="h-3.5 w-3.5 text-ha-success" />;
  }
  if (status === 'in_progress') {
    return (
      <Loader2
        aria-hidden="true"
        className="h-3.5 w-3.5 animate-spin text-ha-info"
      />
    );
  }
  return <Circle aria-hidden="true" className="h-3.5 w-3.5 text-ha-ghost" />;
}
