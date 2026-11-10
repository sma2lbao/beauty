/**
 * 侧栏导航的单一数据源。
 *
 * 三处消费同一份定义,避免入口表各写一份而漂移:
 * - 侧栏导航组(features/sidebar/nav.tsx,组头可折叠);
 * - 移动端顶栏横向导航(components/console/console-layout.tsx);
 * - 命令面板 ⌘K(features/command-palette,步骤 A 复用同一批页面项)。
 *
 * 分组依据是「多久碰一次」,不是功能域 —— 这是收敛入口的落点:
 * - work:每个会话都要用(工作台/任务编排/变更)→ 常驻;
 * - resources:知识库/记忆/Skills/连接器 → 常驻,但排在会话之后;
 * - ops:管理台(仅管理员)/用量/事件队列 → 默认折叠,统计与运维不参与日常路径。
 *
 * 刻意不放进 NAV_GROUPS 的两个入口:
 * - 「设置」「官网」属于账户行为,挂在底部账户卡片的菜单里;
 * - 「工作区」页只在移动端保留:桌面端切换器菜单已有「管理工作区…」,
 *   侧栏再平铺一行是重复入口;移动端没有侧栏,仍需一个页面入口。
 */
import {
  BarChart3,
  BookOpen,
  Brain,
  Cable,
  Columns3,
  FolderKanban,
  GitCompareArrows,
  Globe,
  Inbox,
  LayoutGrid,
  Settings,
  ShieldCheck,
  Sparkles,
} from 'lucide-react';
import type { ReactNode } from 'react';

import { siteUrl } from '@/lib/site-links';

export type NavGroupId = 'work' | 'resources' | 'ops';

export type NavItem = {
  to: string;
  /** react-router NavLink 的 end:只有完全匹配才高亮(工作台用) */
  end?: boolean;
  icon: ReactNode;
  label: string;
  /** 仅管理员可见(P0-7):消费方按当前角色过滤,见 nav-state.ts。 */
  adminOnly?: boolean;
};

export type NavGroup = {
  id: NavGroupId;
  label: string;
  items: NavItem[];
  /** 首次进入的展开状态;用户在侧栏折叠/展开过就以本地偏好(ui-store)为准。 */
  defaultOpen: boolean;
};

export const NAV_GROUPS: NavGroup[] = [
  {
    id: 'work',
    label: '工作',
    defaultOpen: true,
    items: [
      { to: '/', end: true, icon: <LayoutGrid />, label: '工作台' },
      { to: '/runs', icon: <Columns3 />, label: '任务编排' },
      { to: '/changes', icon: <GitCompareArrows />, label: '变更' },
    ],
  },
  {
    id: 'resources',
    label: '资源',
    defaultOpen: true,
    items: [
      { to: '/knowledge', icon: <BookOpen />, label: '知识库' },
      { to: '/memory', icon: <Brain />, label: '记忆' },
      { to: '/skills', icon: <Sparkles />, label: 'Skills' },
      // MCP 入口已并入连接器页(页内「我的 MCP 服务」段),不再单列
      { to: '/connectors', icon: <Cable />, label: '连接器' },
    ],
  },
  {
    id: 'ops',
    label: '管理',
    defaultOpen: false,
    items: [
      {
        to: '/admin',
        icon: <ShieldCheck />,
        label: '管理台',
        adminOnly: true,
      },
      { to: '/usage', icon: <BarChart3 />, label: '用量' },
      { to: '/outbox', icon: <Inbox />, label: '事件队列' },
    ],
  },
];

/** 底部账户卡片菜单:设置(原导航里的那一项)+ 官网外链。 */
export const SETTINGS_NAV_ITEM: NavItem = {
  to: '/settings',
  icon: <Settings />,
  label: '设置',
};

export const SITE_NAV_ITEM: { label: string; icon: ReactNode; href: string } = {
  label: '官网',
  icon: <Globe />,
  href: siteUrl('/'),
};

/** 全部页面入口(不含外链):命令面板的「页面」分组按频次复用这个顺序。 */
export const ALL_NAV_ITEMS: NavItem[] = NAV_GROUPS.flatMap(
  (group) => group.items,
);

/** 只在移动端顶栏出现的页面入口(桌面端已有切换器菜单)。 */
export const MOBILE_ONLY_NAV_ITEMS: NavItem[] = [
  { to: '/workspace', icon: <FolderKanban />, label: '工作区' },
];

/**
 * 移动端顶栏:同一份数据源,按横滚空间做取舍 ——
 * 「记忆」「事件队列」不上顶栏(低频,且横滚条已经很长),其余与桌面一致;
 * 再加「设置」:移动端没有底部账户卡片,设置需要一个入口。
 */
const MOBILE_SKIPPED = new Set(['/memory', '/outbox']);

export const MOBILE_NAV_ITEMS: NavItem[] = [
  ...NAV_GROUPS[0].items,
  ...MOBILE_ONLY_NAV_ITEMS,
  ...NAV_GROUPS[1].items.filter((item) => !MOBILE_SKIPPED.has(item.to)),
  ...NAV_GROUPS[2].items.filter((item) => !MOBILE_SKIPPED.has(item.to)),
  SETTINGS_NAV_ITEM,
];
