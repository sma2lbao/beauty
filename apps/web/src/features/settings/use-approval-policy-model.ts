/**
 * 工具审批策略的模型层(P1-A §6/§7.1)。
 *
 * 服务端只提供「读整表 / 写整表」,所以编辑一律在本地草稿上进行:
 * draft 为 null 表示跟随服务端,首次新增/删除才物化草稿;
 * 保存时把草稿整表 PUT 回去(删行也是整表),成功后丢弃草稿并失效查询重取。
 * 在本地先按 §6.2 校验长度/空值/唯一键,不合法不发请求。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';

import {
  approvalApi,
  type ApprovalAction,
  type ApprovalArgMatch,
  type ApprovalPolicy,
  type ApprovalPolicyInput,
} from '@/lib/approval-api';
import { queryKeys } from '@/lib/query-keys';

export type ApprovalDraft = {
  id: string;
  tool: string;
  action: ApprovalAction;
  argName: string;
  argMatch: ApprovalArgMatch | '';
  argValue: string;
};

export type ApprovalForm = {
  tool: string;
  action: ApprovalAction;
  argName: string;
  argMatch: ApprovalArgMatch;
  argValue: string;
};

export const ARG_MATCH_VALUES: ApprovalArgMatch[] = ['exact', 'prefix', 'glob'];

const EMPTY_FORM: ApprovalForm = {
  tool: '',
  action: 'confirm',
  argName: '',
  argMatch: 'exact',
  argValue: '',
};

function toDraft(row: ApprovalPolicy, id: string): ApprovalDraft {
  return {
    id,
    tool: row.tool,
    action: row.action,
    argName: row.arg_name,
    argMatch: row.arg_match,
    argValue: row.arg_value,
  };
}

/** 参数三列全空时后端只认 (tool):空串键必须整组省略。 */
function toInput(row: ApprovalDraft): ApprovalPolicyInput {
  const tool = row.tool.trim();
  if (!row.argName.trim()) return { tool, action: row.action };
  return {
    tool,
    action: row.action,
    arg_name: row.argName.trim(),
    arg_match: row.argMatch as ApprovalArgMatch,
    arg_value: row.argValue.trim(),
  };
}

/** 唯一键与后端一致:(tool, arg_name, arg_match, arg_value)。 */
function dedupeKey(row: {
  tool: string;
  argName: string;
  argMatch: string;
  argValue: string;
}): string {
  return [
    row.tool.trim(),
    row.argName.trim(),
    row.argMatch,
    row.argValue.trim(),
  ].join('\u0000');
}

export function validateDraft(
  row: ApprovalDraft,
  others: ApprovalDraft[],
): string | null {
  const tool = row.tool.trim();
  if (!tool) return '工具名不能为空。';
  if (tool.length > 120) return '工具名不能超过 120 个字符。';
  const argName = row.argName.trim();
  if (argName.length > 64) return '参数名不能超过 64 个字符。';
  if (argName) {
    if (!ARG_MATCH_VALUES.includes(row.argMatch as ApprovalArgMatch)) {
      return '填了参数名就必须选择匹配方式。';
    }
    if (!row.argValue.trim()) return '填了参数名就必须填参数值。';
    if (row.argValue.trim().length > 512) {
      return '参数值不能超过 512 个字符。';
    }
  }
  const duplicated = others.some(
    (other) => dedupeKey(other) === dedupeKey(row),
  );
  if (duplicated) return '规则重复:同一工具与参数条件只能有一条。';
  return null;
}

export function useApprovalPolicyModel() {
  const queryClient = useQueryClient();
  const nextId = useRef(1);
  const [draft, setDraft] = useState<ApprovalDraft[] | null>(null);
  const [form, setForm] = useState<ApprovalForm>(EMPTY_FORM);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const query = useQuery({
    queryKey: queryKeys.approvalPolicies,
    queryFn: approvalApi.list,
  });
  const rows =
    draft ??
    (query.data ?? []).map((row, index) => toDraft(row, `server:${index}`));

  const replaceMutation = useMutation({
    mutationFn: (policies: ApprovalPolicyInput[]) =>
      approvalApi.replace(policies),
    onSuccess: async () => {
      setDraft(null);
      setValidationError(null);
      setSaved(true);
      await queryClient.invalidateQueries({
        queryKey: queryKeys.approvalPolicies,
      });
    },
  });

  const updateForm = (patch: Partial<ApprovalForm>) => {
    setSaved(false);
    setForm((current) => ({ ...current, ...patch }));
  };

  const addRow = (): boolean => {
    const hasArgs = form.argName.trim().length > 0;
    const row: ApprovalDraft = {
      id: `local:${nextId.current++}`,
      tool: form.tool.trim(),
      action: form.action,
      argName: form.argName.trim(),
      argMatch: hasArgs ? form.argMatch : '',
      argValue: hasArgs ? form.argValue.trim() : '',
    };
    const message = validateDraft(row, rows);
    if (message) {
      setValidationError(message);
      return false;
    }
    setValidationError(null);
    setSaved(false);
    setDraft([...rows, row]);
    setForm(EMPTY_FORM);
    return true;
  };

  const removeRow = (id: string) => {
    setValidationError(null);
    setSaved(false);
    setDraft(rows.filter((row) => row.id !== id));
  };

  const save = () => {
    for (const row of rows) {
      const message = validateDraft(
        row,
        rows.filter((candidate) => candidate.id !== row.id),
      );
      if (message) {
        setValidationError(message);
        return;
      }
    }
    setValidationError(null);
    setSaved(false);
    replaceMutation.mutate(rows.map(toInput));
  };

  /** 放弃本地草稿,回到服务端当前值。 */
  const discard = () => {
    setDraft(null);
    setForm(EMPTY_FORM);
    setValidationError(null);
    setSaved(false);
    replaceMutation.reset();
  };

  return {
    rows,
    form,
    updateForm,
    addRow,
    removeRow,
    save,
    discard,
    dirty: draft !== null,
    loading: query.isPending,
    loadError: query.error,
    refetch: () => void query.refetch(),
    saving: replaceMutation.isPending,
    saveError: replaceMutation.error,
    validationError,
    saved,
  };
}

export type ApprovalPolicyModel = ReturnType<typeof useApprovalPolicyModel>;
