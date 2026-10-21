/**
 * 产物类型识别与下载(M0-10 / task-14)。
 *
 * 交付物现在可以是真的文件(截图、PDF、Office 文档等):后端给出二进制下载端点,
 * 由 `requestBlob` 取回 Blob 后前端落盘。元数据里优先使用后端字段
 * `filename` / `content_type`;旧数据只有 `kind` + `content`(甚至内联 data URL)
 * 时按 kind/内容兜底。图片预览不再依赖 data URL,调用方用 Blob 生成 object URL
 * 后通过 `src` 传入 `ArtifactPreview`。
 */

const IMAGE_MIME = /^data:(image\/(?:png|jpe?g|webp|gif));base64,([A-Za-z0-9+/=\s]+)$/i;

/** 常见 MIME → 下载扩展名(后端没给 filename 时用)。 */
const MIME_EXTENSIONS: Record<string, string> = {
  'image/png': 'png',
  'image/jpeg': 'jpg',
  'image/webp': 'webp',
  'image/gif': 'gif',
  'image/svg+xml': 'svg',
  'text/markdown': 'md',
  'text/plain': 'txt',
  'text/csv': 'csv',
  'text/html': 'html',
  'text/x-patch': 'patch',
  'application/json': 'json',
  'application/pdf': 'pdf',
  'application/zip': 'zip',
};

const TEXT_KINDS = new Set([
  'text',
  'txt',
  'plain',
  'code',
  'diff',
  'patch',
  'json',
  'html',
  'markdown',
  'md',
  'csv',
  'log',
  'yaml',
  'yml',
  'ts',
  'tsx',
  'js',
  'jsx',
  'py',
  'sh',
]);

export type ArtifactLike = {
  title: string;
  kind: string;
  /** 文本正文(旧端点仍带;新多件列表只有元数据)。 */
  content?: string | null;
  /** 后端给出的文件名(优先于 title 推断)。 */
  filename?: string | null;
  /** 后端给出的 MIME(优先于 kind 推断)。 */
  content_type?: string | null;
};

function normalizeContentType(value?: string | null): string | null {
  const mime = value?.split(';')[0]?.trim().toLowerCase();
  if (!mime) return null;
  return mime === 'image/jpg' ? 'image/jpeg' : mime;
}

/** 从 data URL 里取图片 MIME;不是合法内联图片返回 null。 */
export function imageMimeOf(content?: string | null): string | null {
  if (!content) return null;
  const match = IMAGE_MIME.exec(content.trim());
  if (!match) return null;
  return normalizeContentType(match[1]);
}

export function isImageArtifact(
  artifact: Pick<ArtifactLike, 'kind' | 'content' | 'content_type'>,
): boolean {
  const contentType = normalizeContentType(artifact.content_type);
  if (contentType?.startsWith('image/')) return true;
  return Boolean(imageMimeOf(artifact.content));
}

export function isMarkdownArtifact(kindOrType: string): boolean {
  const normalized = kindOrType.toLowerCase();
  return (
    normalized === 'markdown' ||
    normalized === 'md' ||
    normalized === 'text/markdown'
  );
}

/** 文本类交付物可在线预览;图片走 blob URL,其余二进制只给下载。 */
export function isTextArtifact(
  artifact: Pick<ArtifactLike, 'kind' | 'content' | 'content_type'>,
): boolean {
  if (isImageArtifact(artifact)) return false;
  const contentType = normalizeContentType(artifact.content_type);
  if (contentType) {
    return (
      contentType.startsWith('text/') ||
      contentType === 'application/json' ||
      contentType === 'application/xml' ||
      contentType.endsWith('+json')
    );
  }
  if (artifact.content) return true;
  return TEXT_KINDS.has(artifact.kind.toLowerCase());
}

/**
 * 产物 MIME:优先后端 `content_type`,其次内联图片,最后按 kind 兜底。
 * 传字符串时按 kind 处理(兼容旧调用)。
 */
