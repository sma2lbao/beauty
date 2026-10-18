/**
 * 产物类型识别与下载(M0-10)。
 *
 * 当前没有对象存储/BFF 下载端点:产物正文由 workspace API 带鉴权取回后,
 * 前端按 kind/内容类型生成 Blob 下载。图片仅接受 data:image/* 内联内容,
 * 不渲染远程 URL(避免 SSRF/隐私与混合内容),非图片一律回退文本。
 */

const IMAGE_MIME = /^data:(image\/(?:png|jpe?g|webp|gif));base64,([A-Za-z0-9+/=\s]+)$/i;

const IMAGE_EXTENSIONS: Record<string, string> = {
  'image/png': 'png',
  'image/jpeg': 'jpg',
  'image/webp': 'webp',
  'image/gif': 'gif',
};

export type ArtifactLike = {
  title: string;
  kind: string;
  content: string;
};

/** 从 data URL 里取图片 MIME;不是合法内联图片返回 null。 */
export function imageMimeOf(content: string): string | null {
  const match = IMAGE_MIME.exec(content.trim());
  if (!match) return null;
  return match[1].toLowerCase() === 'image/jpg' ? 'image/jpeg' : match[1].toLowerCase();
}

export function isImageArtifact(artifact: Pick<ArtifactLike, 'kind' | 'content'>): boolean {
  return Boolean(imageMimeOf(artifact.content));
}

export function isMarkdownArtifact(kind: string): boolean {
  const normalized = kind.toLowerCase();
  return normalized === 'markdown' || normalized === 'md' || normalized === 'text/markdown';
}

export function artifactContentType(kind: string): string {
  const normalized = kind.toLowerCase();
  if (normalized.startsWith('image/')) return normalized;
  if (normalized === 'image') return 'image/png';
  if (isMarkdownArtifact(normalized)) return 'text/markdown';
  if (normalized === 'diff' || normalized === 'patch') return 'text/x-patch';
  if (normalized === 'json') return 'application/json';
  if (normalized === 'html') return 'text/plain';
  return 'text/plain';
}

/** 下载用的扩展名:优先保留标题里的扩展名,否则按 kind 映射。 */
export function artifactExtension(artifact: Pick<ArtifactLike, 'title' | 'kind' | 'content'>): string {
  const imageMime = imageMimeOf(artifact.content);
  if (imageMime) return IMAGE_EXTENSIONS[imageMime] ?? 'png';
  const titleExtension = /\.([A-Za-z0-9]{1,8})$/.exec(artifact.title)?.[1];
  if (titleExtension) return titleExtension.toLowerCase();
  const normalized = artifact.kind.toLowerCase();
  if (isMarkdownArtifact(normalized)) return 'md';
  if (normalized === 'diff' || normalized === 'patch') return 'patch';
  if (normalized === 'json') return 'json';
  return 'txt';
}

/** 清理文件名里的路径/非法字符,并补齐扩展名。 */
export function artifactFileName(artifact: Pick<ArtifactLike, 'title' | 'kind' | 'content'>): string {
  const extension = artifactExtension(artifact);
  const base = artifact.title
    .replace(/[\\/:*?"<>|]/g, '_')
    .replace(new RegExp('\\.' + extension + '$', 'i'), '')
    .trim() || 'artifact';
  return base + '.' + extension;
}

function base64ToArrayBuffer(payload: string): ArrayBuffer {
  const binary = atob(payload.replace(/\s/g, ''));
  const buffer = new ArrayBuffer(binary.length);
  const bytes = new Uint8Array(buffer);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return buffer;
}

function triggerDownload(url: string, fileName: string): void {
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = fileName;
  anchor.rel = 'noopener';
  anchor.click();
}

/** 下载产物:图片走二进制 Blob,其余按文本类型落盘。 */
export function downloadArtifact(artifact: ArtifactLike): void {
  const imageMime = imageMimeOf(artifact.content);
  const fileName = artifactFileName(artifact);
  try {
    let blob: Blob;
    if (imageMime) {
      const payload = artifact.content.trim().slice(artifact.content.indexOf(',') + 1);
      blob = new Blob([base64ToArrayBuffer(payload)], { type: imageMime });
    } else {
      blob = new Blob([artifact.content], {
        type: artifactContentType(artifact.kind) + ';charset=utf-8',
      });
    }
    const url = URL.createObjectURL(blob);
    triggerDownload(url, fileName);
    URL.revokeObjectURL(url);
  } catch {
    // 非浏览器环境(测试/SSR)或 Blob 不可用时静默;不阻塞页面。
  }
}
