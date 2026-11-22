import { Plug, Sparkles } from 'lucide-react';

import type { MCPServer } from '@/lib/mcp-api';
import type { SkillManifest } from '@/lib/skills-api';

import { truncateTitle } from './mention-ranges';
import type { ComposerGroup, ComposerItem } from './types';

function matches(query: string, ...fields: (string | undefined)[]) {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return fields.some((field) => field?.toLowerCase().includes(needle));
}

/**
 * 把技能与 MCP server 归一成 @ 菜单项。
 * 选中的能力只影响页面状态;正文 token 是可读回声,不是提交依据。
 */
export function buildMentionItems(
  skills: SkillManifest[],
  servers: MCPServer[],
  contextItems: ComposerItem[],
  selectedSkill: string,
  selectedMcp: string,
  selectedConnectors: string[],
): ComposerItem[] {
  const skillItems = skills.map((skill) => ({
    key: `skill:${skill.id}`,
    kind: 'skill' as const,
    id: skill.id,
    label: skill.title || skill.id,
    token: `@${truncateTitle(skill.title || skill.id)}`,
    description: skill.description,
    icon: Sparkles,
    current: skill.id === selectedSkill,
  }));
  const mcpItems = servers.map((server) => ({
    key: `mcp:${server.id}`,
    kind: 'mcp' as const,
    id: server.id,
    label: server.name,
    token: `@${truncateTitle(server.name)}`,
    description: server.url || server.command || undefined,
    icon: Plug,
    current: server.id === selectedMcp,
  }));
  const connectorItems = contextItems
    .filter((item) => item.kind === 'connector')
    .map((item) => ({
      ...item,
      current: selectedConnectors.includes(item.id),
    }));
  return [
    ...skillItems,
    ...mcpItems,
    ...connectorItems,
    ...contextItems.filter((item) => item.kind !== 'connector'),
  ];
}

/**
 * 过滤并分组:前缀命中优先,其次最近使用,其余保持数据源顺序。
 * 最近使用只影响组内顺序,不改变分组归属,避免同一项在菜单里出现两次。
 */
export function filterMentionGroups(
  items: ComposerItem[],
  query: string,
  recentKeys: readonly string[] = [],
): ComposerGroup[] {
  const needle = query.trim().toLowerCase();
  const recentRank = new Map(recentKeys.map((key, index) => [key, index]));
  const sorted = items
    .filter((item) => matches(query, item.label, item.id, item.description))
    .sort((left, right) => {
      const leftPrefix = left.label.toLowerCase().startsWith(needle) ? 0 : 1;
      const rightPrefix = right.label.toLowerCase().startsWith(needle) ? 0 : 1;
      if (leftPrefix !== rightPrefix) return leftPrefix - rightPrefix;
      const leftRecent = recentRank.get(left.key) ?? Number.MAX_SAFE_INTEGER;
      const rightRecent = recentRank.get(right.key) ?? Number.MAX_SAFE_INTEGER;
      return leftRecent - rightRecent;
    });
  const groups: ComposerGroup[] = [
    { key: 'skills', label: '技能', options: [] },
    { key: 'mcp', label: 'MCP 服务', options: [] },
    { key: 'connectors', label: '连接器', options: [] },
    { key: 'messages', label: '消息', options: [] },
    { key: 'memories', label: '记忆', options: [] },
    { key: 'knowledge', label: '知识库文档', options: [] },
    { key: 'artifacts', label: '交付物', options: [] },
    { key: 'sandbox', label: '沙箱文件', options: [] },
  ];
  const groupOf: Record<string, number> = {
    skill: 0,
    mcp: 1,
    connector: 2,
    message: 3,
    memory: 4,
    knowledge_document: 5,
    artifact: 6,
    sandbox_file: 7,
  };
  for (const item of sorted) {
    const group = groups[groupOf[item.kind] ?? 0];
    group.options.push({
      key: item.key,
      label: item.label,
      description: item.description,
      icon: item.icon,
      current: item.current,
    });
  }
  return groups.filter((group) => group.options.length > 0);
}
