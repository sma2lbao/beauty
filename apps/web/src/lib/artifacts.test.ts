import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  artifactContentType,
  artifactExtension,
  artifactFileName,
  downloadArtifact,
  imageMimeOf,
  isImageArtifact,
  isMarkdownArtifact,
  isTextArtifact,
  saveBlob,
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

  it('后端 filename/content_type 优先于 kind/正文兜底', () => {
    const pdf = {
      title: '交付报告',
      kind: 'file',
      filename: 'reports/weekly.pdf',
      content_type: 'application/pdf',
    };
    expect(artifactFileName(pdf)).toBe('weekly.pdf');
    expect(artifactExtension(pdf)).toBe('pdf');
    expect(artifactContentType(pdf)).toBe('application/pdf');
    expect(isTextArtifact(pdf)).toBe(false);

    const png = {
      title: '截图',
      kind: 'attachment',
      filename: 'screen.png',
      content_type: 'image/png',
    };
    expect(isImageArtifact(png)).toBe(true);
    expect(artifactContentType(png)).toBe('image/png');
    expect(artifactFileName(png)).toBe('screen.png');

    // 文件名没有扩展名时从 content_type 补
    const markdown = {
      title: '周报',
      kind: 'file',
      filename: 'weekly-report',
      content_type: 'text/markdown; charset=utf-8',
    };
    expect(artifactContentType(markdown)).toBe('text/markdown');
    expect(artifactFileName(markdown)).toBe('weekly-report');
    expect(isTextArtifact(markdown)).toBe(true);
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

  it('saveBlob 用下载端点取回的 Blob 落盘并释放 object URL', () => {
    const createObjectURL = vi
      .spyOn(URL, 'createObjectURL')
      .mockReturnValue('blob:download');
    const revokeObjectURL = vi
      .spyOn(URL, 'revokeObjectURL')
      .mockImplementation(() => {});
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => {});

    const blob = new Blob(['binary'], { type: 'application/pdf' });
    saveBlob(blob, 'weekly.pdf');

    expect(createObjectURL).toHaveBeenCalledWith(blob);
    expect(click).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:download');
  });

  it('没有正文的产物不构造下载(交给下载端点)', () => {
    const createObjectURL = vi.spyOn(URL, 'createObjectURL');
    downloadArtifact({ title: '截图', kind: 'image', filename: 'a.png' });
    expect(createObjectURL).not.toHaveBeenCalled();
  });
});
