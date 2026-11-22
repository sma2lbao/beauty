import { ArrowUp, Paperclip, Plug, Sparkles } from 'lucide-react';
import { useRef } from 'react';

import type { SendMode } from '@/lib/conversations-api';
import { ApiError } from '@/lib/http';

import { AttachmentRail } from './composer/attachment-rail';
import { ComposerSuggestions } from './composer/composer-suggestions';
import { useComposerContextSources } from './composer/context-providers';
import { refKey } from './composer/context-kinds';
import { RefRail, type RailCapability } from './composer/ref-rail';
import { SUGGESTIONS_ID, suggestionOptionId } from './composer/suggestion-ids';
import { useComposerMenu } from './composer/use-composer-menu';
import {
  SEND_MODE_LABELS,
  useComposerSources,
} from './composer/use-composer-sources';
import type { ComposerCommandId } from './composer/types';
import { MAX_ATTACHMENTS } from './use-chat-attachments';
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
  /** 本轮使用的模型 id;空串 = 默认模型(不指定,由网关路由)。 */
  selectedModel: string;
  onModelChange: (modelId: string) => void;
  /** 本轮显式引用的连接器 provider(多选,能力绑定)。 */
  selectedConnectors: string[];
  onToggleConnector: (provider: string) => void;
  connectorName: (provider: string) => string;
  /** @ 菜单里交付物/沙箱文件的数据源;新会话没有沙箱时为空。 */
  workspaceId: string | null;
  sandboxId: string | null;
  /** /new:清空草稿与暂存附件后跳到新会话。 */
  onNewConversation: () => void;
};

