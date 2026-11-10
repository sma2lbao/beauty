import { Button, Field, Input } from '@beauty/design';
import { useState } from 'react';

import { ConsoleDialog } from '@/components/console/dialog';
import type { AdminAccount, QuotaPatch } from '@/lib/admin-api';

/** 空/缺省 = 不限(清除);否则必须是 >= 1 的整数。 */
function parseLimit(raw: string): { value: number | null; invalid: boolean } {
  const trimmed = raw.trim();
  if (trimmed === '') {
    return { value: null, invalid: false };
  }
  if (!/^\d+$/.test(trimmed)) {
    return { value: null, invalid: true };
  }
  const value = Number(trimmed);
  if (!Number.isSafeInteger(value) || value < 1) {
    return { value: null, invalid: true };
  }
  return { value, invalid: false };
}

/**
 * 配额编辑弹窗:两个数字框都留空表示"不限"(PATCH 显式 null)。
 * 提交的字段一律带 null/整数,后端对 null 的语义就是清除配额。
 */
export function AdminQuotaDialog({
  account,
  pending,
  error,
  onClose,
  onSubmit,
}: {
  account: AdminAccount;
  pending: boolean;
  error: unknown;
  onClose: () => void;
  onSubmit: (patch: QuotaPatch) => void;
}) {
  const [tokens, setTokens] = useState(
    account.quota.monthly_tokens == null ? '' : String(account.quota.monthly_tokens),
  );
  const [runs, setRuns] = useState(
    account.quota.max_concurrent_runs == null
      ? ''
      : String(account.quota.max_concurrent_runs),
  );
  const [invalid, setInvalid] = useState(false);
  const tokensLeft = parseLimit(tokens);
  const runsLeft = parseLimit(runs);

  return (
    <ConsoleDialog
      open
      title="编辑配额"
      description={account.email}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" size="sm" disabled={pending} onClick={onClose}>
            取消
          </Button>
          <Button
            variant="primary"
            size="sm"
            disabled={pending}
            onClick={() => {
              if (tokensLeft.invalid || runsLeft.invalid) {
                setInvalid(true);
                return;
              }
              setInvalid(false);
              onSubmit({
                monthly_tokens: tokensLeft.value,
                max_concurrent_runs: runsLeft.value,
              });
            }}
          >
            {pending ? '提交中…' : '保存配额'}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="每月 tokens(留空表示不限)" htmlFor="admin-quota-tokens">
          <Input
            id="admin-quota-tokens"
            inputMode="numeric"
            placeholder="不限"
            value={tokens}
            onChange={(event) => setTokens(event.target.value)}
          />
        </Field>
        <Field label="最大并发 run(留空表示不限)" htmlFor="admin-quota-runs">
          <Input
            id="admin-quota-runs"
            inputMode="numeric"
            placeholder="不限"
            value={runs}
            onChange={(event) => setRuns(event.target.value)}
          />
        </Field>
        {invalid ? (
          <div role="alert" className="text-[12px] text-ha-danger">
            配额必须是 &gt;= 1 的整数,或留空表示不限。
          </div>
        ) : null}
        {error ? (
          <div role="alert" className="text-[12px] text-ha-danger">
            保存失败:{(error as Error).message}
          </div>
        ) : null}
      </div>
    </ConsoleDialog>
  );
}
