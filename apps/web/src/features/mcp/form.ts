/**
 * MCP 注册表单的纯数据:默认值、类型与「表单 → 创建入参」转换。
 */
import {
  isHttpUrl,
  isStdioCommand,
  parseArgsText,
  parseEnvText,
  type MCPServerCreateInput,
  type MCPRuntime,
  type MCPTransport,
} from '@/lib/mcp-api';

export const TRANSPORT_TABS = [
  { value: 'http', label: 'HTTP(远程)' },
  { value: 'stdio', label: 'stdio(本地进程)' },
];

export type FormState = {
  name: string;
  transport: MCPTransport;
  url: string;
  token: string;
  command: string;
  argsText: string;
  envText: string;
  runtime: MCPRuntime;
  image: string;
  autoApprove: boolean;
};

export const EMPTY_FORM: FormState = {
  name: '',
  transport: 'http',
  url: 'http://127.0.0.1:8010/mcp',
  token: '',
  command: '',
  argsText: '',
  envText: '',
  runtime: 'python',
  image: '',
  autoApprove: false,
};

export function buildCreateInput(form: FormState): MCPServerCreateInput {
  const trimmedName = form.name.trim();
  if (form.transport === 'stdio') {
    const trimmedImage = form.image.trim();
    return {
      transport: 'stdio',
      name: trimmedName,
      command: form.command.trim(),
      args: parseArgsText(form.argsText),
      env: parseEnvText(form.envText),
      runtime: form.runtime,
      auto_approve: form.autoApprove,
      ...(trimmedImage ? { image: trimmedImage } : {}),
    };
  }
  const trimmedToken = form.token.trim();
  return {
    transport: 'http',
    name: trimmedName,
    url: form.url.trim(),
    auto_approve: form.autoApprove,
    ...(trimmedToken ? { token: trimmedToken } : {}),
  };
}

export function httpUrlErrorFor(form: FormState): string | undefined {
  return form.transport === 'http' && form.url.trim() && !isHttpUrl(form.url)
    ? '需以 http:// 或 https:// 开头'
    : undefined;
}

export function commandErrorFor(form: FormState): string | undefined {
  return form.transport === 'stdio' &&
    form.command.trim() &&
    !isStdioCommand(form.command)
    ? '只允许字母数字与 . _ / -,参数请填到下方参数框'
    : undefined;
}

export function canCreateFor(form: FormState): boolean {
  return form.transport === 'http'
    ? Boolean(form.name.trim() && isHttpUrl(form.url))
    : Boolean(form.name.trim() && isStdioCommand(form.command));
}
