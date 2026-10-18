/**
 * 工作台顶栏:面包屑 + 模型/运行状态 + 分享 + 头像。
 *
 * 对应设计稿《01 主控台》的 52px 顶栏。模型选择是只读展示:
 * 平台统一走 llm-gateway 路由,前端没有逐会话换模型的接口,不伪造下拉能力。
 */
import { useQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, Cpu, Share2 } from 'lucide-react';
import { useState } from 'react';
import { siteUrl } from '@/lib/site-links';

import { consoleButtonClass } from '@/components/console/button-class';
import { Avatar, RunStatusPill } from '@/components/console/ui';
import { accountLabel } from '@/lib/auth';
import type { RunStatus } from '@/lib/runs-api';
import { cn } from '@/lib/utils';

export function ConsoleTopbar({
  title,
  status,
}: {
  title: string;
  status?: RunStatus | null;
}) {
  const accountQuery = useQuery({
    queryKey: ['auth-account'],
    queryFn: accountLabel,
  });
  const [copied, setCopied] = useState(false);

  const share = async () => {
    try {
      await navigator.clipboard?.writeText(window.location.href);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      // 剪贴板不可用(非安全上下文)时静默:不打断对话
    }
  };

  return (
    <header className="flex h-[52px] shrink-0 items-center justify-between gap-3 border-b border-ha-border bg-ha-sidebar px-4 sm:px-5">
      <nav
        aria-label="面包屑"
        className="flex min-w-0 items-center gap-2 font-mono text-[12px]"
      >
        <a
          href={siteUrl('/')}
          className="shrink-0 text-ha-dim transition-colors hover:text-ha-text-2"
        >
          beauty moon
        </a>
        <ChevronRight
          aria-hidden="true"
          className="h-3 w-3 shrink-0 text-ha-ghost"
        />
        <span className="shrink-0 text-ha-dim">工作台</span>
        <ChevronRight
          aria-hidden="true"
          className="h-3 w-3 shrink-0 text-ha-ghost"
        />
        <span className="truncate text-[13px] font-semibold text-ha-text">
          {title}
        </span>
      </nav>

      <div className="flex shrink-0 items-center gap-2">
        <span
          className="hidden h-[30px] items-center gap-1.5 rounded-md border border-ha-border-strong bg-ha-panel-2 px-2.5 font-mono text-[12px] text-ha-text-2 sm:inline-flex"
          title="平台统一经 llm-gateway 路由,会话内不单独切换模型"
        >
          <Cpu aria-hidden="true" className="h-3.5 w-3.5 text-ha-muted" />
          自动路由
          <ChevronDown aria-hidden="true" className="h-3 w-3 text-ha-dim" />
        </span>

        {status ? (
          <RunStatusPill status={status} className="hidden sm:inline-flex" />
        ) : (
          <span className="hidden h-[30px] items-center gap-[7px] rounded-md border border-ha-border bg-ha-panel px-2.5 text-[12px] text-ha-dim sm:inline-flex">
            <span className="h-[7px] w-[7px] rounded-full bg-ha-ghost" />
            空闲
          </span>
        )}

        <button
          type="button"
          onClick={() => void share()}
          className={cn(
            consoleButtonClass({ variant: 'secondary', size: 'md' }),
            'h-[30px]',
          )}
        >
          <Share2 aria-hidden="true" className="h-3.5 w-3.5" />
          <span className="hidden sm:inline">{copied ? '已复制' : '分享'}</span>
        </button>

        <Avatar name={accountQuery.data ?? null} />
      </div>
    </header>
  );
}
