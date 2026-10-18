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
