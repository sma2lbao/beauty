/**
 * MCP server 管理区:左侧注册表 + 右侧详情与工具清单。
 *
 * 「连接器」页的「我的 MCP 服务」段与 /mcp 直链页共用这一块;
 * OAuth 回跳提示默认由本组件渲染,嵌进连接器页时关掉,由该页预设区的提示条
 * 统一消费同一组 ?oauth= 参数,避免同一次授权弹两条提示。
 */
import { Plug } from 'lucide-react';

import { ConsolePanel, EmptyPanel } from '@/components/console/ui';
import { ServerDetailCard } from '@/features/mcp/server-detail-card';
import { ServerListPanel } from '@/features/mcp/server-list-panel';
import { ServerToolsCard } from '@/features/mcp/server-tools-card';
import type { McpPageModel } from '@/features/mcp/use-mcp-page-model';

export type McpManagementSectionProps = {
  model: McpPageModel;
  /** 嵌在连接器页时设为 false:OAuth 回跳提示由预设区统一展示 */
  showOAuthNotice?: boolean;
};

export function McpManagementSection({
  model,
  showOAuthNotice = true,
}: McpManagementSectionProps) {
  const { oauthStatus, oauthServerName, oauthErrorText, selected, status } =
    model;

  return (
    <div>
      {showOAuthNotice && oauthStatus === 'success' ? (
        <ConsolePanel className="mt-4 p-3">
          <p className="text-[12px] font-medium text-ha-success">
            {oauthServerName
              ? '浏览器授权成功:' + oauthServerName
              : '浏览器授权成功'}
          </p>
          <p className="mt-1 text-[11px] leading-4 text-ha-text-2">
            授权状态已刷新,可点「测试/刷新」重新拉取该 server 的工具。
          </p>
        </ConsolePanel>
      ) : null}
      {showOAuthNotice && oauthStatus === 'error' ? (
        <ConsolePanel className="mt-4 p-3 text-[12px] text-ha-danger">
          浏览器授权失败:{oauthErrorText || '未知错误'}
        </ConsolePanel>
      ) : null}

      <div className="mt-4 grid gap-4 lg:grid-cols-[320px_1fr]">
        <ServerListPanel model={model} />

        {selected && status ? (
          <div className="space-y-4">
            <ServerDetailCard model={model} />
            <ServerToolsCard model={model} />
          </div>
        ) : (
          <EmptyPanel
            icon={<Plug className="h-6 w-6" />}
            title="还没有注册 MCP server"
            description="在左侧点 + 注册:填远程 Streamable HTTP server 的地址,或由沙箱拉起的本地 stdio 进程。注册后可测试、启停,并查看它暴露给任务的工具。"
          />
        )}
      </div>
    </div>
  );
}
