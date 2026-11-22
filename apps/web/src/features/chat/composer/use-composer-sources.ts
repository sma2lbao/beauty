import {
  Cpu,
  Paperclip,
  Package,
  Plug,
  Plus,
  Sparkles,
  Split,
  Square,
} from 'lucide-react';
import { useMemo } from 'react';

import type { SendMode } from '@/lib/conversations-api';
import type { ConnectorProvider } from '@/lib/connector-api';
import type { MCPServer } from '@/lib/mcp-api';
import type { SkillManifest } from '@/lib/skills-api';

import { MAX_ATTACHMENTS } from '../use-chat-attachments';
import { buildMentionItems } from './context-sources';
import type {
  ComposerCommand,
  ComposerCommandId,
  ComposerItem,
  ComposerSubmenu,
} from './types';

type SourceInput = {
  skills: SkillManifest[];
  mcpServers: MCPServer[];
  contextItems: ComposerItem[];
  selectedConnectors: string[];
  connectorProviders: ConnectorProvider[];
  models: string[];
  defaultModel: string | null;
  selectedSkill: string;
  selectedMcp: string;
  selectedModel: string;
  sendMode: SendMode;
  sending: boolean;
  attachmentCount: number;
};

/** 输入区菜单的数据源:能力项、命令清单、命令二级菜单。 */
export function useComposerSources({
  skills,
  mcpServers,
  contextItems,
  selectedConnectors,
  connectorProviders,
  models,
  defaultModel,
  selectedSkill,
  selectedMcp,
  selectedModel,
  sendMode,
  sending,
  attachmentCount,
}: SourceInput) {
  const items = useMemo(
    () =>
      buildMentionItems(
        skills,
        mcpServers,
        contextItems,
        selectedSkill,
        selectedMcp,
        selectedConnectors,
      ),
    [
      skills,
      mcpServers,
      contextItems,
      selectedSkill,
      selectedMcp,
      selectedConnectors,
    ],
  );

  const commands = useMemo<ComposerCommand[]>(() => {
    const attachmentsFull = attachmentCount >= MAX_ATTACHMENTS;
    return [
      {
        id: 'new',
        label: '/new',
        description: '新建会话',
        icon: Plus,
        kind: 'action',
        disabled: sending,
        disabledReason: '当前回复未结束,先停止生成',
      },
      {
        id: 'stop',
        label: '/stop',
        description: '停止生成',
        icon: Square,
        kind: 'action',
        disabled: !sending,
        disabledReason: '当前没有正在生成的回复',
      },
      {
        id: 'attach',
        label: '/attach',
        description: '添加附件',
        icon: Paperclip,
        kind: 'action',
        disabled: sending || attachmentsFull,
        disabledReason: sending
          ? '发送中不能添加附件'
          : `附件已达上限(${MAX_ATTACHMENTS})`,
      },
      {
        id: 'model',
        label: '/model',
        description: '切换本轮模型',
        icon: Cpu,
        kind: 'capability',
        disabled: models.length === 0,
        disabledReason: '暂无可用模型',
      },
      {
        id: 'mode',
        label: '/mode',
        description: '切换发送方式',
        icon: Split,
        kind: 'capability',
      },
      {
        id: 'skill',
        label: '/skill',
        description: '选择技能',
        icon: Sparkles,
        kind: 'capability',
        disabled: skills.length === 0 && !selectedSkill,
        disabledReason: '暂无可选技能',
      },
      {
        id: 'mcp',
        label: '/mcp',
        description: '选择 MCP 服务',
        icon: Plug,
        kind: 'capability',
        disabled: mcpServers.length === 0 && !selectedMcp,
        disabledReason: '暂无启用的 MCP 服务',
      },
      {
        id: 'connector',
        label: '/connector',
        description: sending
          ? '选择连接器(对新任务生效)'
          : '选择连接器(可多选)',
        icon: Package,
        kind: 'capability',
        disabled:
          connectorProviders.length === 0 && selectedConnectors.length === 0,
        disabledReason: '暂无已连接的连接器',
      },
    ];
  }, [
    attachmentCount,
    connectorProviders.length,
    mcpServers.length,
    models.length,
    selectedConnectors.length,
    selectedMcp,
    selectedSkill,
    sending,
    skills.length,
  ]);

  const submenus = useMemo<
    Partial<Record<ComposerCommandId, ComposerSubmenu>>
  >(() => {
    const modeOptions: {
      value: SendMode;
      label: string;
      description: string;
    }[] = [
      {
        value: 'auto',
        label: '自动分流',
        description: '由服务端判断对话或任务',
      },
      { value: 'chat', label: '直接回答', description: '不建任务,直接回复' },
      { value: 'task', label: '作为任务', description: '创建任务并规划执行' },
    ];
    return {
      model: {
        commandId: 'model',
        title: '选择模型',
        options: [
          {
            key: 'model:',
            label: '默认模型',
            description: defaultModel
              ? `由网关路由(${defaultModel})`
              : '由网关路由',
            icon: Cpu,
            value: '',
            current: !selectedModel,
          },
          ...models.map((model) => ({
            key: `model:${model}`,
            label: model,
            icon: Cpu,
            value: model,
            current: selectedModel === model,
          })),
        ],
      },
      mode: {
        commandId: 'mode',
        title: '选择发送方式',
        options: modeOptions.map((mode) => ({
          key: `mode:${mode.value}`,
          label: mode.label,
          description: mode.description,
          icon: Split,
          value: mode.value,
          current: sendMode === mode.value,
        })),
      },
      skill: {
        commandId: 'skill',
        title: '选择技能',
        options: [
          {
            key: 'skill:',
            label: '不使用技能',
            icon: Sparkles,
            value: '',
            current: !selectedSkill,
          },
          ...skills.map((skill) => ({
            key: `skill:${skill.id}`,
            label: skill.title || skill.id,
            description: skill.description,
            icon: Sparkles,
            value: skill.id,
            current: selectedSkill === skill.id,
          })),
        ],
      },
      mcp: {
        commandId: 'mcp',
        title: '选择 MCP 服务',
        options: [
          {
            key: 'mcp:',
            label: '不使用 MCP',
            icon: Plug,
            value: '',
            current: !selectedMcp,
          },
          ...mcpServers.map((server) => ({
            key: `mcp:${server.id}`,
            label: server.name,
            description: server.url || server.command || undefined,
            icon: Plug,
            value: server.id,
            current: selectedMcp === server.id,
          })),
        ],
      },
      connector: {
        commandId: 'connector',
        title: '选择连接器(可多选)',
        sticky: true,
        options: [
          {
            key: 'connector:',
            label: '不使用连接器',
            description: '清空已选连接器',
            icon: Package,
            value: '',
            current: selectedConnectors.length === 0,
          },
          ...connectorProviders.map((provider) => ({
            key: `connector:${provider.provider}`,
            label: provider.display_name || provider.provider,
            description: `${provider.tools.length} 个工具`,
            icon: Package,
            value: provider.provider,
            current: selectedConnectors.includes(provider.provider),
          })),
        ],
      },
    };
  }, [
    connectorProviders,
    defaultModel,
    mcpServers,
    models,
    selectedConnectors,
    selectedMcp,
    selectedModel,
    selectedSkill,
    sendMode,
    skills,
  ]);

  return { items, commands, submenus };
}
