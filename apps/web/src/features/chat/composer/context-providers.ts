/**
 * @ 菜单里的「内容引用」与「连接器」数据源。
 *
 * 与技能/MCP 不同:连接器是能力绑定(只写 selectedConnectors),
 * 知识库文档/交付物/沙箱文件是内容引用(写 contextRefs 并落库)。
 * 正文 token 只是可读回声,提交时只发结构化 id。
 */
import { useQuery } from '@tanstack/react-query';
import {
  Brain,
  FileText,
  FolderOpen,
  MessageSquare,
  Package,
} from 'lucide-react';

import type { ChatMessage } from '@/lib/conversations-api';
import { connectorApi } from '@/lib/connector-api';
import { knowledgeApi } from '@/lib/knowledge-api';
import { memoryApi } from '@/lib/memory-api';
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

/** 记忆引用取最近 30 条;检索留给 @ 搜索(P2 待排期)。 */
const MEMORY_ITEM_LIMIT = 30;

/** 记忆类型的展示名(与后端 MEMORY_KIND_LABELS 保持一致)。 */
const MEMORY_KIND_LABELS: Record<string, string> = {
  note: '随手记',
  preference: '偏好',
  fact: '事实',
  procedure: '流程',
};

function messageTitle(message: ChatMessage): string {
  const firstLine = message.content.split('\n')[0].trim();
  return firstLine || message.content.trim();
}

function memoryTitle(content: string): string {
  const firstLine = content.split('\n')[0].trim();
  return firstLine || content.trim();
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

/** 账号级 + 当前工作区的记忆(后端按 account 隔离,这里只做展示)。 */
function useMemories(workspaceId: string | null) {
  return useQuery({
    queryKey: queryKeys.memoryList(undefined, workspaceId ?? undefined),
    queryFn: () =>
      memoryApi.list({
        workspaceId: workspaceId ?? undefined,
        limit: MEMORY_ITEM_LIMIT,
      }),
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
  const memories = useMemories(workspaceId);
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
    // 记忆紧随消息:两者都是"对话上下文",先于外部资料
    ...(memories.data ?? []).map((memory) => {
      const title = memoryTitle(memory.content);
      return {
        key: `memory:${memory.id}`,
        kind: 'memory' as const,
        id: memory.id,
        label: truncateTitle(title),
        token: `@${truncateTitle(title)}`,
        description: `记忆 · ${MEMORY_KIND_LABELS[memory.kind] ?? memory.kind}`,
        icon: Brain,
      };
    }),
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
      memories.isPending ||
      (Boolean(workspaceId) && artifacts.isPending) ||
      (Boolean(sandboxId) && files.isPending),
  };
}
