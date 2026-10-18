/**
 * 产物预览:图片(data URL)/ Markdown / 纯文本代码回退。
 * PDF/Office 暂不支持(对象存储后置),会以纯文本回退并提示下载。
 */
import { CodePreview } from '@/components/console/diff-view';
import { Markdown } from '@/components/console/markdown';
import {
  imageMimeOf,
  isMarkdownArtifact,
  type ArtifactLike,
} from '@/lib/artifacts';
import { cn } from '@/lib/utils';

export function ArtifactPreview({
  artifact,
  className,
}: {
  artifact: ArtifactLike;
  className?: string;
}) {
  const imageMime = imageMimeOf(artifact.content);
  if (imageMime) {
    return (
      <div
        className={cn(
          'flex max-h-[28rem] items-center justify-center overflow-auto rounded-md border border-ha-border bg-ha-code p-3',
          className,
        )}
      >
        <img
          src={artifact.content.trim()}
          alt={artifact.title}
          className="max-h-[26rem] max-w-full object-contain"
        />
      </div>
    );
  }

  if (isMarkdownArtifact(artifact.kind)) {
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
