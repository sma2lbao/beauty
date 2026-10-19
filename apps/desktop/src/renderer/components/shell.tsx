/**
 * 应用外壳:侧边栏 + 主区域 + 全局提示。
 *
 * 视图切换用本地 state(桌面端没有 URL 路由需求,MVP 不为它引入 react-router):
 * - 本地模式:工作台 / 文件 / 设置;
 * - 云端模式:整块主区域交给 CloudPage。
 */
import { X } from 'lucide-react';
import { useState } from 'react';

import { useDesktop } from '@/lib/desktop-context';
import { cn } from '@/lib/utils';
import type { DesktopView } from '@/lib/views';

import { ChatPage } from '@/pages/chat-page';
import { CloudPage } from '@/pages/cloud-page';
import { FilesPage } from '@/pages/files-page';
import { SettingsPage } from '@/pages/settings-page';

import { Sidebar } from '@/components/sidebar';
import { BootErrorPage, BootLoadingPage } from '@/components/startup-screens';
import { Topbar } from '@/components/topbar';

export function DesktopShell() {
  const { state, actions } = useDesktop();
  const [view, setView] = useState<DesktopView>('chat');
  const mode = state.settings?.mode ?? 'local';

  if (state.phase === 'loading') return <BootLoadingPage />;
  if (state.phase === 'error') {
    return <BootErrorPage error={state.bootError} onRetry={actions.reload} />;
  }

  const openSettings = () => {
    if (mode === 'cloud') void actions.switchMode('local');
    setView('settings');
  };

  return (
    <div className="relative flex h-full min-h-0 bg-ha-bg text-ha-text-body">
      <Sidebar view={view} onViewChange={setView} />
      <main className="flex min-w-0 flex-1 flex-col">
        {mode === 'cloud' ? (
          <CloudPage
            onBackToLocal={() => void actions.switchMode('local')}
            onOpenSettings={openSettings}
          />
        ) : (
          <>
            <Topbar view={view} />
            {view === 'chat' ? (
              <ChatPage />
            ) : view === 'files' ? (
              <FilesPage />
            ) : (
              <SettingsPage />
            )}
          </>
        )}
      </main>
      <Toast />
    </div>
  );
}

/** 全局提示:写操作成功(绿)或失败(红),可手动关闭。 */
function Toast() {
  const { state, actions } = useDesktop();
  const error = state.actionError;
  const notice = state.notice;
  if (!error && !notice) return null;

  return (
    <div
      role={error ? 'alert' : 'status'}
      className={cn(
        'fixed bottom-4 right-4 z-50 flex max-w-sm items-start gap-2 rounded-md border px-3 py-2 text-[12px] leading-5 shadow-[0_16px_40px_rgba(0,0,0,0.5)]',
        error
          ? 'border-ha-danger-border bg-ha-danger-bg text-ha-danger'
          : 'border-ha-success-border bg-ha-success-bg text-ha-success',
      )}
    >
      <span className="min-w-0 flex-1 break-words">{error ?? notice}</span>
      <button
        type="button"
        aria-label="关闭提示"
        onClick={actions.dismissNotice}
        className="mt-0.5 shrink-0 opacity-70 transition-opacity hover:opacity-100"
      >
        <X aria-hidden="true" className="h-3 w-3" />
      </button>
    </div>
  );
}
