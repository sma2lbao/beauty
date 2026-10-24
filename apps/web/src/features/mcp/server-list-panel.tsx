/**
 * 左列:server 注册表(标题 + 展开注册表单 + 列表)。
 */
import { Globe, Plus, Terminal } from 'lucide-react';

import { ConsolePanel, ToneBadge } from '@/components/console/ui';

import { describeEndpoint, serverStatus } from './display';
import { ServerForm } from './server-form';
import type { McpPageModel } from './use-mcp-page-model';

export function ServerListPanel({ model }: { model: McpPageModel }) {
  const { servers, selected, showForm, setShowForm, setSelectedId } = model;

  return (
    <ConsolePanel className="p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[12px] font-semibold text-ha-text-2">
          我的 MCP
        </span>
        <button
          type="button"
          aria-label="添加 MCP server"
          onClick={() => setShowForm((value) => !value)}
          className="grid h-6 w-6 place-items-center rounded text-ha-muted hover:bg-ha-panel-2 hover:text-ha-text"
        >
          <Plus className="h-3.5 w-3.5" />
        </button>
      </div>

      {showForm ? <ServerForm model={model} /> : null}

      <div className="mt-3 space-y-1">
        {servers.length === 0 ? (
          <p className="px-1 py-3 text-[12px] leading-5 text-ha-dim">
            还没有注册 MCP server。点右上角 + 添加:填远程 HTTP
            地址,或由沙箱拉起的本地 stdio 进程。
          </p>
        ) : null}
        {servers.map((server) => {
          const tone = serverStatus(server);
          return (
            <button
              key={server.id}
              type="button"
              onClick={() => setSelectedId(server.id)}
              className={
                'flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left transition-colors ' +
                (selected?.id === server.id
                  ? 'bg-[#1c1d1f]'
                  : 'hover:bg-ha-panel-2/60')
              }
            >
              <span className="grid h-7 w-7 shrink-0 place-items-center rounded-md bg-ha-panel-2">
                {server.transport === 'stdio' ? (
                  <Terminal className="h-3.5 w-3.5 text-ha-muted" />
                ) : (
                  <Globe className="h-3.5 w-3.5 text-ha-muted" />
                )}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] text-ha-text-2">
                  {server.name}
                </span>
                <span className="block truncate font-mono text-[11px] text-ha-dim">
                  {describeEndpoint(server)}
                </span>
              </span>
              <ToneBadge tone={tone.tone}>{tone.label}</ToneBadge>
            </button>
          );
        })}
      </div>
    </ConsolePanel>
  );
}
