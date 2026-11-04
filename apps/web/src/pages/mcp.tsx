/**
 * MCP 页(/mcp 直链):用户自定义 MCP server 的注册表与连接定义。
 *
 * 入口已并入「连接器」页(该页内嵌同一个管理区),本页保留两用:旧书签直达,
 * 以及 OAuth state 验签失败时后端按默认 /mcp 回跳的兜底落点。
 *
 * 定义式交互:左侧列表(右上 + 展开定义表单)、右侧详情;
 * - http:远程 Streamable HTTP server(URL/Token);
 * - stdio:本地进程(命令/参数/环境变量/运行时/镜像),由 orchestrator 沙箱拉起。
 * Token 与环境变量只写不读:读端只返回尾号与变量名,提交成功后清空敏感输入。
 * 「免确认执行」是每个 server 的信任开关:会话没有审批流(§6.7),只有开了它的
 * 工具才能在会话里直接被调用;关闭时工具仍可在「任务」路径按确认流程使用。
 */
import { PageHeader } from '@/components/console/ui';
import { McpManagementSection } from '@/features/mcp/mcp-management-section';
import { useMcpPageModel } from '@/features/mcp/use-mcp-page-model';

export function McpPage() {
  const model = useMcpPageModel();

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title="MCP"
        subtitle="自定义 MCP server(远程 Streamable HTTP / 沙箱 stdio)· 工具以 mcp.<server>.<tool> 注入任务,Token 与环境变量只写不读"
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 sm:px-8">
        <McpManagementSection model={model} />
      </div>
    </div>
  );
}
