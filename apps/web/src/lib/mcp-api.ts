/**
 * MCP Client 数据层:同源 /api/v1/mcp/servers(BFF → orchestrator)。
 */
import { requestJson } from '@/lib/http';

export type MCPTool = {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
};

export type MCPServer = {
  id: string;
  name: string;
  url: string;
  enabled: boolean;
  has_token: boolean;
  token_hint: string | null;
  tools_allowlist: string[];
  tools: MCPTool[];
  timeout_seconds: number;
  last_status: string | null;
  last_checked_at: string | null;
  last_error: string | null;
  created_at: string;
};

function request<T>(path: string, init?: RequestInit): Promise<T> {
  return requestJson<T>('', '/api/v1/mcp/servers' + path, init);
}

export const mcpApi = {
  list: () => request<MCPServer[]>(''),
  create: (input: {
    name: string;
    url: string;
    token?: string;
    timeout_seconds?: number;
  }) =>
    request<MCPServer>('', {
      method: 'POST',
      body: JSON.stringify(input),
    }),
  update: (id: string, patch: Partial<Pick<MCPServer, 'enabled' | 'url'>>) =>
    request<MCPServer>('/' + id, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),
  remove: (id: string) => request<void>('/' + id, { method: 'DELETE' }),
  test: (id: string) =>
    request<{ ok: boolean; detail: string; tools: MCPTool[] }>(
      '/' + id + '/test',
      {
        method: 'POST',
      },
    ),
};
