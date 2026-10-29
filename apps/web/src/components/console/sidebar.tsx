/**
 * 控制台侧边栏:品牌 + 新会话 + 工作区切换器 + 主导航 + 当前工作区会话 + 账户。
 *
 * 交互对齐 dsh 侧栏的「工作区 / 会话」心智:
 * - URL ?workspace=<id> 是当前工作区的唯一真源,切换工作区只改 URL;
 * - 工作区由一行的切换器(触发器 + 下拉菜单,见 WorkspaceSwitcher)切换与管理,
 *   侧栏不再平铺全部工作区;
 * - 会话区只列当前工作区的会话,标题为「<工作区> 的会话」;
 *   归属为空、或指向已删除工作区的会话收进「未分组」一段,不丢可见性;
 * - 列表仍取租户全部会话(不按工作区过滤),这里只做客户端过滤:
 *   搜索/归档语义不变,「未分组」会话也依然可达;
 * - 重命名 / 归档 / 软删除就地操作,成功后失效会话查询并同步当前选中会话;
 * - 分页用 keyset 游标,加载更多不丢不重。
 *
 * 数据与派生逻辑在 features/sidebar/use-sidebar-model.ts,单条会话、
 * 会话列表、导航与底部区块在 features/sidebar/ 下,这里只做布局组合。
 */
import { PanelLeftClose, Plus } from 'lucide-react';
import { Link } from 'react-router';

import { BrandLockup } from '@/components/brand/logo';
import { consoleButtonClass } from '@/components/console/button-class';
import { WorkspaceSwitcher } from '@/features/workspace/workspace-switcher';
import { MoreNav, PrimaryNav } from '@/features/sidebar/nav';
import { SessionList } from '@/features/sidebar/session-list';
import { SidebarFooter } from '@/features/sidebar/sidebar-footer';
import { useSidebarModel } from '@/features/sidebar/use-sidebar-model';
import { cn } from '@/lib/utils';
import { useUiStore } from '@/stores/ui-store';

export function ConsoleSidebar() {
  const model = useSidebarModel();
  const setSidebarVisible = useUiStore((state) => state.setSidebarVisible);
  const { workspaceCounts, newSessionHref, account, planLabel } = model;

  return (
    <aside
      aria-label="控制台导航"
      className="hidden h-full w-[264px] shrink-0 flex-col gap-[18px] border-r border-ha-border bg-ha-sidebar px-3 py-4 lg:flex"
    >
      <div className="flex items-center justify-between">
        <Link to="/" aria-label="beauty moon 工作台">
          <BrandLockup size={28} />
        </Link>
        <div className="flex items-center gap-1.5">
          <span className="inline-flex h-5 items-center rounded-[4px] border border-ha-border-strong bg-ha-panel-2 px-1.5 font-mono text-[10px] font-medium text-ha-muted">
            PROD
          </span>
          <button
            type="button"
            aria-label="隐藏侧边栏"
            title="隐藏侧边栏"
            onClick={() => setSidebarVisible(false)}
            className="grid h-6 w-6 place-items-center rounded text-ha-faint hover:bg-ha-panel-2 hover:text-ha-text-2"
          >
            <PanelLeftClose className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      <Link
        to={newSessionHref}
        className={cn(
          consoleButtonClass({ variant: 'primary', size: 'lg' }),
          'w-full',
        )}
      >
        <Plus className="h-3.5 w-3.5" />
        新会话
      </Link>

      {/* 工作区:一行切换器 + 下拉菜单(切换/添加/管理) */}
      <WorkspaceSwitcher counts={workspaceCounts} />

      <PrimaryNav />

      <SessionList model={model} />

      <MoreNav />

      <SidebarFooter account={account} planLabel={planLabel} />
    </aside>
  );
}
