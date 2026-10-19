/**
 * 侧边栏:模式切换 + 新建会话 + 本地导航 + 会话列表 + 底部工作区。
 *
 * 排版对齐 apps/web 控制台侧栏(264px、会话行两行:标题 + 元信息、hover 出操作)。
 * 会话的增删改都直接调 window.beautyDesktop.sessions(没有 HTTP 数据层),
 * 写操作完成后由数据层统一刷新列表。
 */
import {
  Cloud,
  FolderInput,
  FolderOpen,
  FolderTree,
  MessagesSquare,
  Monitor,
  MoreHorizontal,
  Pencil,
  Plus,
  Settings,
  Trash2,
  type LucideIcon,
} from 'lucide-react';
import { useState, type ReactNode } from 'react';

import { useDesktop } from '@/lib/desktop-context';
import { baseName, relativeTime } from '@/lib/format';
import { cn } from '@/lib/utils';
import type { DesktopView } from '@/lib/views';
import { VIEW_LABELS } from '@/lib/views';

import { ConsoleButton, IconBox, Mono, StatusDot } from '@/components/ui';

const NAV_ITEMS: { view: DesktopView; icon: LucideIcon }[] = [
  { view: 'chat', icon: MessagesSquare },
  { view: 'files', icon: FolderTree },
  { view: 'settings', icon: Settings },
];

