import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  artifactContentType,
  artifactExtension,
  artifactFileName,
  downloadArtifact,
  imageMimeOf,
  isMarkdownArtifact,
} from './artifacts';

const PNG = 'data:image/png;base64,iVBORw0KGgo=';

afterEach(() => {
  vi.restoreAllMocks();
});

describe('artifact helpers', () => {
  it('识别内联图片并忽略远程 URL/非法内容', () => {
    expect(imageMimeOf(PNG)).toBe('image/png');
    expect(imageMimeOf('data:image/jpeg;base64,AAAA')).toBe('image/jpeg');
    expect(imageMimeOf('https://example.com/a.png')).toBeNull();
    expect(imageMimeOf('data:text/html;base64,AAAA')).toBeNull();
  });

  it('按 kind/标题推断类型、扩展名与文件名', () => {
    expect(isMarkdownArtifact('markdown')).toBe(true);
    expect(isMarkdownArtifact('diff')).toBe(false);
    expect(artifactContentType('markdown')).toBe('text/markdown');
    expect(artifactContentType('diff')).toBe('text/x-patch');
    expect(artifactContentType('html')).toBe('text/plain');

    const image = { title: '截图', kind: 'image', content: PNG };
    expect(artifactExtension(image)).toBe('png');
    expect(artifactFileName(image)).toBe('截图.png');

    const markdown = { title: '周报.md', kind: 'markdown', content: '# 周报' };
    expect(artifactExtension(markdown)).toBe('md');
    expect(artifactFileName(markdown)).toBe('周报.md');

    const patch = { title: 'a/b.patch', kind: 'diff', content: '@@ -1 +1 @@' };
    expect(artifactFileName(patch)).toBe('a_b.patch');
  });

  it('下载图片走二进制 Blob,Markdown 走文本 Blob', () => {
    const createObjectURL = vi
      .spyOn(URL, 'createObjectURL')
      .mockReturnValue('blob:mock');
    const revokeObjectURL = vi
      .spyOn(URL, 'revokeObjectURL')
      .mockImplementation(() => {});
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => {});

    downloadArtifact({ title: '截图', kind: 'image', content: PNG });
    expect(createObjectURL).toHaveBeenCalledOnce();
    const imageBlob = createObjectURL.mock.calls[0][0] as Blob;
    expect(imageBlob.type).toBe('image/png');
    expect(click).toHaveBeenCalledOnce();

    downloadArtifact({ title: '周报', kind: 'markdown', content: '# 周报' });
    const markdownBlob = createObjectURL.mock.calls[1][0] as Blob;
    expect(markdownBlob.type).toContain('text/markdown');
    expect(revokeObjectURL).toHaveBeenCalledTimes(2);
  });
});
