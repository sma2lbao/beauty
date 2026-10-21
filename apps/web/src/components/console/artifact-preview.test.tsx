import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { ArtifactPreview } from './artifact-preview';

const PNG = 'data:image/png;base64,iVBORw0KGgo=';

describe('ArtifactPreview', () => {
  it('渲染内联图片', () => {
    render(
      <ArtifactPreview
        artifact={{ title: '截图', kind: 'image', content: PNG }}
      />,
    );

    const image = screen.getByRole('img', { name: '截图' });
    expect(image).toHaveAttribute('src', PNG);
  });

  it('优先使用 src(下载端点取回的 blob URL)渲染图片', () => {
    render(
      <ArtifactPreview
        artifact={{
          title: '截图',
          kind: 'attachment',
          filename: 'screen.png',
          content_type: 'image/png',
        }}
        src="blob:http://localhost/abc"
      />,
    );

    const image = screen.getByRole('img', { name: '截图' });
    expect(image).toHaveAttribute('src', 'blob:http://localhost/abc');
  });

  it('二进制文件没有正文时提示下载', () => {
    render(
      <ArtifactPreview
        artifact={{
          title: '报告',
          kind: 'file',
          filename: 'weekly.pdf',
          content_type: 'application/pdf',
        }}
      />,
    );
    expect(screen.getByText(/请下载后查看/)).toBeInTheDocument();
  });

  it('渲染 Markdown', () => {
    render(
      <ArtifactPreview
        artifact={{ title: '周报', kind: 'markdown', content: '# 本周周报' }}
      />,
    );
    expect(
      screen.getByRole('heading', { name: '本周周报' }),
    ).toBeInTheDocument();
  });

  it('文本回退到代码预览', () => {
    render(
      <ArtifactPreview
        artifact={{ title: 'a.ts', kind: 'code', content: 'const a = 1;' }}
      />,
    );
    expect(screen.getByText('const a = 1;')).toBeInTheDocument();
  });
});
