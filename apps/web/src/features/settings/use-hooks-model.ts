/**
 * 自动化 hooks 的模型层(P1-B,design/hooks.md §8)。
 *
 * - 列表查询(queryKeys.hooks):创建/更新/删除/轮换后统一失效重取;
 * - 新建表单在本地先按 §7 校验(https、matcher 长度、超时 1..30),不合法不发请求;
 * - secret 一次性:创建/轮换成功放进 secret state,弹窗关闭即丢弃,不写缓存;
 * - 部署总开关关闭时后端一律 409 hooks.disabled:这里归一成 disabled 态,
 *   面板整卡显示说明,而不是当作普通加载失败。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import {
  hooksApi,
  type Hook,
  type HookCreateInput,
  type HookEvent,
  type HookSecret,
} from '@/lib/hooks-api';
import { ApiError } from '@/lib/http';
import { queryKeys } from '@/lib/query-keys';

export const HOOK_TIMEOUT_MIN = 1;
export const HOOK_TIMEOUT_MAX = 30;

/** 事件的中文名(表格与下拉共用;面板据此展示「工具调用前」等)。 */
export const EVENT_LABELS: Record<HookEvent, string> = {
  PreToolUse: '工具调用前',
  PostToolUse: '工具调用后',
  SessionStart: '会话开始',
  SessionEnd: '会话结束',
  PreCompact: '上下文压缩前',
};

export type HookForm = {
  event: HookEvent;
  matcher: string;
  target: string;
  timeout: string;
};

export type HookSecretState = {
  hookId: string;
  event: HookEvent;
  target: string;
  secret: string;
};

const EMPTY_FORM: HookForm = {
  event: 'PreToolUse',
  matcher: '*',
  target: '',
  timeout: '3',
};

/** 错误码 → 面板可读文案(§7);未知错误回退到 message。 */
export function hookErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case 'hooks.disabled':
        return '当前部署未启用 hooks。';
      case 'hooks.too_many':
        return 'hook 数量已达上限(20 条),请先删除不再使用的。';
      case 'hooks.invalid_target':
        return '目标地址无效:只能是公网 HTTPS 地址(拒绝私网/环回/元数据地址)。';
      case 'hooks.not_found':
        return 'hook 不存在或已被删除,请刷新列表。';
      default:
        break;
    }
  }
  return error instanceof Error ? error.message : '操作失败,请重试。';
}

function isDisabledError(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'hooks.disabled';
}

export function validateHookForm(form: HookForm): string | null {
  const target = form.target.trim();
  if (!target) return '目标地址不能为空。';
  if (!/^https:\/\//i.test(target)) return '目标地址必须是 HTTPS URL。';
  if (target.length > 1024) return '目标地址不能超过 1024 个字符。';
  if (form.matcher.trim().length > 200) {
    return '匹配表达式不能超过 200 个字符。';
  }
  const timeout = Number(form.timeout);
  if (
    !Number.isInteger(timeout) ||
    timeout < HOOK_TIMEOUT_MIN ||
    timeout > HOOK_TIMEOUT_MAX
  ) {
    return '超时必须是不小于 1 且不超过 30 的整数。';
  }
  return null;
}

/** 目标地址摘要:域名 + 路径(超长由 CSS 截断,完整值放 title)。 */
export function summarizeTarget(target: string): string {
  try {
    const url = new URL(target);
    const path = url.pathname === '/' ? '' : url.pathname;
    return url.host + path;
  } catch {
    return target;
  }
}

export function useHooksModel() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<HookForm>(EMPTY_FORM);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [secret, setSecret] = useState<HookSecretState | null>(null);
  const [copied, setCopied] = useState(false);

  const query = useQuery({ queryKey: queryKeys.hooks, queryFn: hooksApi.list });
  const hooks = query.data ?? [];
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.hooks });

  const toSecretState = (hook: HookSecret): HookSecretState => ({
    hookId: hook.id,
    event: hook.event,
    target: hook.target,
    secret: hook.secret,
  });

  const createMutation = useMutation({
    mutationFn: (input: HookCreateInput) => hooksApi.create(input),
    onSuccess: async (created) => {
      setSecret(toSecretState(created));
      setValidationError(null);
      // 保留事件选择,方便连续给多个事件加 hook
      setForm((prev) => ({ ...EMPTY_FORM, event: prev.event }));
      await refresh();
    },
  });

  const rotateMutation = useMutation({
    mutationFn: (hookId: string) => hooksApi.rotate(hookId),
    onSuccess: async (rotated) => {
      setSecret(toSecretState(rotated));
      await refresh();
    },
  });

  const toggleMutation = useMutation({
    mutationFn: (input: { hookId: string; enabled: boolean }) =>
      hooksApi.update(input.hookId, { enabled: input.enabled }),
    onSuccess: async () => {
      await refresh();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (hookId: string) => hooksApi.remove(hookId),
    onSuccess: async () => {
      await refresh();
    },
  });

  const updateForm = (patch: Partial<HookForm>) => {
    setForm((prev) => ({ ...prev, ...patch }));
    setValidationError(null);
  };

  const submit = () => {
    const validation = validateHookForm(form);
    if (validation) {
      setValidationError(validation);
      return;
    }
    createMutation.mutate({
      event: form.event,
      matcher: form.matcher.trim() || '*',
      target: form.target.trim(),
      timeout_seconds: Number(form.timeout),
    });
  };

  const toggleHook = (hook: Hook, enabled: boolean) =>
    toggleMutation.mutate({ hookId: hook.id, enabled });
  const deleteHook = (hookId: string) => deleteMutation.mutate(hookId);
  const rotateHook = (hookId: string) => rotateMutation.mutate(hookId);

  const copySecret = async () => {
    if (!secret) return;
    try {
      await navigator.clipboard?.writeText(secret.secret);
    } finally {
      // 非安全上下文没有剪贴板权限时也提示,明文仍可手动选中复制
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    }
  };

  const dismissSecret = () => {
    setSecret(null);
    setCopied(false);
  };

  const disabled =
    isDisabledError(query.error) ||
    [
      createMutation.error,
      rotateMutation.error,
      toggleMutation.error,
      deleteMutation.error,
    ].some(isDisabledError);

  return {
    hooks,
    loading: query.isPending,
    loadError: query.error,
    refetch: () => void query.refetch(),
    disabled,
    form,
    updateForm,
    submit,
    validationError,
    createError: createMutation.error,
    createPending: createMutation.isPending,
    rotateError: rotateMutation.error,
    rotatePending: rotateMutation.isPending,
    toggleError: toggleMutation.error,
    toggling: toggleMutation.isPending,
    deleteError: deleteMutation.error,
    deleting: deleteMutation.isPending,
    toggleHook,
    deleteHook,
    rotateHook,
    secret,
    copied,
    copySecret,
    dismissSecret,
  };
}

export type HooksModel = ReturnType<typeof useHooksModel>;
