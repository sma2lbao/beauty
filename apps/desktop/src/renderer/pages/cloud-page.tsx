/**
 * 云端模式页:说明「继续使用原有服务端编排」+ 在系统浏览器打开云端控制台。
 *
 * 桌面端不内嵌云端控制台(避免 Web 控制台的登录态/路由与 Electron 环境纠缠),
 * 只提供一个受控的 openExternal 入口,地址来自设置里的 cloudConsoleUrl。
 */
import { Cloud, ExternalLink, Monitor } from 'lucide-react';

import { useDesktop } from '@/lib/desktop-context';

import { ConsoleButton } from '@/components/ui';

export function CloudPage({
  onBackToLocal,
  onOpenSettings,
}: {
  onBackToLocal: () => void;
  onOpenSettings: () => void;
}) {
  const { bridge, state } = useDesktop();
  const consoleUrl = state.settings?.cloudConsoleUrl?.trim() ?? '';

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex h-[52px] shrink-0 items-center justify-between gap-3 border-b border-ha-border bg-ha-sidebar px-4">
        <nav
          aria-label="面包屑"
          className="flex items-center gap-2 font-mono text-[12px]"
        >
          <span className="text-ha-dim">beauty moon</span>
          <span aria-hidden="true" className="text-ha-ghost">
            /
          </span>
          <span className="font-semibold text-ha-text">云端模式</span>
        </nav>
        <span className="font-mono text-[11px] text-ha-faint">服务端编排</span>
      </header>

      <div className="flex min-h-0 flex-1 items-center justify-center overflow-y-auto p-8">
        <section className="flex w-full max-w-[560px] flex-col gap-4 rounded-xl border border-ha-border bg-ha-panel p-6">
          <span className="grid h-10 w-10 place-items-center rounded-xl border border-ha-border bg-ha-sidebar text-ha-accent-soft">
            <Cloud aria-hidden="true" className="h-4 w-4" />
          </span>

          <div>
            <h1 className="text-[17px] font-semibold text-ha-text">云端模式</h1>
            <p className="mt-2 text-[13px] leading-6 text-ha-muted">
              云端模式继续使用原有服务端编排:任务规划与审批、连接器、知识库都在云端运行。
              本机会话、文件读写与命令执行在云端模式下不会启动。
            </p>
          </div>

          <div className="rounded-md border border-ha-border bg-ha-code px-3 py-2">
            <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-ha-faint">
              控制台地址
            </p>
            <p className="mt-1 truncate font-mono text-[12px] text-ha-text-2">
              {consoleUrl || '未配置(可在设置中填写)'}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <ConsoleButton
              variant="primary"
              size="lg"
              onClick={() => void bridge.cloud.openConsole()}
            >
              <ExternalLink aria-hidden="true" className="h-3.5 w-3.5" />
              打开云端控制台
            </ConsoleButton>
            <ConsoleButton
              variant="secondary"
              size="lg"
              onClick={onBackToLocal}
            >
              <Monitor aria-hidden="true" className="h-3.5 w-3.5" />
              回到本地电脑
            </ConsoleButton>
            <ConsoleButton variant="ghost" size="lg" onClick={onOpenSettings}>
              配置地址
            </ConsoleButton>
          </div>

          <p className="font-mono text-[11px] leading-5 text-ha-faint">
            控制台在系统默认浏览器中打开(Electron 不会内嵌页面)。
          </p>
        </section>
      </div>
    </div>
  );
}