export function artifactContentType(artifact: ArtifactLike | string): string {
  if (typeof artifact !== 'string') {
    const backendType = normalizeContentType(artifact.content_type);
    if (backendType) return backendType;
    const imageMime = imageMimeOf(artifact.content);
    if (imageMime) return imageMime;
    return artifactContentType(artifact.kind);
  }
  const normalized = artifact.toLowerCase();
  if (normalized.startsWith('image/')) return normalized;
  if (normalized === 'image') return 'image/png';
  if (isMarkdownArtifact(normalized)) return 'text/markdown';
  if (normalized === 'diff' || normalized === 'patch') return 'text/x-patch';
  if (normalized === 'json') return 'application/json';
  if (normalized === 'html') return 'text/plain';
  return 'text/plain';
}

/** 下载用的扩展名:后端 filename → content_type → 内联图片 → 标题 → kind。 */
export function artifactExtension(
  artifact: Pick<
    ArtifactLike,
    'title' | 'kind' | 'content' | 'filename' | 'content_type'
  >,
): string {
  const backendExtension = /\.([A-Za-z0-9]{1,8})$/.exec(
    artifact.filename?.trim() ?? '',
  )?.[1];
  if (backendExtension) return backendExtension.toLowerCase();

  const contentType = normalizeContentType(artifact.content_type);
  if (contentType) {
    const mapped = MIME_EXTENSIONS[contentType];
    if (mapped) return mapped;
    const subtype = contentType.split('/')[1]?.replace(/[^a-z0-9]/g, '');
    if (subtype) return subtype.slice(0, 8);
  }

  const imageMime = imageMimeOf(artifact.content);
  if (imageMime) return MIME_EXTENSIONS[imageMime] ?? 'png';

  const titleExtension = /\.([A-Za-z0-9]{1,8})$/.exec(artifact.title)?.[1];
  if (titleExtension) return titleExtension.toLowerCase();

  const normalized = artifact.kind.toLowerCase();
  if (isMarkdownArtifact(normalized)) return 'md';
  if (normalized === 'diff' || normalized === 'patch') return 'patch';
  if (normalized === 'json') return 'json';
  if (normalized === 'image') return 'png';
  return 'txt';
}

function sanitizeFileName(value: string, fallback: string): string {
  const base = value.split(/[\\/]/).pop() ?? value;
  return base.replace(/[\\/:*?"<>|]/g, '_').trim() || fallback;
}

/** 下载文件名:优先后端 filename(已含扩展名),否则标题 + 推断扩展名。 */
export function artifactFileName(
  artifact: Pick<
    ArtifactLike,
    'title' | 'kind' | 'content' | 'filename' | 'content_type'
  >,
): string {
  const backendName = artifact.filename?.trim();
  if (backendName) {
    return sanitizeFileName(backendName, 'artifact');
  }
  const extension = artifactExtension(artifact);
  const base =
    artifact.title
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

/** 把已经取回的 Blob 落盘(下载端点 → Blob 后调用)。 */
export function saveBlob(blob: Blob, fileName: string): void {
  try {
    const url = URL.createObjectURL(blob);
    try {
      triggerDownload(url, fileName);
    } finally {
      URL.revokeObjectURL(url);
    }
  } catch {
    // 非浏览器环境(测试/SSR)或 Blob 不可用时静默;不阻塞页面。
  }
}

/** 旧端点/内联正文能构造出的 Blob;没有正文时返回 null。 */
export function artifactContentBlob(artifact: ArtifactLike): Blob | null {
  if (!artifact.content) return null;
  const imageMime = imageMimeOf(artifact.content);
  if (imageMime) {
    const payload = artifact.content
      .trim()
      .slice(artifact.content.indexOf(',') + 1);
    return new Blob([base64ToArrayBuffer(payload)], { type: imageMime });
  }
  return new Blob([artifact.content], {
    type: artifactContentType(artifact) + ';charset=utf-8',
  });
}

/** 从正文构造 Blob 下载(旧路径;新文件走下载端点的 `saveBlob`)。 */
export function downloadArtifact(artifact: ArtifactLike): void {
  const blob = artifactContentBlob(artifact);
  if (!blob) return;
  saveBlob(blob, artifactFileName(artifact));
}
