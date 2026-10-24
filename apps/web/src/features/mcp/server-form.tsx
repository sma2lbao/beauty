/**
 * 新增 MCP server 的内联表单(HTTP / stdio 两套字段)。
 */
import { Plug } from 'lucide-react';

import {
  Button,
  Field,
  Input,
  Select,
  Switch,
  Tabs,
  Textarea,
} from '@beauty/design';

import type { MCPRuntime, MCPTransport } from '@/lib/mcp-api';

import { TRANSPORT_TABS } from './form';
import type { McpPageModel } from './use-mcp-page-model';

export function ServerForm({ model }: { model: McpPageModel }) {
  const {
    form,
    updateForm,
    httpUrlError,
    commandError,
    canCreate,
    formError,
    createMutation,
  } = model;

  return (
    <div className="mt-3 space-y-3 rounded-md border border-ha-border bg-ha-panel-2/60 p-3">
      <Field label="名称" htmlFor="mcp-name" required>
        <Input
          id="mcp-name"
          value={form.name}
          onChange={(event) => updateForm({ name: event.target.value })}
          placeholder="github"
        />
      </Field>
      <div className="space-y-1.5">
        <span className="text-[11px] font-medium text-ha-muted">
          传输方式
        </span>
        <Tabs
          size="sm"
          block
          value={form.transport}
          onValueChange={(value) =>
            updateForm({ transport: value as MCPTransport })
          }
          items={TRANSPORT_TABS}
        />
      </div>

      {form.transport === 'http' ? (
        <>
          <Field
            label="Server URL"
            htmlFor="mcp-url"
            required
            error={httpUrlError}
          >
            <Input
              id="mcp-url"
              value={form.url}
              onChange={(event) => updateForm({ url: event.target.value })}
              placeholder="https://…/mcp"
            />
          </Field>
          <Field
            label="Token(可选)"
            htmlFor="mcp-token"
            hint="保存在服务端,读端只回尾号"
          >
            <Input
              id="mcp-token"
              type="password"
              value={form.token}
              onChange={(event) => updateForm({ token: event.target.value })}
              placeholder="保存后只显示尾号"
            />
          </Field>
        </>
      ) : (
        <>
          <Field
            label="命令"
            htmlFor="mcp-command"
            required
            hint="如 npx、uvx、python"
            error={commandError}
          >
            <Input
              id="mcp-command"
              value={form.command}
              onChange={(event) => updateForm({ command: event.target.value })}
              placeholder={form.runtime === 'node' ? 'npx' : 'uvx'}
            />
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="运行时" htmlFor="mcp-runtime">
              <Select
                id="mcp-runtime"
                value={form.runtime}
                onChange={(event) =>
                  updateForm({
                    runtime: event.target.value as MCPRuntime,
                  })
                }
              >
                <option value="python">Python</option>
                <option value="node">Node</option>
              </Select>
            </Field>
            <Field label="镜像(可选)" htmlFor="mcp-image">
              <Input
                id="mcp-image"
                value={form.image}
                onChange={(event) => updateForm({ image: event.target.value })}
                placeholder={
                  form.runtime === 'node'
                    ? 'node:22-slim'
                    : 'python:3.12-slim'
                }
              />
            </Field>
          </div>
          <Field
            label="参数(一行一个)"
            htmlFor="mcp-args"
            hint="如 -y、@modelcontextprotocol/server-filesystem"
          >
            <Textarea
              id="mcp-args"
              className="font-mono"
              rows={3}
              value={form.argsText}
              onChange={(event) =>
                updateForm({ argsText: event.target.value })
              }
              placeholder={'-y\n@modelcontextprotocol/server-filesystem\n/tmp'}
            />
          </Field>
          <Field
            label="环境变量(KEY=VALUE,一行一个)"
            htmlFor="mcp-env"
            hint="只写不读;列表只显示变量名,不回显值"
          >
            <Textarea
              id="mcp-env"
              className="font-mono"
              rows={3}
              value={form.envText}
              onChange={(event) => updateForm({ envText: event.target.value })}
              placeholder={'GITHUB_TOKEN=ghp_…\nLOG_LEVEL=debug'}
            />
          </Field>
        </>
      )}

      <div className="flex items-start justify-between gap-2 rounded-md border border-ha-border bg-ha-panel px-2.5 py-2">
        <div className="min-w-0">
          <p className="text-[11px] font-medium text-ha-muted">
            免确认执行
          </p>
          <p className="text-[10px] leading-4 text-ha-dim">
            信任该 server:工具在会话里可直接执行,任务里也不再逐次确认
          </p>
        </div>
        <Switch
          aria-label="免确认执行(注册)"
          checked={form.autoApprove}
          onCheckedChange={(checked) => updateForm({ autoApprove: checked })}
        />
      </div>
      {formError ? (
        <p className="text-[12px] text-ha-danger">{formError}</p>
      ) : null}
      <Button
        size="sm"
        className="w-full"
        disabled={!canCreate || createMutation.isPending}
        onClick={() => createMutation.mutate()}
      >
        <Plug className="mr-1 h-3.5 w-3.5" />
        {createMutation.isPending ? '保存中…' : '注册 MCP server'}
      </Button>
    </div>
  );
}
