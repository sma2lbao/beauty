/**
 * 文件查看器顶栏:文件名/路径、内容/改动页签、「跳到上次修改」与关闭按钮。
 *
 * 是否可跳转由外壳算好传入;这里只负责展示与回调。
 */
import { ArrowDownToLine, X } from 'lucide-react';

import { ConsoleTabs } from '@/components/console/ui';

import type { SandboxViewerTab } from './sandbox-file-viewer';

export function SandboxFileViewerHeader({
  name,
  path,
  tab,
  onTabChange,
  canJump,
  onJumpToLastChange,
  onClose,
}: {
  name: string;
  path: string;
  tab: SandboxViewerTab;
  onTabChange: (tab: SandboxViewerTab) => void;
  canJump: boolean;
  onJumpToLastChange: () => void;
  onClose: () => void;
}) {
  return (
    <header className="flex h-12 shrink-0 items-center gap-2 border-b border-ha-border px-4">
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13px] font-semibold text-ha-text">
          {name}
        </span>
        <span className="block truncate font-mono text-[11px] text-ha-faint">
          {path}
        </span>
      </span>
      <ConsoleTabs
        value={tab}
        onChange={onTabChange}
        items={[
          { value: 'content', label: '内容' },
          { value: 'diff', label: '改动' },
        ]}
      />
      <button
        type="button"
        aria-label="跳到上次修改"
        title={
          canJump
            ? '滚动到该文件最后一个补丁'
            : '本次会话还没有该文件的补丁，无法跳转'
        }
        onClick={onJumpToLastChange}
        disabled={!canJump}
        className="grid h-7 w-7 place-items-center rounded-md text-ha-muted transition-colors hover:bg-ha-panel-2 hover:text-ha-text disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-ha-muted"
      >
        <ArrowDownToLine aria-hidden="true" className="h-4 w-4" />
      </button>
      <button
        type="button"
        aria-label="关闭文件查看器"
        onClick={onClose}
        className="grid h-7 w-7 place-items-center rounded-md text-ha-muted transition-colors hover:bg-ha-panel-2 hover:text-ha-text"
      >
        <X aria-hidden="true" className="h-4 w-4" />
      </button>
    </header>
  );
}
