import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { approvalApi, type ApprovalPolicy } from '@/lib/approval-api';

import { useApprovalPolicyModel } from './use-approval-policy-model';

vi.mock('@/lib/approval-api', async () => {
  const actual =
    await vi.importActual<typeof import('@/lib/approval-api')>(
      '@/lib/approval-api',
    );
  return {
    ...actual,
    approvalApi: { list: vi.fn(), replace: vi.fn() },
  };
});

const toolLevel: ApprovalPolicy = {
  tool: 'exec',
  action: 'confirm',
  arg_name: '',
  arg_match: '',
  arg_value: '',
};

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(approvalApi.list).mockResolvedValue([]);
});

describe('useApprovalPolicyModel', () => {
  it('初值跟随服务端,dirty=false', async () => {
    vi.mocked(approvalApi.list).mockResolvedValue([toolLevel]);
    const { result } = renderHook(() => useApprovalPolicyModel(), { wrapper });

    await waitFor(() => expect(result.current.rows).toHaveLength(1));
    expect(result.current.rows[0]?.tool).toBe('exec');
    expect(result.current.dirty).toBe(false);
    expect(result.current.loading).toBe(false);
  });

  it('addRow 把表单转成草稿行,无参数名时不带参数三列', async () => {
    const { result } = renderHook(() => useApprovalPolicyModel(), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => result.current.updateForm({ tool: 'exec' }));
    let added = false;
    act(() => {
      added = result.current.addRow();
    });

    expect(added).toBe(true);
    expect(result.current.dirty).toBe(true);
    expect(result.current.rows[0]).toMatchObject({
      tool: 'exec',
      action: 'confirm',
      argName: '',
      argMatch: '',
      argValue: '',
    });
  });

  it('save 把草稿整表 PUT,成功后丢弃草稿并重取', async () => {
    vi.mocked(approvalApi.replace).mockResolvedValue([toolLevel]);
    const { result } = renderHook(() => useApprovalPolicyModel(), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => result.current.updateForm({ tool: 'exec' }));
    act(() => {
      result.current.addRow();
    });
    act(() => {
      result.current.save();
    });

    await waitFor(() => {
      expect(approvalApi.replace).toHaveBeenCalledWith([
        { tool: 'exec', action: 'confirm' },
      ]);
    });
    await waitFor(() => expect(approvalApi.list).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.dirty).toBe(false));
  });

  it('参数级草稿在 save 时带上三列', async () => {
    vi.mocked(approvalApi.replace).mockResolvedValue([]);
    const { result } = renderHook(() => useApprovalPolicyModel(), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() =>
      result.current.updateForm({
        tool: '*',
        argName: 'command',
        argMatch: 'glob',
        argValue: 'git *',
      }),
    );
    act(() => {
      result.current.addRow();
    });
    act(() => {
      result.current.save();
    });

    await waitFor(() => {
      expect(approvalApi.replace).toHaveBeenCalledWith([
        {
          tool: '*',
          action: 'confirm',
          arg_name: 'command',
          arg_match: 'glob',
          arg_value: 'git *',
        },
      ]);
    });
  });

  it('重复规则在 addRow 时被拦截', async () => {
    vi.mocked(approvalApi.list).mockResolvedValue([toolLevel]);
    const { result } = renderHook(() => useApprovalPolicyModel(), { wrapper });
    await waitFor(() => expect(result.current.rows).toHaveLength(1));

    act(() => result.current.updateForm({ tool: 'exec' }));
    let added = true;
    act(() => {
      added = result.current.addRow();
    });

    expect(added).toBe(false);
    expect(result.current.validationError).toMatch(/规则重复/);
    expect(result.current.dirty).toBe(false);
  });

  it('discard 丢弃草稿回到服务端值', async () => {
    vi.mocked(approvalApi.list).mockResolvedValue([toolLevel]);
    const { result } = renderHook(() => useApprovalPolicyModel(), { wrapper });
    await waitFor(() => expect(result.current.rows).toHaveLength(1));

    act(() => result.current.updateForm({ tool: 'other' }));
    act(() => {
      result.current.addRow();
    });
    expect(result.current.dirty).toBe(true);

    act(() => {
      result.current.discard();
    });

    expect(result.current.dirty).toBe(false);
    expect(result.current.rows).toHaveLength(1);
    expect(result.current.validationError).toBeNull();
  });
});