/**
 * 输入区:附件、模型与发送按钮。
 * 发送方式/技能/MCP 不再占底部工具栏——@ 与 / 菜单是它们的唯一入口,
 * 已选技能/MCP 由引用轨的能力芯片显示(芯片 × 即解绑)。
 * 只负责表单呈现,上传/发送逻辑由上层 hook 提供。
 * 新会话(还没有 id)也能选附件:hook 先暂存,发送时建会话再上传。
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
  selectedModel,
  onModelChange,
  selectedConnectors,
  onToggleConnector,
  connectorName,
  workspaceId,
  sandboxId,
  onNewConversation,
}: ChatComposerProps) {
  const { draft, attachments, sending, canSend, setDraft, submit, stop } =
    stream;
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const {
    skills,
    models,
    defaultModel,
    enabledMcpServers,
    mcpServerName,
    connectorProviders,
    messages,
    inFlight,
  } = queries;
  const {
    uploaded,
    staged,
    uploading,
    error: attachmentError,
    fileInputRef,
    pick,
  } = attachments;
  const attachmentCount = uploaded.length + staged.length;
  // 知识库文档/交付物/沙箱文件与连接器:@ 菜单的另外四组来源
  const contextSources = useComposerContextSources({
    workspaceId,
    sandboxId,
    messages,
  });
  const { items, commands, submenus } = useComposerSources({
    skills,
    mcpServers: enabledMcpServers,
    contextItems: contextSources.items,
    selectedConnectors,
    connectorProviders,
    models: models.map((model) => model.id),
    defaultModel,
    selectedSkill,
    selectedMcp,
    selectedModel,
    sendMode,
    sending,
    attachmentCount,
  });
  const menu = useComposerMenu({
    textareaRef,
    value: draft,
    onChange: setDraft,
    items,
    commands,
    submenus,
    onPickItem: (item) => {
      if (item.kind === 'skill') onSkillChange(item.id);
      else if (item.kind === 'mcp') onMcpChange(item.id);
      else if (item.kind === 'connector') onToggleConnector(item.id);
      else {
        // 内容引用:结构化 kind+id 随发送提交;标题只用于芯片展示
        stream.addContextRef({
          kind: item.kind,
          id: item.id,
          title: item.label,
        });
      }
    },
    onRunCommand: (command) => {
      if (command.id === 'new') onNewConversation();
      else if (command.id === 'stop') stop();
      else if (command.id === 'attach') fileInputRef.current?.click();
    },
    onPickSubmenuOption: (commandId: ComposerCommandId, value) => {
      if (commandId === 'model') onModelChange(value);
      else if (commandId === 'mode') onSendModeChange(value as SendMode);
      else if (commandId === 'skill') onSkillChange(value);
      else if (commandId === 'mcp') onMcpChange(value);
      else if (commandId === 'connector') {
        // 空串 = 不使用连接器:逐个 toggle 清空(保持页面状态单一真源)
        if (!value) selectedConnectors.forEach(onToggleConnector);
        else onToggleConnector(value);
      }
    },
  });
  // 配额拦截(429 quota.*)就地提示:P0-2 起对话直答也过配额闸,
  // 错误详情里的月用量/上限在「用量」页能看,这里只给一条可行动提示。
  const quotaBlocked =
    stream.visibleError instanceof ApiError &&
    stream.visibleError.code.startsWith('quota.');
  // 技能/MCP 的选中态:底部 select 已移除,引用轨的能力芯片是唯一可见处
  const skillLabel = selectedSkill
    ? skills.find((skill) => skill.id === selectedSkill)?.title || selectedSkill
    : '';
  const capabilityChips: RailCapability[] = [];
  if (selectedSkill) {
    capabilityChips.push({
      key: `skill:${selectedSkill}`,
      label: skillLabel,
      title: `技能 · ${skillLabel}`,
      removeLabel: `移除技能 ${skillLabel}`,
      Icon: Sparkles,
      onRemove: () => onSkillChange(''),
    });
  }
  if (selectedMcp) {
    const serverName = mcpServerName(selectedMcp);
    capabilityChips.push({
      key: `mcp:${selectedMcp}`,
      label: serverName,
      title: `MCP · ${serverName}`,
      removeLabel: `移除 MCP ${serverName}`,
      Icon: Plug,
      onRemove: () => onMcpChange(''),
    });
  }
  return (
    <div className="shrink-0 px-4 pb-5 sm:px-8 lg:px-12">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
        className="mx-auto w-full max-w-[820px] rounded-[10px] border border-ha-border-strong bg-ha-panel p-3 transition-colors focus-within:border-ha-accent/70"
      >
        <RefRail
          refs={stream.contextRefs}
          items={contextSources.items}
          selectedConnectors={selectedConnectors}
          connectorName={connectorName}
          capabilities={capabilityChips}
          onRemoveRef={(ref) => {
            // 正文里的可读 token 一起删掉;token 已被改掉时只删引用本身
            menu.removeToken(refKey(ref));
            stream.removeContextRef(ref);
          }}
          onRemoveConnector={onToggleConnector}
        />
        <AttachmentRail attachments={attachments} />
        {attachmentError ? (
          <p className="mb-2 text-[12px] text-ha-danger" role="alert">
            {attachmentError}
          </p>
        ) : null}
        {quotaBlocked ? (
          <p className="mb-2 text-[12px] text-ha-danger" role="alert">
            本月模型用量已达配额，发送会被拒绝；可在「用量」页查看配额明细。
          </p>
        ) : null}
        <label className="sr-only" htmlFor="chat-input">
          描述下一步任务
        </label>
        <div className="relative">
          <textarea
            ref={textareaRef}
            id="chat-input"
            rows={2}
            role="combobox"
            aria-haspopup="listbox"
            aria-expanded={menu.open}
            aria-controls={menu.open ? SUGGESTIONS_ID : undefined}
            aria-autocomplete="list"
            aria-activedescendant={
              menu.activeOption
                ? suggestionOptionId(menu.activeOption.key)
                : undefined
            }
            value={draft}
            onChange={menu.handleChange}
            onSelect={menu.handleSelect}
            onKeyDown={(event) => {
              // 菜单优先吃掉 ↑↓/Enter/Esc;没被处理才走发送快捷键。
              if (menu.handleKeyDown(event)) return;
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
                : '描述下一步任务,输入 @ 添加上下文、/ 选择命令…'
            }
            className="block w-full resize-none bg-transparent px-1 py-0.5 text-[13px] leading-6 text-ha-text-body outline-none placeholder:text-ha-dim"
          />
          <ComposerSuggestions
            open={menu.open}
            anchor={menu.anchor}
            groups={menu.groups}
            activeKey={menu.activeKey}
            label={menu.label}
            emptyLabel={menu.emptyLabel}
            onPick={menu.choose}
            onHover={menu.setActiveKey}
          />
        </div>
        <div className="mt-2 flex items-center justify-between gap-2">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              accept=".txt,.md,.markdown,.csv,.json,.pdf,.docx,.xlsx,.png,.jpg,.jpeg,.webp"
              onChange={(event) => void pick(event.target.files)}
            />
            <button
              type="button"
              aria-label="添加附件"
              title={
                attachmentCount >= MAX_ATTACHMENTS
                  ? `最多 ${MAX_ATTACHMENTS} 个附件`
                  : '添加附件(文本/Markdown/CSV/JSON/PDF/docx/xlsx/图片,单个 ≤10MB)'
              }
              disabled={uploading || attachmentCount >= MAX_ATTACHMENTS}
              onClick={() => fileInputRef.current?.click()}
              className="grid h-6 w-6 place-items-center rounded text-ha-muted transition-colors hover:bg-ha-panel-2 hover:text-ha-text disabled:opacity-40"
            >
              <Paperclip className="h-3.5 w-3.5" />
            </button>
            <label
              htmlFor="chat-model"
              className="font-mono text-[11px] text-ha-faint"
            >
              模型
            </label>
            <select
              id="chat-model"
              data-testid="chat-model-select"
              aria-label="选择模型"
              title="本次对话使用的模型;默认模型由平台按网关配置路由"
              value={selectedModel}
              onChange={(event) => onModelChange(event.target.value)}
              className="h-[22px] max-w-[150px] rounded-[5px] border border-transparent bg-ha-panel-2 px-1.5 font-mono text-[11px] font-medium text-ha-muted outline-none hover:text-ha-text-2 focus:border-ha-accent"
            >
              <option value="">
                默认模型{defaultModel ? `（${defaultModel}）` : ''}
              </option>
              {models.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.id}
                </option>
              ))}
              {/* 会话里存了已下线的模型(不在清单里):补兜底项,避免 value 失配 */}
              {selectedModel &&
              !models.some((model) => model.id === selectedModel) ? (
                <option value={selectedModel}>{selectedModel}</option>
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
              title={`发送(发送方式:${SEND_MODE_LABELS[sendMode]})`}
              disabled={!canSend}
              className="grid h-7 w-7 place-items-center rounded-md bg-ha-accent text-white transition-colors hover:bg-[#6b77e2] disabled:opacity-40"
            >
              <ArrowUp className="h-4 w-4" />
            </button>
          </div>
        </div>
      </form>
      <p className="mx-auto mt-2 max-w-[820px] text-center font-mono text-[11px] text-ha-faint">
        Enter 发送 · Shift + Enter 换行 · @ 添加上下文 · / 选择命令
      </p>
    </div>
  );
}
