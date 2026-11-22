/**
 * 附件芯片轨:已上传的实心边框,新会话暂存的虚线边框(发送时才上传)。
 */
import { Paperclip, X } from 'lucide-react';

import type { ChatAttachments } from '../use-chat-attachments';

export function AttachmentRail({
  attachments,
}: {
  attachments: ChatAttachments;
}) {
  const { uploaded, staged, removeUploaded, removeStaged } = attachments;
  if (!uploaded.length && !staged.length) return null;
  return (
    <div className="mb-2 flex flex-wrap gap-1.5">
      {uploaded.map((attachment) => (
        <span
          key={attachment.id}
          className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-ha-border bg-ha-panel-2 px-2 py-1 font-mono text-[11px] text-ha-text-2"
        >
          <Paperclip className="h-3 w-3 shrink-0 text-ha-muted" />
          <span className="truncate">{attachment.filename}</span>
          <button
            type="button"
            aria-label={`移除附件 ${attachment.filename}`}
            onClick={() => removeUploaded(attachment.id)}
            className="text-ha-dim hover:text-ha-text"
          >
            <X className="h-3 w-3" />
          </button>
        </span>
      ))}
      {staged.map((file, index) => (
        <span
          key={`staged-${index}-${file.name}`}
          title="发送首条消息时上传"
          className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-dashed border-ha-border bg-ha-panel-2 px-2 py-1 font-mono text-[11px] text-ha-text-2"
        >
          <Paperclip className="h-3 w-3 shrink-0 text-ha-muted" />
          <span className="truncate">{file.name}</span>
          <button
            type="button"
            aria-label={`移除附件 ${file.name}`}
            onClick={() => removeStaged(index)}
            className="text-ha-dim hover:text-ha-text"
          >
            <X className="h-3 w-3" />
          </button>
        </span>
      ))}
    </div>
  );
}
