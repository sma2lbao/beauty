/**
 * 看板搜索框:按标题/目标过滤 run(服务端 q,输入延迟由 use-runs-model 的
 * useDeferredValue 控制,与侧栏会话搜索同款)。
 */
import { Search } from 'lucide-react';

import type { RunsModel } from './use-runs-model';

export function RunSearchInput({ model }: { model: RunsModel }) {
  return (
    <div className="relative">
      <Search
        aria-hidden="true"
        className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ha-faint"
      />
      <input
        aria-label="搜索任务"
        maxLength={100}
        value={model.query}
        onChange={(event) => model.setQuery(event.target.value)}
        placeholder="搜索任务标题或目标…"
        className="h-8 w-52 rounded-md border border-ha-border bg-ha-panel pl-7 pr-2.5 text-[12px] text-ha-text-body outline-none placeholder:text-ha-faint focus:border-ha-accent"
      />
    </div>
  );
}
