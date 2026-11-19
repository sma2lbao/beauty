/**
 * 社交登录入口(session 模式):整页跳转 API 网关的 OAuth start,
 * 回调落在 `/api/auth/oauth/github/callback`,成功后由网关种 Cookie 再回控制台。
 *
 * 目前只开放 GitHub;Google 按钮暂时隐藏(account-service 仍保留其能力,
 * 需要恢复时把 `<a>` 加回来即可)。
 */

import { oauthStartUrl } from '@/lib/auth';

/** GitHub 官方 mark(16×16),只取图形,颜色跟随文字。 */
function GitHubMark() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 16 16"
      className="h-4 w-4"
      fill="currentColor"
    >
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
    </svg>
  );
}

export function SocialLogin() {
  return (
    <div className="mt-5">
      <div className="mb-5 flex items-center gap-3 text-xs text-muted-foreground">
        <span className="h-px flex-1 bg-border" aria-hidden="true" />
        或
        <span className="h-px flex-1 bg-border" aria-hidden="true" />
      </div>

      <a
        href={oauthStartUrl('github')}
        className="inline-flex h-9 w-full items-center justify-center gap-2 rounded-md border border-ha-border-strong bg-ha-panel-2 text-[13px] font-medium text-ha-text-2 transition-colors hover:text-ha-text"
      >
        <GitHubMark />
        使用 GitHub 登录
      </a>
      <p className="mt-3 text-center text-[11px] text-ha-faint">
        首次登录会自动创建账号
      </p>
    </div>
  );
}
