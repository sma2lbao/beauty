/**
 * 右列:server 暴露的工具清单。
 */
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@beauty/design';

import { Mono } from '@/components/console/ui';

import type { McpPageModel } from './use-mcp-page-model';

export function ServerToolsCard({ model }: { model: McpPageModel }) {
  const { selected } = model;
  if (!selected) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>工具</CardTitle>
        <CardDescription>
          以 mcp.&lt;server&gt;.&lt;tool&gt;
          注入任务;开了「免确认执行」的工具在 会话里也能直接调用,其余仍需人工确认;点「测试/刷新」重新拉取。
        </CardDescription>
      </CardHeader>
      <CardContent>
        {selected.tools.length ? (
          <ul className="space-y-1.5">
            {selected.tools.map((tool) => {
              const properties = tool.input_schema?.properties;
              const paramCount =
                properties && typeof properties === 'object'
                  ? Object.keys(properties).length
                  : 0;
              return (
                <li
                  key={tool.name}
                  className="rounded-md border border-ha-border bg-ha-panel-2/60 px-2.5 py-2"
                >
                  <div className="flex items-center justify-between gap-2">
                    <Mono className="truncate text-ha-text-2">
                      {tool.name}
                    </Mono>
                    <span className="shrink-0 font-mono text-[10px] text-ha-faint">
                      {paramCount} 个参数
                    </span>
                  </div>
                  {tool.description ? (
                    <p className="mt-0.5 text-[11px] leading-4 text-ha-dim">
                      {tool.description}
                    </p>
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="px-1 text-[12px] leading-5 text-ha-dim">
            还没有拉取到工具。点右上角「测试/刷新」连接一次 server。
          </p>
        )}
      </CardContent>
    </Card>
  );
}
