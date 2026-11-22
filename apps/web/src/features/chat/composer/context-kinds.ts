/**
 * 内容引用的展示元数据(图标/中文名/菜单 key 映射)。
 * 单独成文件:组件文件只导出组件,否则 fast refresh 失效。
 */
import { FileCode2, FileText, FolderOpen, MessageSquare } from 'lucide-react';

import type { ContextRefKind } from '@/lib/conversations-api';

export const CONTEXT_KIND_ICONS = {
  knowledge_document: FileText,
  artifact: FolderOpen,
  sandbox_file: FileCode2,
  message: MessageSquare,
} satisfies Record<ContextRefKind, typeof FileText>;

export const CONTEXT_KIND_LABELS: Record<ContextRefKind, string> = {
  knowledge_document: '知识库',
  artifact: '交付物',
  sandbox_file: '沙箱文件',
  message: '消息',
};

/**
 * @ 菜单项的 key 前缀与 context ref 的 kind 不同名(菜单用短名),
 * 这里统一映射,保证删除引用芯片时能命中正文 token 区间。
 */
const KEY_PREFIX: Record<string, string> = {
  knowledge_document: 'knowledge',
  artifact: 'artifact',
  sandbox_file: 'sandbox',
  message: 'message',
};

export function refKey(ref: { kind: string; id: string }): string {
  return `${KEY_PREFIX[ref.kind] ?? ref.kind}:${ref.id}`;
}
