/**
 * 产物预览:图片(blob URL 或内联 data URL)/ Markdown / 纯文本代码回退。
 *
 * 新的下载端点是带鉴权的二进制接口,图片要先取 Blob 再 `URL.createObjectURL`,
 * 因此组件既接受 `src`(`<img>` 源),也接受旧的 `content`(内联正文)。
 * PDF/Office 等二进制暂不做内嵌预览,提示用户下载。
 */
import { CodePreview } from '@/components/console/diff-view';
import { Markdown } from '@/components/console/markdown';
import {
  artifactContentType,
  imageMimeOf,
  isMarkdownArtifact,
  type ArtifactLike,
} from '@/lib/artifacts';
import { cn } from '@/lib/utils';

export function ArtifactPreview({
  artifact,
  src,
  className,
}: {
  artifact: ArtifactLike;
  /** 图片的 object URL(由 Blob 生成);有值时优先于内联 data URL。 */
  src?: string | null;
  className?: string;
}) {
  const inlineImage = imageMimeOf(artifact.content);
  const imageSrc = src ?? (inlineImage ? artifact.content?.trim() : null);
  if (imageSrc) {
    return (
      <div
        className={cn(
          'flex max-h-[28rem] items-center justify-center overflow-auto rounded-md border border-ha-border bg-ha-code p-3',
          className,
        )}
      >
        <img
          src={imageSrc}
          alt={artifact.title}
          className="max-h-[26rem] max-w-full object-contain"
        />
      </div>
    );
  }

  const contentType = artifactContentType(artifact);
  if (isMarkdownArtifact(artifact.kind) || contentType === 'text/markdown') {
    if (!artifact.content) {
      return <UnavailablePreview className={className} />;
    }
    return (
      <div
        className={cn(
          'max-h-[28rem] overflow-auto rounded-md border border-ha-border bg-ha-panel px-4 py-2',
          className,
        )}
      >
        <Markdown content={artifact.content} />
      </div>
    );
  }

  if (!artifact.content) {
    return <UnavailablePreview className={className} />;
  }

  return (
    <div
      className={cn(
        'max-h-[28rem] overflow-auto rounded-md border border-ha-border bg-ha-code py-2',
        className,
      )}
    >
      <CodePreview content={artifact.content} numbers />
    </div>
  );
}

/** 二进制产物(截图以外的文件)没有正文:提示下载,不渲染空白预览。 */
function UnavailablePreview({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        'rounded-md border border-ha-border bg-ha-panel px-4 py-6 text-center text-sm text-ha-muted',
        className,
      )}
    >
      该文件不支持在线预览，请下载后查看。
    </div>
  );
}
