/**
 * 会话区空态:还没有任何工作区时引导去创建工作区;有工作区但当前没有会话时,
 * 展示「开始一个新会话」hero(保存到哪个工作区 + 三条示例指令)。
 * 两块互斥,同时不成立时什么都不渲染。
 */
import { FolderKanban, Sparkles } from 'lucide-react';
import type { ReactNode } from 'react';
import { Link } from 'react-router';

import { Select } from '@beauty/design';

import { consoleButtonClass } from '@/components/console/button-class';
import { EmptyPanel } from '@/components/console/ui';
import type { Workspace } from '@/lib/workspace-api';

const SUGGESTIONS = [
  '把本周文档整理成一份周报草稿',
  '帮我理一下这周的待办,给出优先级',
  '基于知识库整理一份产品背景资料',
];

export function ChatEmptyState({
  hasNoWorkspaces,
  showHero,
  workspaces,
  activeWorkspace,
  onChangeWorkspace,
  onSuggestion,
}: {
  hasNoWorkspaces: boolean;
  /** 有工作区但当前既没有会话、也没有流式内容时才展示 hero。 */
  showHero: boolean;
  workspaces: Workspace[];
  activeWorkspace: Workspace | null;
  onChangeWorkspace: (workspaceId: string) => void;
  onSuggestion: (suggestion: string) => void;
}): ReactNode {
  if (!hasNoWorkspaces && !showHero) {
    return null;
  }
  if (hasNoWorkspaces) {
    return (
      <div className="flex min-h-[52vh] items-center justify-center">
        <EmptyPanel
          className="w-full max-w-xl"
          icon={<FolderKanban className="h-7 w-7" />}
          title="还没有工作区"
          description="会话必须归属于工作区:先创建一个工作区"
          action={
            <Link
              to="/workspace?new=1"
              className={consoleButtonClass({ variant: 'primary', size: 'sm' })}
            >
              去创建工作区
            </Link>
          }
        />
      </div>
    );
  }
  return (
    <div className="flex flex-col items-center gap-4 py-12 text-center">
      <span className="grid h-11 w-11 place-items-center rounded-xl border border-ha-border bg-[#1f2333]">
        <Sparkles className="h-5 w-5 text-ha-accent-soft" />
      </span>
      <div>
        <h2 className="text-[17px] font-semibold text-ha-text">
          开始一个新会话
        </h2>
        <p className="mx-auto mt-2 max-w-md text-[13px] leading-6 text-ha-muted">
          描述要完成的事。简单的直接回答;复杂任务会先给计划,
          等你放行再执行,危险操作会单独确认。
        </p>
        {activeWorkspace ? (
          <div className="mt-3 flex flex-wrap items-center justify-center gap-2">
            <label
              htmlFor="session-workspace"
              className="font-mono text-[11px] text-ha-faint"
            >
              保存到
            </label>
            <Select
              id="session-workspace"
              size="sm"
              aria-label="选择工作区"
              value={activeWorkspace.id}
              onChange={(event) => onChangeWorkspace(event.target.value)}
            >
              {workspaces.map((workspace) => (
                <option key={workspace.id} value={workspace.id}>
                  {workspace.name}
                </option>
              ))}
            </Select>
          </div>
        ) : null}
        {activeWorkspace ? (
          <p className="mx-auto mt-2 max-w-md text-[12px] leading-5 text-ha-dim">
            会话将保存在「{activeWorkspace.name}」中。
          </p>
        ) : null}
      </div>
      <div className="flex max-w-lg flex-wrap justify-center gap-2">
        {SUGGESTIONS.map((suggestion) => (
          <button
            key={suggestion}
            type="button"
            onClick={() => onSuggestion(suggestion)}
            className="rounded-md border border-ha-border bg-ha-panel px-3 py-2 text-left text-[12px] text-ha-text-2 transition-colors hover:border-ha-border-strong hover:text-ha-text"
          >
            {suggestion}
          </button>
        ))}
      </div>
    </div>
  );
}
