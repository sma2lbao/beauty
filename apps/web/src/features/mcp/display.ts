/**
 * MCP server 的展示型派生:列表副标题、状态标签、测试结果文案。
 * 纯函数,便于单测与在列表/详情两处复用。
 */
import type { MCPServer } from '@/lib/mcp-api';
import type { Tone } from '@/lib/presentation';

/** 列表副标题:http 给地址,stdio 给「运行时 命令 参数」,一眼看出连的是什么。 */
export function describeEndpoint(server: MCPServer): string {
  if (server.transport === 'stdio') {
    return [server.runtime, server.command, ...(server.args ?? [])]
      .filter(Boolean)
      .join(' ');
  }
  return server.url;
}

/** 状态标签:停用优先于连接状态,免得上一次测试结果盖住「已停用」。 */
export function serverStatus(server: MCPServer): { tone: Tone; label: string } {
  if (!server.enabled) return { tone: 'neutral', label: '已停用' };
  if (server.last_status === 'ok') return { tone: 'success', label: '已连接' };
  // 401 不是"连不上":服务端明确要求授权,提示用户去重新授权/换 token
  if (server.last_status === 'auth_required')
    return { tone: 'warning', label: '需要授权' };
  if (server.last_status === 'error')
    return { tone: 'danger', label: '连接失败' };
  return { tone: 'info', label: '待测试' };
}

/** 测试结果文案:401 单独提示"需要授权",不与普通连接失败混在一起。 */
export function testResultText(result: {
  ok: boolean;
  authRequired: boolean;
}): string {
  if (result.authRequired) return '需要授权';
  return result.ok ? '测试通过' : '测试失败';
}

export function testResultClass(result: {
  ok: boolean;
  authRequired: boolean;
}): string {
  if (result.authRequired) return 'text-[12px] text-ha-warning';
  return result.ok
    ? 'text-[12px] text-ha-success'
    : 'text-[12px] text-ha-danger';
}