export function Sidebar({
  view,
  onViewChange,
}: {
  view: DesktopView;
  onViewChange: (view: DesktopView) => void;
}) {
  const { bridge, state, actions } = useDesktop();
  const mode = state.settings?.mode ?? 'local';
  const workspace = state.workspace;
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [confirmFor, setConfirmFor] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<{
    id: string;
    title: string;
  } | null>(null);
  const activeId = state.activeSession?.id ?? null;

  const openView = (next: DesktopView) => {
    if (mode === 'cloud') void actions.switchMode('local');
    onViewChange(next);
  };

  return (
    <aside
      aria-label="桌面导航"
      className="flex h-full w-[264px] shrink-0 flex-col border-r border-ha-border bg-ha-sidebar"
    >
      {/* 品牌 + 版本 */}
      <div className="flex items-center justify-between px-4 pb-3 pt-4">
        <div className="flex min-w-0 items-center gap-2">
          <span
            aria-hidden="true"
            className="grid h-6 w-6 shrink-0 place-items-center rounded-md bg-ha-accent"
          >
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none">
              <path
                d="M15 4a8 8 0 1 0 4 12 6 6 0 0 1-4-12z"
                fill="currentColor"
                className="text-white"
              />
            </svg>
          </span>
          <span className="truncate text-[13px] font-semibold text-ha-text">
            beauty moon
          </span>
        </div>
        <Mono className="shrink-0 text-ha-faint">v{bridge.appVersion}</Mono>
      </div>

      {/* 模式切换 */}
      <div className="px-3">
        <div
          role="group"
          aria-label="运行模式"
          className="grid grid-cols-2 gap-1 rounded-lg border border-ha-border bg-ha-panel p-1"
        >
          <ModeButton
            active={mode === 'local'}
            icon={<Monitor aria-hidden="true" className="h-3.5 w-3.5" />}
            label="本地电脑"
            onClick={() => void actions.switchMode('local')}
          />
          <ModeButton
            active={mode === 'cloud'}
            icon={<Cloud aria-hidden="true" className="h-3.5 w-3.5" />}
            label="云端"
            onClick={() => void actions.switchMode('cloud')}
          />
        </div>
      </div>

      {mode === 'local' ? (
        <>
          <div className="px-3 pb-2 pt-3">
            <ConsoleButton
              variant="primary"
              size="lg"
              className="w-full"
              onClick={() => void actions.createSession()}
              title={workspace ? '在当前工作区新建会话' : '先选择本地目录'}
            >
              <Plus aria-hidden="true" className="h-3.5 w-3.5" />
              新建会话
            </ConsoleButton>
          </div>

          <nav
            aria-label="本地导航"
            className="flex flex-col gap-0.5 px-3 pb-1"
          >
            {NAV_ITEMS.map((item) => {
              const Icon = item.icon;
              const active = view === item.view;
              return (
                <button
                  key={item.view}
                  type="button"
                  aria-current={active ? 'page' : undefined}
                  onClick={() => openView(item.view)}
                  className={cn(
                    'flex h-8 items-center gap-2.5 rounded-md px-2.5 text-left transition-colors',
                    active
                      ? 'bg-ha-panel-2 font-medium text-ha-text'
                      : 'text-ha-text-3 hover:bg-ha-panel-2/70 hover:text-ha-text-2',
                  )}
                >
                  <Icon aria-hidden="true" className="h-4 w-4 shrink-0" />
                  <span className="truncate text-[13px]">
                    {VIEW_LABELS[item.view]}
                  </span>
                </button>
              );
            })}
          </nav>

          {/* 会话列表 */}
          <div className="flex min-h-0 flex-1 flex-col px-3 pt-2">
            <div className="flex items-center justify-between px-2.5 pb-1.5">
              <span className="text-[11px] font-semibold text-ha-dim">
                最近会话
              </span>
              <Mono className="text-ha-faint">{state.sessions.length}</Mono>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto pb-2 pr-0.5">
              {state.sessions.length === 0 ? (
                <p className="px-2.5 py-2 text-[12px] leading-5 text-ha-dim">
                  还没有会话。点上方「新建会话」开始。
                </p>
              ) : null}

              {state.sessions.map((session) => {
                const selected = session.id === activeId;
                return (
                  <div
                    key={session.id}
                    className={cn(
                      'group relative flex items-center gap-2 rounded-md px-2.5 py-[9px] transition-colors',
                      selected ? 'bg-ha-panel-2' : 'hover:bg-ha-panel-2/60',
                    )}
                  >
                    <StatusDot tone={selected ? 'info' : 'neutral'} />
                    {renaming?.id === session.id ? (
                      <form
                        className="min-w-0 flex-1"
                        onSubmit={(event) => {
                          event.preventDefault();
                          void actions.renameSession(
                            session.id,
                            renaming.title,
                          );
                          setRenaming(null);
                        }}
                      >
                        <input
                          aria-label="会话标题"
                          autoFocus
                          value={renaming.title}
                          onChange={(event) =>
                            setRenaming({
                              id: session.id,
                              title: event.target.value,
                            })
                          }
                          onBlur={() => setRenaming(null)}
                          onKeyDown={(event) => {
                            if (event.key === 'Escape') setRenaming(null);
                            if (event.key === 'Enter') {
                              event.preventDefault();
                              void actions.renameSession(
                                session.id,
                                renaming.title,
                              );
                              setRenaming(null);
                            }
                          }}
                          className="h-6 w-full rounded border border-ha-accent bg-ha-panel px-1.5 text-[12px] text-ha-text outline-none"
                        />
                      </form>
                    ) : (
                      <button
                        type="button"
                        aria-current={selected ? 'page' : undefined}
                        onClick={() => {
                          openView('chat');
                          void actions.openSession(session.id);
                        }}
                        className="min-w-0 flex-1 text-left"
                      >
                        <span
                          className={cn(
                            'block truncate text-[13px]',
                            selected
                              ? 'font-medium text-ha-text'
                              : 'font-normal text-ha-text-2',
                          )}
                        >
                          {session.title}
                        </span>
                        <span className="mt-[3px] block truncate font-mono text-[11px] text-ha-dim">
                          {relativeTime(session.updatedAt) || '刚刚'} ·{' '}
                          {session.messageCount} 条
                        </span>
                      </button>
                    )}

                    <button
                      type="button"
                      aria-label="会话操作"
                      aria-expanded={menuFor === session.id}
                      onClick={() => {
                        setConfirmFor(null);
                        setMenuFor((value) =>
                          value === session.id ? null : session.id,
                        );
                      }}
                      className="absolute right-1 top-1 grid h-5 w-5 place-items-center rounded text-ha-dim opacity-0 transition-opacity hover:bg-ha-panel-2 hover:text-ha-text focus-visible:opacity-100 group-hover:opacity-100"
                    >
                      <MoreHorizontal
                        aria-hidden="true"
                        className="h-3.5 w-3.5"
                      />
                    </button>

                    {menuFor === session.id ? (
                      <>
                        <button
                          type="button"
                          aria-hidden="true"
                          tabIndex={-1}
                          onClick={() => {
                            setMenuFor(null);
                            setConfirmFor(null);
                          }}
                          className="fixed inset-0 z-10 cursor-default"
                        />
                        <div className="absolute right-1 top-7 z-20 w-36 rounded-md border border-ha-border bg-ha-sidebar p-1 shadow-[0_16px_40px_rgba(0,0,0,0.5)]">
                          {confirmFor === session.id ? (
                            <>
                              <p className="px-2 pb-1 pt-0.5 text-[11px] leading-4 text-ha-muted">
                                删除后不可恢复,确定?
                              </p>
                              <button
                                type="button"
                                onClick={() => {
                                  setMenuFor(null);
                                  setConfirmFor(null);
                                  void actions.removeSession(session.id);
                                }}
                                className="flex h-7 w-full items-center gap-2 rounded px-2 text-left text-[12px] text-ha-danger hover:bg-ha-danger-bg"
                              >
                                <Trash2
                                  aria-hidden="true"
                                  className="h-3 w-3"
                                />
                                确认删除
                              </button>
                            </>
                          ) : (
                            <>
                              <button
                                type="button"
                                onClick={() => {
                                  setMenuFor(null);
                                  setRenaming({
                                    id: session.id,
                                    title: session.title,
                                  });
                                }}
                                className="flex h-7 w-full items-center gap-2 rounded px-2 text-left text-[12px] text-ha-text-2 hover:bg-ha-panel-2"
                              >
                                <Pencil
                                  aria-hidden="true"
                                  className="h-3 w-3"
                                />
                                重命名
                              </button>
                              <button
                                type="button"
                                onClick={() => setConfirmFor(session.id)}
                                className="flex h-7 w-full items-center gap-2 rounded px-2 text-left text-[12px] text-ha-danger hover:bg-ha-danger-bg"
                              >
                                <Trash2
                                  aria-hidden="true"
                                  className="h-3 w-3"
                                />
                                删除
                              </button>
                            </>
                          )}
                        </div>
                      </>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </div>
        </>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-3 px-3 pt-3">
          <p className="rounded-md border border-ha-border bg-ha-panel px-3 py-2.5 text-[12px] leading-5 text-ha-dim">
            云端模式的任务在服务端运行,本地会话与文件暂停使用。
          </p>
          <button
            type="button"
            onClick={() => void bridge.cloud.openConsole()}
            className="flex items-center gap-2.5 rounded-md border border-ha-border bg-ha-panel p-2.5 text-left transition-colors hover:border-ha-border-strong"
          >
            <IconBox>
              <Cloud aria-hidden="true" className="h-3.5 w-3.5" />
            </IconBox>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[12px] font-medium text-ha-text-2">
                打开云端控制台
              </span>
              <span className="mt-0.5 block truncate font-mono text-[10px] text-ha-faint">
                {state.settings?.cloudConsoleUrl ?? '未配置'}
              </span>
            </span>
          </button>
        </div>
      )}

      {/* 底部:工作区目录 + 切换 */}
      <div className="border-t border-ha-border p-3">
        {workspace ? (
          <div className="flex items-center gap-2.5">
            <IconBox>
              <FolderOpen aria-hidden="true" className="h-3.5 w-3.5" />
            </IconBox>
            <div className="min-w-0 flex-1">
              <p className="truncate text-[12px] font-medium text-ha-text-2">
                {workspace.name || baseName(workspace.root)}
              </p>
              <p
                className="truncate font-mono text-[10px] text-ha-faint"
                title={workspace.root}
              >
                {workspace.root}
              </p>
            </div>
            <button
              type="button"
              aria-label="切换目录"
              title="切换目录"
              onClick={() => void actions.chooseWorkspace()}
              className="grid h-6 w-6 shrink-0 place-items-center rounded text-ha-dim transition-colors hover:bg-ha-panel-2 hover:text-ha-text"
            >
              <FolderInput aria-hidden="true" className="h-3.5 w-3.5" />
            </button>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            <p className="text-[12px] leading-5 text-ha-dim">未选择本地目录</p>
            <ConsoleButton
              variant="secondary"
              size="sm"
              className="w-full"
              onClick={() => void actions.chooseWorkspace()}
            >
              <FolderOpen aria-hidden="true" className="h-3.5 w-3.5" />
              选择目录
            </ConsoleButton>
          </div>
        )}
      </div>
    </aside>
  );
}

function ModeButton({
  active,
  icon,
  label,
  onClick,
}: {
  active: boolean;
  icon: ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        'flex h-7 items-center justify-center gap-1.5 rounded-md text-[12px] font-medium transition-colors',
        active
          ? 'bg-ha-accent text-white'
          : 'text-ha-muted hover:bg-ha-panel-2 hover:text-ha-text-2',
      )}
    >
      {icon}
      {label}
    </button>
  );
}
