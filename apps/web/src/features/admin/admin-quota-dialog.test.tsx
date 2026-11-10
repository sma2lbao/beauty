import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { AdminAccount } from '@/lib/admin-api';

import { AdminQuotaDialog } from './admin-quota-dialog';

const account: AdminAccount = {
  id: 'a'.repeat(32),
  email: 'ops@example.com',
  display_name: '运营',
  status: 'active',
  role: 'user',
  quota: { monthly_tokens: 1000, max_concurrent_runs: 2 },
  created_at: '2026-10-13T00:00:00+00:00',
  usage: {
    month_tokens: 1234,
    month_events: 5,
    month_cost_usd: 0.42,
    unpriced_events: 0,
  },
};

function renderDialog(
  onSubmit = vi.fn(),
  overrides: Partial<AdminAccount> = {},
) {
  const onClose = vi.fn();
  render(
    <AdminQuotaDialog
      account={{ ...account, ...overrides }}
      pending={false}
      error={null}
      onClose={onClose}
      onSubmit={onSubmit}
    />,
  );
  return { onClose, onSubmit };
}

function submit() {
  fireEvent.click(screen.getByRole('button', { name: '保存配额' }));
}

describe('AdminQuotaDialog 配额编辑', () => {
  it('预填现有配额,原样保存时两个字段都是整数', () => {
    const { onSubmit } = renderDialog();

    expect(screen.getByLabelText('每月 tokens(留空表示不限)')).toHaveValue(
      '1000',
    );
    expect(
      screen.getByLabelText('最大并发 run(留空表示不限)'),
    ).toHaveValue('2');

    submit();
    expect(onSubmit).toHaveBeenCalledWith({
      monthly_tokens: 1000,
      max_concurrent_runs: 2,
    });
  });

  it('清空两个输入表示不限,提交 null', () => {
    const { onSubmit } = renderDialog();
    fireEvent.change(screen.getByLabelText('每月 tokens(留空表示不限)'), {
      target: { value: '' },
    });
    fireEvent.change(screen.getByLabelText('最大并发 run(留空表示不限)'), {
      target: { value: '  ' },
    });

    submit();
    expect(onSubmit).toHaveBeenCalledWith({
      monthly_tokens: null,
      max_concurrent_runs: null,
    });
  });

  it('非法输入(0 / 负数 / 小数)就地报错,不提交', () => {
    const { onSubmit } = renderDialog();
    fireEvent.change(screen.getByLabelText('每月 tokens(留空表示不限)'), {
      target: { value: '0' },
    });

    submit();
    expect(onSubmit).not.toHaveBeenCalled();
    expect(
      screen.getByText('配额必须是 >= 1 的整数,或留空表示不限。'),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('每月 tokens(留空表示不限)'), {
      target: { value: '1.5' },
    });
    submit();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('服务端错误显示在弹窗内,供管理员重试', () => {
    render(
      <AdminQuotaDialog
        account={account}
        pending={false}
        error={new Error('配额只支持整数')}
        onClose={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByText('保存失败:配额只支持整数')).toBeInTheDocument();
  });
});
