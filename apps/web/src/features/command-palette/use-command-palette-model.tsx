/**
 * 命令面板的模型:把页面、会话与操作三类入口整理成一份可选择的扁平列表。
 *
 * 分组只影响展示,键盘选择只认扁平索引 —— 若让组件自己遍历分组算位置,
 * 选中态、aria-activedescendant 与回车执行很容易各算一套而对不上。
 */
import { useQuery } from '@tanstack/react-query';
import { FolderPlus, MessageSquare, PanelLeft, Plus } from 'lucide-react';
import {
  useCallback,
  useMemo,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from 'react';
import { useNavigate } from 'react-router';

import { ALL_NAV_ITEMS, SETTINGS_NAV_ITEM } from '@/features/sidebar/nav-data';
import { conversationsApi, type Conversation } from '@/lib/conversations-api';
import { queryKeys } from '@/lib/query-keys';
import { useWorkspaceSelection } from '@/lib/workspace-selection';
import { useUiStore } from '@/stores/ui-store';

export type CommandPaletteItem = {
  /** 稳定标识,仅用于 React key;DOM id 由渲染顺序决定。 */
  id: string;
  label: string;
  icon: ReactNode;
  /** 右侧弱化提示(页面路径),操作项不显示。 */
  hint?: string;
  disabled?: boolean;
  run: () => void;
};

export type CommandPaletteGroup = {
  id: 'pages' | 'conversations' | 'actions';
  label: string;
  items: CommandPaletteItem[];
  /** 组内没有可选项时的说明;null 表示整组不渲染。 */
  placeholder: string | null;
};

const MAX_CONVERSATION_RESULTS = 8;

function matchesLabel(label: string, term: string): boolean {
  return label.toLowerCase().includes(term.toLowerCase());
}

/** 会话回链:未分组会话没有 workspace_id,此时只带 conversation。 */
function conversationHref(conversation: Conversation): string {
  const search = new URLSearchParams({ conversation: conversation.id });
  if (conversation.workspace_id) {
    search.set('workspace', conversation.workspace_id);
  }
  return `/?${search.toString()}`;
}

/** 新建会话落在当前工作区;还没有工作区时不带参数,由服务端回落到默认工作区。 */
function newConversationHref(activeWorkspaceId: string | null): string {
  const search = new URLSearchParams();
  if (activeWorkspaceId) {
    search.set('workspace', activeWorkspaceId);
  }
  search.set('new', '1');
  return `/?${search.toString()}`;
}

export function useCommandPaletteModel() {
  const navigate = useNavigate();
  const { activeWorkspaceId } = useWorkspaceSelection();
  const setCommandPaletteOpen = useUiStore(
    (state) => state.setCommandPaletteOpen,
  );
  const toggleSidebar = useUiStore((state) => state.toggleSidebar);
  const sidebarVisible = useUiStore((state) => state.sidebarVisible);

  const [query, setQueryState] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const term = query.trim();

  const setQuery = useCallback((value: string) => {
    setQueryState(value);
    // 搜索词一变,旧索引指向的是上一批结果;不重置的话回车会执行看不见的项。
    setSelectedIndex(0);
  }, []);

  // 只有输入非空才查会话:空输入下面的会话分组只是提示,不该无意义地打一次接口。
  const conversationQuery = useQuery({
    queryKey: [...queryKeys.conversations, 'palette', term],
    queryFn: () =>
      conversationsApi.page({
        limit: MAX_CONVERSATION_RESULTS,
        q: term,
      }),
    enabled: term.length > 0,
  });

  const pages = useMemo<CommandPaletteItem[]>(() => {
    return [...ALL_NAV_ITEMS, SETTINGS_NAV_ITEM]
      .filter((item) => matchesLabel(item.label, term))
      .map((item) => ({
        id: `page:${item.to}`,
        label: item.label,
        icon: item.icon,
        hint: item.to === '/' ? undefined : item.to,
        run: () => navigate(item.to),
      }));
  }, [navigate, term]);

  const sessions = useMemo<CommandPaletteItem[]>(() => {
    const rows = conversationQuery.data?.items ?? [];
    return rows.map((conversation) => ({
      id: `conversation:${conversation.id}`,
      label: conversation.title || '未命名会话',
      icon: <MessageSquare />,
      run: () => navigate(conversationHref(conversation)),
    }));
  }, [conversationQuery.data, navigate]);

  const actions = useMemo<CommandPaletteItem[]>(() => {
    const all: CommandPaletteItem[] = [
      {
        id: 'action:new-conversation',
        label: '新建会话',
        icon: <Plus />,
        run: () => navigate(newConversationHref(activeWorkspaceId)),
      },
      {
        id: 'action:new-workspace',
        label: '新建工作区',
        icon: <FolderPlus />,
        run: () => navigate('/workspace?new=1'),
      },
      {
        id: 'action:toggle-sidebar',
        // 文案跟着当前状态走:命令面板里显示的是「点了会发生什么」。
        label: sidebarVisible ? '隐藏侧边栏' : '显示侧边栏',
        icon: <PanelLeft />,
        run: () => toggleSidebar(),
      },
    ];
    return all.filter((item) => matchesLabel(item.label, term));
  }, [activeWorkspaceId, navigate, sidebarVisible, term, toggleSidebar]);

  const conversationsPlaceholder = !term
    ? '输入关键词搜索会话'
    : conversationQuery.isPending
      ? '搜索中…'
      : conversationQuery.isError
        ? '会话搜索失败'
        : sessions.length === 0
          ? '没有匹配的会话'
          : null;

  const groups = useMemo<CommandPaletteGroup[]>(
    () => [
      {
        id: 'pages',
        label: '页面',
        items: pages,
        placeholder:
          term.length > 0 && pages.length === 0 ? '没有匹配的页面' : null,
      },
      {
        id: 'conversations',
        label: '会话',
        items: sessions,
        placeholder: conversationsPlaceholder,
      },
      {
        id: 'actions',
        label: '操作',
        items: actions,
        placeholder:
          term.length > 0 && actions.length === 0 ? '没有匹配的操作' : null,
      },
    ],
    [actions, conversationsPlaceholder, pages, sessions, term],
  );

  const items = useMemo(() => groups.flatMap((group) => group.items), [groups]);
  // 会话结果异步追加到列表时,旧索引可能已经越界;渲染前先夹到有效范围。
  const activeIndex =
    items.length === 0 ? -1 : Math.min(selectedIndex, items.length - 1);

  const close = useCallback(() => {
    setCommandPaletteOpen(false);
  }, [setCommandPaletteOpen]);

  const execute = useCallback(
    (item: CommandPaletteItem | undefined) => {
      if (!item || item.disabled) {
        return;
      }
      // 先关再执行:导航会让页面重渲染,先关掉才能保证面板不会挂在新页面上。
      setCommandPaletteOpen(false);
      item.run();
    },
    [setCommandPaletteOpen],
  );

  const move = useCallback(
    (delta: number) => {
      if (items.length === 0) {
        return;
      }
      let next = activeIndex;
      // 逐项试探而不是简单取模:禁用项不能停,到头后继续回绕到另一端。
      for (let step = 0; step < items.length; step += 1) {
        next = (next + delta + items.length) % items.length;
        if (!items[next].disabled) {
          setSelectedIndex(next);
          return;
        }
      }
    },
    [activeIndex, items],
  );

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLInputElement>) => {
      // 中文输入法选词时的回车/方向键属于输入法,不能拿来做面板操作。
      if (event.nativeEvent.isComposing) {
        return;
      }
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        move(1);
        return;
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        move(-1);
        return;
      }
      if (event.key === 'Enter') {
        event.preventDefault();
        execute(items[activeIndex]);
        return;
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        close();
      }
    },
    [activeIndex, close, execute, items, move],
  );

  return {
    query,
    setQuery,
    groups,
    items,
    activeIndex,
    setActiveIndex: setSelectedIndex,
    execute,
    close,
    handleKeyDown,
  };
}
