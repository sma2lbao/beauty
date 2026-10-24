/**
 * MCP 页(M1 本地版):用户自定义 MCP server 的注册表与连接定义。
 *
 * 定义式交互:左侧列表(右上 + 展开定义表单)、右侧详情;
 * - http:远程 Streamable HTTP server(URL/Token);
 * - stdio:本地进程(命令/参数/环境变量/运行时/镜像),由 orchestrator 沙箱拉起。
 * Token 与环境变量只写不读:读端只返回尾号与变量名,提交成功后清空敏感输入。
 * 「免确认执行」是每个 server 的信任开关:会话没有审批流(§6.7),只有开了它的
 * 工具才能在会话里直接被调用;关闭时工具仍可在「任务」路径按确认流程使用。
 */
import { Plug } from 'lucide-react';

import { ConsolePanel, EmptyPanel, PageHeader } from '@/components/console/ui';
import { ServerDetailCard } from '@/features/mcp/server-detail-card';
import { ServerListPanel } from '@/features/mcp/server-list-panel';
import { ServerToolsCard } from '@/features/mcp/server-tools-card';
import { useMcpPageModel } from '@/features/mcp/use-mcp-page-model';

export function McpPage() {
  const model = useMcpPageModel();
  const { oauthStatus, oauthServerName, oauthErrorText, selected, status } =
    model;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="MCP"
        subtitle="自定义 MCP server(远程 Streamable HTTP / 沙箱 stdio)· 工具以 mcp.<server>.<tool> 注入任务,Token 与环境变量只写不读"
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 sm:px-8">
        {oauthStatus === 'success' ? (
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
        {oauthStatus === 'error' ? (
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
    </div>
  );
}
