/**
 * @ 菜单里的「内容引用」与「连接器」数据源。
 *
 * 与技能/MCP 不同:连接器是能力绑定(只写 selectedConnectors),
 * 知识库文档/交付物/沙箱文件是内容引用(写 contextRefs 并落库)。
 * 正文 token 只是可读回声,提交时只发结构化 id。
 */
import { useQuery } from '@tanstack/react-query';
import { FileText, FolderOpen, MessageSquare, Package } from 'lucide-react';

import type { ChatMessage } from '@/lib/conversations-api';
import { connectorApi } from '@/lib/connector-api';
import { knowledgeApi } from '@/lib/knowledge-api';
import { queryKeys } from '@/lib/query-keys';
import { sandboxTree } from '@/lib/sandbox-api';
import { workspaceApi } from '@/lib/workspace-api';

import { truncateTitle } from './mention-ranges';
import type { ComposerItem } from './types';

type Options = {
  workspaceId: string | null;
  /** 当前会话的沙箱;没有沙箱时沙箱文件组为空(新建会话的常态)。 */
  sandboxId: string | null;
  /** 当前会话已落库的消息;新建会话时为空数组。 */
  messages: ChatMessage[];
};

/** 消息引用只列最近 20 条:更早的交给"引用目录"再做(P2 待排期)。 */
const MESSAGE_ITEM_LIMIT = 20;

function messageTitle(message: ChatMessage): string {
  const firstLine = message.content.split('\n')[0].trim();
  return firstLine || message.content.trim();
}

/** 已连接且启用的连接器 provider(按 provider 粒度,不是 connection id)。 */
function useConnectedProviders() {
  return useQuery({
    queryKey: queryKeys.connectorProviders,
    queryFn: () => connectorApi.listProviders(),
    staleTime: 60_000,
    retry: false,
  });
}

function useKnowledgeDocuments() {
  return useQuery({
    queryKey: queryKeys.knowledgeDocuments,
    queryFn: () => knowledgeApi.documents(),
    staleTime: 60_000,
    retry: false,
  });
}

function useWorkspaceArtifacts(workspaceId: string | null) {
  return useQuery({
    queryKey: queryKeys.workspaceArtifacts(workspaceId ?? ''),
    queryFn: () => workspaceApi.artifacts(workspaceId as string),
    enabled: Boolean(workspaceId),
    staleTime: 30_000,
    retry: false,
  });
}

function useSandboxFiles(sandboxId: string | null) {
  return useQuery({
    queryKey: ['composer-sandbox-files', sandboxId ?? ''],
    queryFn: async () => {
      const tree = await sandboxTree(sandboxId as string, {
        path: '/workspace',
        depth: 3,
      });
      return tree.entries.filter((entry) => entry.kind === 'file');
    },
    enabled: Boolean(sandboxId),
    staleTime: 30_000,
    retry: false,
  });
}

/** 把四类来源归一成 @ 菜单项;连接器 current 由 selectedConnectors 决定。 */
export function useComposerContextSources({
  workspaceId,
  sandboxId,
  messages,
}: Options): { items: ComposerItem[]; loading: boolean } {
  const providers = useConnectedProviders();
  const documents = useKnowledgeDocuments();
  const artifacts = useWorkspaceArtifacts(workspaceId);
  const files = useSandboxFiles(sandboxId);

  const items: ComposerItem[] = [
    // 最近的对话消息排最前:引用上一轮结论是最常见的用法
    ...messages
      .filter((message) => message.content.trim().length > 0)
      .slice(-MESSAGE_ITEM_LIMIT)
      .reverse()
      .map((message) => {
        const title = messageTitle(message);
        return {
          key: `message:${message.id}`,
          kind: 'message' as const,
          id: message.id,
          label: truncateTitle(title),
          token: `@${truncateTitle(title)}`,
          description:
            message.role === 'user' ? '消息 · 你发的' : '消息 · 助手回复',
          icon: MessageSquare,
        };
      }),
    ...(providers.data ?? [])
      .filter(
        (provider) =>
          provider.connected === true && provider.connection?.enabled !== false,
      )
      .map((provider) => ({
        key: `connector:${provider.provider}`,
        kind: 'connector' as const,
        id: provider.provider,
        label: provider.display_name || provider.provider,
        token: `@${truncateTitle(provider.display_name || provider.provider)}`,
        description: `连接器 · ${provider.tools.length} 个工具`,
        icon: Package,
      })),
    ...(documents.data ?? []).map((document) => ({
      key: `knowledge:${document.id}`,
      kind: 'knowledge_document' as const,
      id: document.id,
      label: document.title || document.id,
      token: `@${truncateTitle(document.title || document.id)}`,
      description: `知识库 · ${document.chunk_count} 段`,
      icon: FileText,
    })),
    ...(artifacts.data ?? []).map((artifact) => ({
      key: `artifact:${artifact.id}`,
      kind: 'artifact' as const,
      id: artifact.id,
      label: artifact.title || artifact.filename || artifact.id,
      token: `@${truncateTitle(artifact.title || artifact.filename || artifact.id)}`,
      description: `交付物 · ${artifact.kind}`,
      icon: FolderOpen,
    })),
    ...(files.data ?? []).map((entry) => {
      const token = entry.path.replace(/^\/workspace\//, '');
      return {
        key: `sandbox:${entry.path}`,
        kind: 'sandbox_file' as const,
        id: entry.path,
        label: token,
        token: `@${truncateTitle(token)}`,
        description: '沙箱文件',
        icon: FileText,
      };
    }),
  ];

  return {
    items,
    loading:
      providers.isPending ||
      documents.isPending ||
      (Boolean(workspaceId) && artifacts.isPending) ||
      (Boolean(sandboxId) && files.isPending),
  };
}
