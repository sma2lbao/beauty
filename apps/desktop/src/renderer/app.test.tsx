import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { App } from '@/app';
import { installFakeBridge, removeFakeBridge } from '@/test-utils/fake-bridge';

afterEach(() => {
  removeFakeBridge();
});

describe('App 启动与模式切换', () => {
  it('没有桌面桥接时显示「请通过桌面应用启动」而不是白屏', () => {
    removeFakeBridge();
    render(<App />);
    expect(screen.getByText('请通过桌面应用启动')).toBeInTheDocument();
  });

  it('读取设置后进入本地模式,切换到云端会调用 settings.update', async () => {
    const fake = installFakeBridge();
    render(<App />);
    await screen.findByText('最近会话');

    fireEvent.click(screen.getByRole('button', { name: '云端' }));

    await waitFor(() =>
      expect(fake.mocks.settingsUpdate).toHaveBeenCalledWith({ mode: 'cloud' }),
    );
    expect(
      await screen.findByRole('heading', { name: '云端模式' }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '本地电脑' }));
    await waitFor(() =>
      expect(fake.mocks.settingsUpdate).toHaveBeenLastCalledWith({
        mode: 'local',
      }),
    );
    expect(await screen.findByText('最近会话')).toBeInTheDocument();
  });

  it('云端页的「打开云端控制台」走 cloud.openConsole', async () => {
    const fake = installFakeBridge({ settings: { mode: 'cloud' } });
    render(<App />);
    await screen.findByRole('heading', { name: '云端模式' });

    const buttons = screen.getAllByRole('button', {
      name: '打开云端控制台',
    });
    fireEvent.click(buttons[0]);

    await waitFor(() => expect(fake.mocks.cloudOpenConsole).toHaveBeenCalled());
  });
});
