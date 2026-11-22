import type { LucideIcon } from 'lucide-react';

import type { ContextRefKind } from '@/lib/conversations-api';

/** 根菜单里的斜杠命令 id(P0 固定清单)。 */
export type ComposerCommandId =
  'new' | 'stop' | 'attach' | 'model' | 'mode' | 'skill' | 'mcp' | 'connector';

/** @ 菜单项的语义:能力绑定(技能/MCP/连接器)或内容引用。 */
export type ComposerItemKind = 'skill' | 'mcp' | 'connector' | ContextRefKind;

/** @ 菜单里的一项:技能、MCP、连接器,或一条内容引用。 */
export type ComposerItem = {
  key: string;
  kind: ComposerItemKind;
  id: string;
  label: string;
  /** 插入正文的可读 token(不含尾随空格)。 */
  token: string;
  description?: string;
  icon: LucideIcon;
  current?: boolean;
};

export type ComposerOption = {
  key: string;
  label: string;
  description?: string;
  icon?: LucideIcon;
  disabled?: boolean;
  disabledReason?: string;
  current?: boolean;
  /** 根菜单中的命令项。 */
  commandId?: ComposerCommandId;
  /** 二级菜单选中后写回的能力值(空串表示「不使用」)。 */
  value?: string;
};

export type ComposerGroup = {
  key: string;
  label: string;
  options: ComposerOption[];
};

export type ComposerCommand = {
  id: ComposerCommandId;
  label: string;
  description: string;
  icon: LucideIcon;
  /** action 立即执行;capability 打开二级菜单。 */
  kind: 'action' | 'capability';
  disabled?: boolean;
  disabledReason?: string;
};

export type ComposerSubmenu = {
  commandId: ComposerCommandId;
  title: string;
  options: ComposerOption[];
  /** 多选二级菜单:选中不关闭,Enter/Space 切换,直到 Esc/点击外部。 */
  sticky?: boolean;
};
