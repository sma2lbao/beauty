import Link from 'next/link';

export default function NotFound() {
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col items-start px-5 py-24 sm:px-8 sm:py-32">
      <p className="font-mono text-[9.5px] uppercase tracking-[0.22em] text-ha-accent-soft">
        404 / not found
      </p>
      <h1 className="mt-4 font-serif text-[36px] tracking-[-0.02em] text-ha-text">
        这页还没写。
      </h1>
      <p className="mt-3 max-w-[460px] text-[14px] leading-7 text-ha-muted">
        链接可能过期,或者笔记还没发布。回到首页看看最近在学什么。
      </p>
      <Link
        href="/"
        className="mt-8 rounded-md border border-ha-border-strong px-4 py-2 text-[12.5px] text-ha-text-2 transition-colors hover:border-ha-accent hover:text-ha-text"
      >
        ← 返回笔记
      </Link>
    </div>
  );
}
