import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { App } from '@/app';
import {
  installFakeBridge,
  makeFile,
  removeFakeBridge,
} from '@/test-utils/fake-bridge';

afterEach(() => {
  removeFakeBridge();
});

describe('文件页', () => {
  it('渲染目录树,展开目录懒加载,点击文件读取内容', async () => {
    const fake = installFakeBridge({
      directories: {
        '': [makeFile('README.md'), makeFile('src', 'directory', 0)],
        src: [makeFile('src/index.ts')],
      },
      contents: {
        'README.md': {
          path: 'README.md',
          content: '# 示例项目',
          size: 1024,
          truncated: false,
        },
      },
    });

    render(<App />);
    await screen.findByText('最近会话');
    fireEvent.click(screen.getByRole('button', { name: '文件' }));

    expect(await screen.findByText('README.md')).toBeInTheDocument();
    expect(screen.getByText('工作区文件')).toBeInTheDocument();

    // 展开目录才请求子项
    expect(fake.mocks.workspaceList).toHaveBeenCalledWith('');
    fireEvent.click(screen.getByText('src'));
    expect(await screen.findByText('index.ts')).toBeInTheDocument();
    expect(fake.mocks.workspaceList).toHaveBeenCalledWith('src');

    fireEvent.click(screen.getByText('README.md'));
    await waitFor(() =>
      expect(fake.mocks.workspaceRead).toHaveBeenCalledWith('README.md'),
    );
    expect(await screen.findByText('# 示例项目')).toBeInTheDocument();
  });

  it('未选工作区时给出选择目录引导', async () => {
    installFakeBridge({ workspace: null, settings: { workspaceRoot: null } });
    render(<App />);
    await screen.findByText('最近会话');
    fireEvent.click(screen.getByRole('button', { name: '文件' }));

    expect(
      await screen.findByText(
        '还没有选择本地工作区。选择目录后可以在这里浏览文件并预览内容。',
      ),
    ).toBeInTheDocument();
  });
});
