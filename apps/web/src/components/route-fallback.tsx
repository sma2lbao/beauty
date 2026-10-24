/**
 * 路由级懒加载占位:控制台内页切到新 chunk 时先渲染骨架,避免白屏。
 *
 * 尺寸/配色沿用控制台骨架(--ha-* 令牌):一行标题 + 一行说明 + 一块内容,
 * 只表达"正在加载",不模拟具体页面内容(避免闪变)。
 */
export function RouteFallback() {
  return (
    <div
      role="status"
      aria-label="页面加载中"
      className="flex h-full flex-1 flex-col gap-3 p-6"
    >
      <div className="h-6 w-48 animate-pulse rounded bg-ha-panel-2" />
      <div className="h-4 w-72 animate-pulse rounded bg-ha-panel-2/70" />
      <div className="mt-2 h-40 w-full animate-pulse rounded-lg bg-ha-panel-2/50" />
      <span className="sr-only">加载中…</span>
    </div>
  );
}
