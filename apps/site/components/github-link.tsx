import { GithubMark } from '@/components/icons/github-mark';
import { GITHUB_URL } from '@/components/link';

/**
 * 页头的 GitHub 入口:描边图标按钮,统一在新窗口打开源码仓库。
 * 官网两套外壳(落地页 / 内页 SiteShell)共用,避免 href 与无障碍名各写一份。
 * 使用描边而非裸图标,是为了和右侧实心主按钮落在同一套控件语言里:
 * 同高、同圆角、有可悬停的表面,两者是并列控件而非图标紧贴按钮。
 */
export function GithubLink({ className = '' }: { className?: string }) {
  return (
    <a
      href={GITHUB_URL}
      target="_blank"
      rel="noreferrer"
      aria-label="GitHub 源码仓库"
      title="GitHub · sma2lbao/beauty"
      className={`inline-flex h-8 w-8 items-center justify-center rounded-md border border-ha-border-strong text-ha-text-2 transition-colors hover:border-ha-ghost hover:bg-ha-panel hover:text-ha-text ${className}`.trim()}
    >
      <GithubMark size={20} />
    </a>
  );
}
