import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { App } from '@/app';
import { installFakeBridge, removeFakeBridge } from '@/test-utils/fake-bridge';

afterEach(() => {
  removeFakeBridge();
});

describe('设置页', () => {
  it('保存模型设置调用 settings.update 并展示保存成功', async () => {
    const fake = installFakeBridge();
    render(<App />);

    await screen.findByText('最近会话');
    fireEvent.click(screen.getByRole('button', { name: '设置' }));
    expect(
      await screen.findByRole('heading', { name: '设置' }),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Base URL'), {
      target: { value: 'https://api.example.com/v1' },
    });
    fireEvent.change(screen.getByLabelText('模型'), {
      target: { value: 'qwen2.5-coder' },
    });
    fireEvent.change(screen.getByLabelText('API Key'), {
      target: { value: 'sk-test' },
    });
    fireEvent.change(screen.getByLabelText('控制台地址'), {
      target: { value: 'https://console.example.com/app' },
    });

    fireEvent.click(screen.getByRole('button', { name: '保存设置' }));

    await waitFor(() =>
      expect(fake.mocks.settingsUpdate).toHaveBeenCalledWith({
        model: {
          baseUrl: 'https://api.example.com/v1',
          model: 'qwen2.5-coder',
          apiKey: 'sk-test',
        },
        cloudConsoleUrl: 'https://console.example.com/app',
      }),
    );
    expect(await screen.findByText('设置已保存')).toBeInTheDocument();
  });

  it('Base URL 非法时不提交,并给出可读错误', async () => {
    const fake = installFakeBridge();
    render(<App />);

    await screen.findByText('最近会话');
    fireEvent.click(screen.getByRole('button', { name: '设置' }));
    await screen.findByRole('heading', { name: '设置' });

    fireEvent.change(screen.getByLabelText('Base URL'), {
      target: { value: 'api.example.com' },
    });
    fireEvent.click(screen.getByRole('button', { name: '保存设置' }));

    expect(
      await screen.findByText('Base URL 需要以 http:// 或 https:// 开头'),
    ).toBeInTheDocument();
    expect(fake.mocks.settingsUpdate).not.toHaveBeenCalled();
  });
});
