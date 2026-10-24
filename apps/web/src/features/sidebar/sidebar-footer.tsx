/**
 * 侧边栏底部:通知入口(整行入口 + 面板向上展开)+ 账户入口。
 */
import { Link } from 'react-router';

import { NotificationCenter } from '@/components/console/notification-center';

export function SidebarFooter({
  account,
  planLabel,
}: {
  account: string | null;
  planLabel: string;
}) {
  return (
    <>
      {/* 通知入口:整行入口 + 面板向上展开(铃铛贴着左下角,向下展开会被视口裁掉) */}
      <div className="shrink-0 border-b border-ha-border pb-2">
        <NotificationCenter variant="bar" />
      </div>

      <Link
        to="/settings"
        className="flex shrink-0 items-center gap-2.5 rounded-md border border-ha-border bg-ha-panel p-2.5 transition-colors hover:border-ha-border-strong"
      >
        <span
          aria-hidden="true"
          className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-[#2a2d35] font-mono text-[11px] text-ha-muted"
        >
          {(account?.trim().charAt(0) || '访').toUpperCase()}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] font-medium text-ha-text">
            {account ?? '未登录'}
          </span>
          <span className="mt-0.5 block truncate font-mono text-[11px] text-ha-dim">
            {planLabel}
          </span>
        </span>
      </Link>
    </>
  );
}
