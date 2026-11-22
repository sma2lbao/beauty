import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type KeyboardEvent,
  type RefObject,
} from 'react';

import { measureCaretRect, type CaretAnchor } from './caret-rect';
import { filterCommandGroups } from './command-registry';
import { filterMentionGroups } from './context-sources';
import {
  detectCommand,
  detectMention,
  remapRanges,
  removeTokenRange,
  type TokenRange,
} from './mention-ranges';
import type {
  ComposerCommand,
  ComposerCommandId,
  ComposerGroup,
  ComposerItem,
  ComposerOption,
  ComposerSubmenu,
} from './types';

type ComposerMenuInput = {
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  value: string;
  onChange: (value: string) => void;
  items: ComposerItem[];
  commands: ComposerCommand[];
  submenus: Partial<Record<ComposerCommandId, ComposerSubmenu>>;
  onPickItem: (item: ComposerItem) => void;
  onRunCommand: (command: ComposerCommand) => void;
  onPickSubmenuOption: (commandId: ComposerCommandId, value: string) => void;
};

/** @ 与 / 共用的 suggestion 状态机:正文仍是受控纯文本,token 只用于插入可读文本与维护区间;真正提交的是页面级能力绑定与 context refs,不从正文解析。 */
export function useComposerMenu({
  textareaRef,
  value,
  onChange,
  items,
  commands,
  submenus,
  onPickItem,
  onRunCommand,
  onPickSubmenuOption,
}: ComposerMenuInput) {
  const [mode, setMode] = useState<'mention' | 'command' | null>(null);
  const [triggerStart, setTriggerStart] = useState(0);
  const [query, setQuery] = useState('');
  const [caret, setCaret] = useState(0);
  const [submenuId, setSubmenuId] = useState<ComposerCommandId | null>(null);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [anchor, setAnchor] = useState<CaretAnchor | null>(null);
  const tokensRef = useRef<TokenRange[]>([]);
  const previousValueRef = useRef(value);
  const pendingCaretRef = useRef<number | null>(null);

  const submenu = submenuId ? (submenus[submenuId] ?? null) : null;

  const close = useCallback(() => {
    setMode(null);
    setQuery('');
    setSubmenuId(null);
    setActiveKey(null);
    setAnchor(null);
  }, []);

  const groups = useMemo<ComposerGroup[]>(() => {
    if (mode === 'mention') return filterMentionGroups(items, query);
    if (mode === 'command') {
      if (submenu) {
        return [
          {
            key: `submenu:${submenu.commandId}`,
            label: submenu.title,
            options: submenu.options,
          },
        ];
      }
      return filterCommandGroups(commands, query);
    }
    return [];
  }, [commands, items, mode, query, submenu]);

  const options = useMemo(
    () => groups.flatMap((group) => group.options),
    [groups],
  );
  const resolvedKey = useMemo(() => {
    if (
      activeKey &&
      options.some((option) => option.key === activeKey && !option.disabled)
    ) {
      return activeKey;
    }
    return options.find((option) => !option.disabled)?.key ?? null;
  }, [activeKey, options]);
  const activeOption = options.find((option) => option.key === resolvedKey);

  const sync = useCallback(
    (
      next: string,
      nextCaret: number,
      inputType: string | undefined,
      composing: boolean,
    ) => {
      const remapped = remapRanges(
        previousValueRef.current,
        next,
        tokensRef.current,
      );
      tokensRef.current = remapped;
      previousValueRef.current = next;
      if (submenuId) setSubmenuId(null);
      const command = detectCommand(next, nextCaret, composing);
      if (command) {
        setMode('command');
        setTriggerStart(command.start);
        setQuery(command.query);
        setCaret(nextCaret);
        setActiveKey(null);
        const textarea = textareaRef.current;
        if (textarea) setAnchor(measureCaretRect(textarea, nextCaret));
        return;
      }
      const mention = detectMention(next, nextCaret, remapped, inputType);
      if (mention) {
        setMode('mention');
        setTriggerStart(mention.start);
        setQuery(mention.query);
        setCaret(nextCaret);
        setActiveKey(null);
        const textarea = textareaRef.current;
        if (textarea) setAnchor(measureCaretRect(textarea, nextCaret));
        return;
      }
      close();
    },
    [close, submenuId, textareaRef],
  );

  const handleChange = useCallback(
    (event: ChangeEvent<HTMLTextAreaElement>) => {
      const next = event.target.value;
      const nextCaret = event.target.selectionStart ?? next.length;
      const native = event.nativeEvent as InputEvent;
      onChange(next);
      sync(next, nextCaret, native.inputType, native.isComposing);
    },
    [onChange, sync],
  );

  const applyItem = useCallback(
    (item: ComposerItem) => {
      let end = caret;
      while (value[end] === ' ') end += 1;
      const next = `${value.slice(0, triggerStart)}${item.token} ${value.slice(end)}`;
      const nextCaret = triggerStart + item.token.length + 1;
      tokensRef.current = [
        ...remapRanges(value, next, tokensRef.current),
        {
          refId: item.key,
          start: triggerStart,
          end: triggerStart + item.token.length,
          token: item.token,
        },
      ];
      previousValueRef.current = next;
      pendingCaretRef.current = nextCaret;
      setCaret(nextCaret);
      onChange(next);
      onPickItem(item);
      close();
    },
    [caret, close, onChange, onPickItem, triggerStart, value],
  );

  /** 删除已输入的 / 命令文本,把光标放回触发位置。 */
  const removeCommandText = useCallback(() => {
    const next = value.slice(0, triggerStart) + value.slice(caret);
    const nextCaret = Math.min(triggerStart, next.length);
    tokensRef.current = remapRanges(value, next, tokensRef.current);
    previousValueRef.current = next;
    pendingCaretRef.current = nextCaret;
    setCaret(nextCaret);
    onChange(next);
    return nextCaret;
  }, [caret, onChange, triggerStart, value]);

  /** 删除正文里的一段 token(引用轨芯片删除时同步)。 */
  const removeToken = useCallback(
    (refId: string) => {
      const next = removeTokenRange(value, tokensRef.current, refId);
      if (!next) return;
      tokensRef.current = next.ranges;
      previousValueRef.current = next.value;
      onChange(next.value);
      close();
    },
    [close, onChange, value],
  );

  const choose = useCallback(
    (option: ComposerOption) => {
      if (option.disabled) return;
      if (mode === 'mention') {
        const item = items.find((candidate) => candidate.key === option.key);
        if (item) applyItem(item);
        return;
      }
      if (submenu) {
        onPickSubmenuOption(submenu.commandId, option.value ?? '');
        // sticky(如 /connector)留在原地连续勾选;其余二级菜单选完即收
        if (!submenu.sticky) close();
        return;
      }
      if (!option.commandId) return;
      const command = commands.find((item) => item.id === option.commandId);
      if (!command || command.disabled) return;
      removeCommandText();
      if (command.kind === 'capability') {
        const nextSubmenu = submenus[command.id];
        if (nextSubmenu) {
          setMode('command');
          setSubmenuId(command.id);
          setQuery('');
          setActiveKey(null);
        }
        return;
      }
      onRunCommand(command);
      close();
    },
    [
      applyItem,
      close,
      commands,
      items,
      mode,
      onPickSubmenuOption,
      onRunCommand,
      removeCommandText,
      submenu,
      submenus,
    ],
  );

  const move = useCallback(
    (delta: number) => {
      if (!options.length) return;
      const currentIndex = options.findIndex(
        (option) => option.key === resolvedKey,
      );
      let index = currentIndex < 0 ? 0 : currentIndex;
      for (let step = 0; step < options.length; step += 1) {
        index = (index + delta + options.length) % options.length;
        if (!options[index].disabled) {
          setActiveKey(options[index].key);
          return;
        }
      }
    },
    [options, resolvedKey],
  );

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.nativeEvent.isComposing) return false;
      if (!mode) return false;
      if (!options.length) {
        if (event.key === 'Escape') {
          event.preventDefault();
          close();
          return true;
        }
        return false;
      }
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        move(1);
        return true;
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        move(-1);
        return true;
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        close();
        return true;
      }
      // Enter/Tab 确认;多选二级菜单(如 /connector)里 Space 等价,便于连续勾选
      if (
        event.key === 'Enter' ||
        event.key === 'Tab' ||
        (event.key === ' ' && submenu?.sticky)
      ) {
        if (!activeOption || activeOption.disabled) return false;
        event.preventDefault();
        choose(activeOption);
        return true;
      }
      return false;
    },
    [activeOption, choose, close, mode, move, options.length, submenu],
  );

  const handleSelect = useCallback(() => {
    if (!mode) return;
    const textarea = textareaRef.current;
    if (!textarea) return;
    // 子菜单打开时正文已被清空:选择事件不应把它关掉。
    if (submenu) return;
    const nextValue = textarea.value;
    const nextCaret = textarea.selectionStart ?? nextValue.length;
    if (!nextValue) return;
    if (mode === 'command') {
      const command = detectCommand(nextValue, nextCaret, false);
      if (
        nextCaret !== nextValue.length ||
        !command ||
        command.start !== triggerStart
      ) {
        close();
        return;
      }
      setCaret(nextCaret);
      setQuery(command.query);
      setAnchor(measureCaretRect(textarea, nextCaret));
      return;
    }
    const mention = detectMention(nextValue, nextCaret, tokensRef.current);
    if (!mention || mention.start !== triggerStart) {
      close();
      return;
    }
    setCaret(nextCaret);
    setQuery(mention.query);
    setAnchor(measureCaretRect(textarea, nextCaret));
  }, [close, mode, submenu, textareaRef, triggerStart]);

  // 外部改正文(如 /new 清空、编辑重发)时作废 token,避免区间漂移。
  useEffect(() => {
    if (value === previousValueRef.current) return;
    if (!value) {
      tokensRef.current = [];
      previousValueRef.current = '';
      // eslint-disable-next-line react-hooks/set-state-in-effect -- 外部清空正文(如 /new)时必须同步收起菜单
      close();
      return;
    }
    tokensRef.current = remapRanges(
      previousValueRef.current,
      value,
      tokensRef.current,
    );
    previousValueRef.current = value;
    close();
  }, [close, value]);

  // 插入 token 后恢复光标;必须在 DOM 更新后执行。
  useLayoutEffect(() => {
    const nextCaret = pendingCaretRef.current;
    if (nextCaret === null) return;
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.focus();
    textarea.setSelectionRange(nextCaret, nextCaret);
    setCaret(nextCaret);
    pendingCaretRef.current = null;
  }, [textareaRef, value]);

  return {
    open: mode !== null,
    mode,
    groups,
    activeKey: resolvedKey,
    activeOption,
    anchor,
    submenu,
    label: mode === 'mention' ? '上下文候选' : '命令候选',
    emptyLabel:
      mode === 'mention'
        ? '没有匹配的上下文,可直接发送'
        : '没有匹配的命令,可直接发送',
    handleChange,
    handleKeyDown,
    handleSelect,
    choose,
    removeToken,
    setActiveKey,
  };
}
