/**
 * 会话消息气泡:用户消息(时间/MCP chip/附件)与 Agent 回复(头像 + 元信息 + Markdown),
 * 以及完成态消息(按 run 补充已执行步数/运行摘要)。
 * 附件下载:点击即流式取回 blob 触发浏览器下载,失败静默(可重点)。
 */
import { FileText, Plug, Sparkles } from 'lucide-react';
import type { ReactNode } from 'react';

import { Markdown } from '@/components/console/markdown';
import type { Attachment, ChatMessage } from '@/lib/conversations-api';
import { conversationsApi } from '@/lib/conversations-api';
import { clockTime, runMetaLine } from '@/lib/presentation';
import type { Run } from '@/lib/runs-api';

export function UserBlock({
  text,
  createdAt,
  attachments = [],
  mcpServers = [],
}: {
  text: string;
  createdAt?: string;
  attachments?: Attachment[];
  mcpServers?: { id: string; name: string }[];
}) {
  return (
    <article>
      <header className="flex items-center gap-2">
        <span className="text-[11px] font-semibold text-ha-dim">你</span>
        {createdAt ? (
          <span className="font-mono text-[11px] text-ha-faint">
            {clockTime(createdAt)}
          </span>
        ) : null}
      </header>
      <p className="mt-2 whitespace-pre-wrap text-[14px] leading-[23px] text-ha-text-body">
        {text}
      </p>
      {mcpServers.length ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {mcpServers.map((server) => (
            <span
              key={server.id}
              className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-ha-border bg-ha-panel-2 px-2 py-0.5 font-mono text-[11px] text-ha-text-2"
            >
              <Plug className="h-3 w-3 shrink-0 text-ha-muted" />
              <span className="truncate">MCP · {server.name}</span>
            </span>
          ))}
        </div>
      ) : null}
      {attachments.length ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {attachments.map((attachment) => (
            <button
              key={attachment.id}
              type="button"
              onClick={() => void downloadAttachmentFile(attachment)}
              className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-ha-border bg-ha-panel px-2 py-1 font-mono text-[11px] text-ha-text-2 transition-colors hover:border-ha-border-strong hover:text-ha-text"
            >
              <FileText className="h-3 w-3 shrink-0 text-ha-muted" />
              <span className="truncate">{attachment.filename}</span>
              <span className="shrink-0 text-ha-faint">
                {formatAttachmentSize(attachment.size_bytes)}
              </span>
            </button>
          ))}
        </div>
      ) : null}
    </article>
  );
}

function formatAttachmentSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}

async function downloadAttachmentFile(attachment: Attachment): Promise<void> {
  try {
    const blob = await conversationsApi.downloadAttachment(attachment.id);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = attachment.filename;
    anchor.click();
    URL.revokeObjectURL(url);
  } catch {
    // 下载失败时静默;下次点击可重试
  }
}

export function AgentBlock({
  meta,
  live = false,
  badge,
  children,
}: {
  meta: string;
  live?: boolean;
  /** 头部附加标签(如任务阶段),与 meta 同行展示。 */
  badge?: ReactNode;
  children: string;
}) {
  return (
    <article className="flex flex-col gap-3">
      <header className="flex items-center gap-2">
        <span className="grid h-5 w-5 shrink-0 place-items-center rounded-md bg-[#1f2333]">
          <Sparkles className="h-3 w-3 text-ha-accent-soft" />
        </span>
        <span className="text-[13px] font-semibold text-ha-text">
          beauty moon Agent
        </span>
        <span className="font-mono text-[11px] text-ha-dim">{meta}</span>
        {badge}
      </header>
      <Markdown content={children} streaming={live} />
    </article>
  );
}

export function MessageBlock({
  message,
  run,
  steps,
  attachments,
  mcpCatalog = [],
}: {
  message: ChatMessage;
  run?: Run;
  steps?: number;
  attachments?: Attachment[];
  mcpCatalog?: { id: string; name: string }[];
}) {
  if (message.role === 'user') {
    // 消息只存 id;名字用当前 server 列表映射,列表里没有(已删除)则退回 id
    const mcpServers = (message.mcp_server_ids ?? []).map((id) => ({
      id,
      name: mcpCatalog.find((server) => server.id === id)?.name ?? id,
    }));
    return (
      <UserBlock
        text={message.content}
        createdAt={message.created_at}
        attachments={attachments}
        mcpServers={mcpServers}
      />
    );
  }
  const meta =
    run && steps
      ? `已执行 ${steps} 步`
      : run
        ? runMetaLine(run)
        : 'beauty moon Agent';
  return <AgentBlock meta={meta}>{message.content}</AgentBlock>;
}
