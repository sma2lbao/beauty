/**
 * 能力目录:MCP server 与连接器 provider。
 *
 * 输入区的 @ 菜单、历史消息芯片、顶栏都用同一份缓存;
 * 失败不阻塞对话(退化成空清单),所以不进 queryError。
 */
import { useQuery } from '@tanstack/react-query';

import { connectorApi } from '@/lib/connector-api';
import { mcpApi } from '@/lib/mcp-api';
import { queryKeys } from '@/lib/query-keys';

export function useChatCapabilities() {
  const mcpQuery = useQuery({
    queryKey: queryKeys.mcpServers,
    queryFn: mcpApi.list,
    retry: false,
  });
  const connectorsQuery = useQuery({
    queryKey: queryKeys.connectorProviders,
    queryFn: connectorApi.listProviders,
    retry: false,
    staleTime: 60_000,
  });
  // 选择器只列启用的 server;历史消息/URL 里可能引用已停用的 id,用全量列表展示名字
  const mcpServers = mcpQuery.data ?? [];
  const enabledMcpServers = mcpServers.filter((server) => server.enabled);
  const mcpServerName = (id: string) =>
    mcpServers.find((server) => server.id === id)?.name ?? id;
  const mcpCatalog = mcpServers.map((server) => ({
    id: server.id,
    name: server.name,
  }));
  const connectorProviders = connectorsQuery.data ?? [];
  const connectorName = (provider: string) =>
    connectorProviders.find((item) => item.provider === provider)
      ?.display_name || provider;
  return {
    mcpServers,
    enabledMcpServers,
    mcpServerName,
    mcpCatalog,
    connectorProviders,
    connectorName,
  };
}
