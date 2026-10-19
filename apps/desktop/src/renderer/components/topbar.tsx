/**
 * 工作台顶栏:面包屑 + 当前模型 + 会话运行状态。
 *
 * 对齐 apps/web 控制台的 52px 顶栏。模型名是只读展示:本地模式下模型由设置页决定。
 */
import { ChevronRight, Cpu } from 'lucide-react';

import { useDesktop } from '@/lib/desktop-context';
import { VIEW_LABELS, type DesktopView } from '@/lib/views';

import { StatusPill } from '@/components/ui';

export function Topbar({ view }: { view: DesktopView }) {
  const { state } = useDesktop();
  const session = state.activeSession;
  const model = state.settings?.model.model;
  const running = Boolean(session && state.runningSessionId === session.id);

  return (
    <header className="flex h-[52px] shrink-0 items-center justify-between gap-3 border-b border-ha-border bg-ha-sidebar px-4">
      <nav
        aria-label="面包屑"
        className="flex min-w-0 items-center gap-2 font-mono text-[12px]"
      >
        <span className="shrink-0 text-ha-dim">本地电脑</span>
        <ChevronRight
          aria-hidden="true"
          className="h-3 w-3 shrink-0 text-ha-ghost"
        />
        <span className="shrink-0 text-ha-dim">{VIEW_LABELS[view]}</span>
        {view === 'chat' && session ? (
          <>
            <ChevronRight
              aria-hidden="true"
              className="h-3 w-3 shrink-0 text-ha-ghost"
            />
            <span className="truncate text-[13px] font-semibold text-ha-text">
              {session.title}
            </span>
          </>
        ) : null}
      </nav>

      <div className="flex shrink-0 items-center gap-2">
        <span
          className="inline-flex h-[26px] max-w-[240px] items-center gap-1.5 rounded-md border border-ha-border-strong bg-ha-panel-2 px-2.5 font-mono text-[11px] text-ha-text-2"
          title={model ? `当前模型:${model}` : '尚未配置模型端点'}
        >
          <Cpu
            aria-hidden="true"
            className="h-3.5 w-3.5 shrink-0 text-ha-muted"
          />
          <span className="truncate">{model || '未配置模型'}</span>
        </span>
        {view === 'chat' && session ? (
          running ? (
            <StatusPill tone="info" pulse>
              运行中
            </StatusPill>
          ) : (
            <StatusPill tone="neutral">空闲</StatusPill>
          )
        ) : null}
      </div>
    </header>
  );
}
