/**
 * 统一过程项渲染器(P1-D 事件分类学):消费 `GET /runs/{id}/items` 的投影。
 *
 * 一个 item = run 内一个原子过程项,kind 决定卡片形状:
 * - `plan`:一等过程项,显示「第 N 版」徽标 + Markdown 正文,被 `supersedes`
 *   取代的旧版本折进「旧版本」可折叠组(计划修订 = 新版本,不再是快照回看);
 * - `tool` / `file_change`:工具名/参数/结果摘要,文件变更另显示路径与 sha256 前 8 位;
 * - `mcp_call`:server / tool;`search`:查询与参数;`todo`:复用 RunTodoList 清单;
 * - `subagent`(P1-E):子代理委派,显示子 run 状态与 token 成本,展开看摘要,
 *   完成后可经 `onOpenRun` 就地切到子 run 详情。
 *
 * 交互统一:默认折叠结果详情(避免长文本撑爆抽屉),展开后显示结构化 data;
 * `started` 项显示「进行中」,`failed` / `denied` 显示状态徽标。本组件是纯展示
 * 层,数据由 run-detail 轮询 `itemsQuery` 注入,不感知 SSE(未知事件忽略)。
 *
 * 拆分后:卡片见 ./run-item-card.tsx,正文/详情见 ./run-item-plan-body.tsx 与
 * ./run-item-detail.tsx,元信息与 data 解析见 ./run-item-meta.ts。
 */
import type { RunItem } from '@/lib/runs-api';
import { cn } from '@/lib/utils';

import { RunItemCard } from './run-item-card';
import { splitPlanVersions } from './run-item-meta';

/* ── 时间线 ───────────────────────────────────────────────────────────── */

export function RunItemTimeline({
  items,
  className,
  onOpenRun,
}: {
  items: readonly RunItem[];
  className?: string;
  /**
   * 打开子 run(P1-E):subagent 过程项在终态后出现「打开子 run」,
   * 由 run-detail 注入 `setSelectedRun` 就地切换,不离开当前看板。
   */
  onOpenRun?: (runId: string) => void;
}) {
  if (items.length === 0) {
    return (
      <p
        data-testid="run-item-empty"
        className={cn(
          'rounded-md border border-dashed border-ha-border px-4 py-6 text-center text-[12px] leading-5 text-ha-dim',
          className,
        )}
      >
        还没有过程项记录。计划、工具、文件变更、MCP
        调用、检索与任务清单会随执行出现在这里。
      </p>
    );
  }

  const { current, history } = splitPlanVersions(items);

  return (
    <ol
      aria-label="运行项时间线"
      data-testid="run-item-timeline"
      className={cn('space-y-1.5', className)}
    >
      {items.map((item) => {
        // 计划旧版本折进当前计划卡的「旧版本」组,不再单独占一条。
        if (item.kind === 'plan' && current?.item_id !== item.item_id) {
          return null;
        }
        return (
          <RunItemCard
            key={item.item_id}
            item={item}
            planHistory={item.kind === 'plan' ? history : []}
            onOpenRun={onOpenRun}
          />
        );
      })}
    </ol>
  );
}
