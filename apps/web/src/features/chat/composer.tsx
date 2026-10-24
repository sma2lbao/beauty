import { ArrowUp, Paperclip, X } from 'lucide-react';

import type { SendMode } from '@/lib/conversations-api';

import type { ChatQueries } from './use-chat-queries';
import type { ChatStream } from './use-chat-stream';

export type ChatComposerProps = {
  queries: ChatQueries;
  stream: ChatStream;
  sendMode: SendMode;
  onSendModeChange: (mode: SendMode) => void;
  selectedSkill: string;
  onSkillChange: (skillId: string) => void;
  selectedMcp: string;
  onMcpChange: (serverId: string) => void;
  activeConversationId: string | null;
};

/**
 * 输入区:附件、发送方式/技能/MCP 选择与发送按钮。
 * 只负责表单呈现,上传/发送逻辑由上层 hook 提供。
 */
export function ChatComposer({
  queries,
  stream,
  sendMode,
  onSendModeChange,
  selectedSkill,
  onSkillChange,
  selectedMcp,
  onMcpChange,
  activeConversationId,
}: ChatComposerProps) {
  const {
    draft,
    draftAttachments: attachments,
    fileInputRef,
    attachmentError,
    uploading,
    sending,
    canSend,
    setDraft,
    removeAttachment,
    uploadAttachments,
    submit,
    stop,
  } = stream;
  const { skills, enabledMcpServers, mcpServerName, inFlight } = queries;
  return (
    <div className="shrink-0 px-4 pb-5 sm:px-8 lg:px-12">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
        className="mx-auto w-full max-w-[820px] rounded-[10px] border border-ha-border-strong bg-ha-panel p-3 transition-colors focus-within:border-ha-accent/70"
      >
        {attachments.length ? (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {attachments.map((attachment) => (
              <span
                key={attachment.id}
                className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-ha-border bg-ha-panel-2 px-2 py-1 font-mono text-[11px] text-ha-text-2"
              >
                <Paperclip className="h-3 w-3 shrink-0 text-ha-muted" />
                <span className="truncate">{attachment.filename}</span>
                <button
                  type="button"
                  aria-label={`移除附件 ${attachment.filename}`}
                  onClick={() => removeAttachment(attachment.id)}
                  className="text-ha-dim hover:text-ha-text"
                >
                  <X className="h-3 w-3" />
                </button>
              </span>
            ))}
          </div>
        ) : null}
        {attachmentError ? (
          <p className="mb-2 text-[12px] text-ha-danger" role="alert">
            {attachmentError}
          </p>
        ) : null}
        <label className="sr-only" htmlFor="chat-input">
          描述下一步任务
        </label>
        <textarea
          id="chat-input"
          rows={2}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (
              event.key === 'Enter' &&
              !event.shiftKey &&
              !event.nativeEvent.isComposing
            ) {
              event.preventDefault();
              submit();
            }
          }}
          placeholder={
            inFlight
              ? '补充一句,会并入当前任务的追加指令…'
              : '描述下一步任务,或直接说说你想做什么…'
          }
          className="block w-full resize-none bg-transparent px-1 py-0.5 text-[13px] leading-6 text-ha-text-body outline-none placeholder:text-ha-dim"
        />
        <div className="mt-2 flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              accept=".txt,.md,.markdown,.csv,.json,.pdf,.docx,.xlsx,.png,.jpg,.jpeg,.webp"
              onChange={(event) => void uploadAttachments(event.target.files)}
            />
            <button
              type="button"
              aria-label="添加附件"
              title={
                activeConversationId
                  ? '添加附件(文本/Markdown/CSV/JSON/PDF/docx/xlsx/图片,单个 ≤10MB)'
                  : '先发一条消息创建会话,再添加附件'
              }
              disabled={
                !activeConversationId || uploading || attachments.length >= 5
              }
              onClick={() => fileInputRef.current?.click()}
              className="grid h-6 w-6 place-items-center rounded text-ha-muted transition-colors hover:bg-ha-panel-2 hover:text-ha-text disabled:opacity-40"
            >
              <Paperclip className="h-3.5 w-3.5" />
            </button>
            <label
              htmlFor="send-mode"
              className="font-mono text-[11px] text-ha-faint"
            >
              发送方式
            </label>
            <select
              id="send-mode"
              value={sendMode}
              onChange={(event) =>
                onSendModeChange(event.target.value as SendMode)
              }
              className="h-[22px] rounded-[5px] border border-transparent bg-ha-panel-2 px-1.5 font-mono text-[11px] font-medium text-ha-muted outline-none hover:text-ha-text-2 focus:border-ha-accent"
            >
              <option value="auto">自动分流</option>
              <option value="chat">直接回答</option>
              <option value="task">作为任务</option>
            </select>
            <label
              htmlFor="send-skill"
              className="font-mono text-[11px] text-ha-faint"
            >
              技能
            </label>
            <select
              id="send-skill"
              aria-label="选择技能"
              value={selectedSkill}
              onChange={(event) => onSkillChange(event.target.value)}
              className="h-[22px] max-w-[150px] rounded-[5px] border border-transparent bg-ha-panel-2 px-1.5 font-mono text-[11px] font-medium text-ha-muted outline-none hover:text-ha-text-2 focus:border-ha-accent"
            >
              <option value="">不使用技能</option>
              {skills.map((skill) => (
                <option key={skill.id} value={skill.id}>
                  {skill.title}
                </option>
              ))}
            </select>
            <label
              htmlFor="send-mcp"
              className="font-mono text-[11px] text-ha-faint"
            >
              MCP
            </label>
            <select
              id="send-mcp"
              aria-label="选择 MCP server"
              value={selectedMcp}
              onChange={(event) => onMcpChange(event.target.value)}
              className="h-[22px] max-w-[150px] rounded-[5px] border border-transparent bg-ha-panel-2 px-1.5 font-mono text-[11px] font-medium text-ha-muted outline-none hover:text-ha-text-2 focus:border-ha-accent"
            >
              <option value="">不使用 MCP</option>
              {enabledMcpServers.map((server) => (
                <option key={server.id} value={server.id}>
                  {server.name}
                </option>
              ))}
              {/* URL/草稿引用了已停用或已删除的 server:补一个兜底项,避免 value 失配 */}
              {selectedMcp &&
              !enabledMcpServers.some((server) => server.id === selectedMcp) ? (
                <option value={selectedMcp}>
                  {mcpServerName(selectedMcp)}
                </option>
              ) : null}
            </select>
            {inFlight ? (
              <span className="hidden truncate font-mono text-[11px] text-ha-info sm:inline">
                任务进行中 · 发送会并入追加指令
              </span>
            ) : null}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {sending ? (
              <button
                type="button"
                onClick={stop}
                className="font-mono text-[11px] text-ha-dim hover:text-ha-text-2"
              >
                停止生成
              </button>
            ) : null}
            <button
              type="submit"
              aria-label="发送"
              disabled={!canSend}
              className="grid h-7 w-7 place-items-center rounded-md bg-ha-accent text-white transition-colors hover:bg-[#6b77e2] disabled:opacity-40"
            >
              <ArrowUp className="h-4 w-4" />
            </button>
          </div>
        </div>
      </form>
      <p className="mx-auto mt-2 max-w-[820px] text-center font-mono text-[11px] text-ha-faint">
        Enter 发送 · Shift + Enter 换行
      </p>
    </div>
  );
}
