import { useEffect } from 'react';

/**
 * 旧 /landing 深链的兼容跳转:官网已迁到 apps/site(Next.js),SPA 不再维护第二份 Landing。
 * 渲染可点击链接兜底(测试/弹窗拦截/自动跳转失败时仍能手动继续)。
 */
export function ExternalRedirect({ to }: { to: string }) {
  useEffect(() => {
    try {
      window.location.replace(to);
    } catch {
      // 非浏览器环境(测试)下静默,保留页面里的手动链接
    }
  }, [to]);

  return (
    <main className="flex min-h-svh flex-col items-center justify-center gap-4 bg-ha-bg px-6 text-center text-ha-text-body">
      <p className="font-mono text-[12px] text-ha-dim">正在前往官网…</p>
      <a
        href={to}
        className="inline-flex h-9 items-center rounded-md bg-ha-accent px-4 text-[13px] font-medium text-white transition-colors hover:bg-[#6b77e2]"
      >
        继续前往官网
      </a>
    </main>
  );
}
