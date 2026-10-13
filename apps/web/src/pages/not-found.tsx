import { Link } from 'react-router';

import { Button } from '@/components/ui/button';

export function NotFoundPage() {
  return (
    <section className="mx-auto flex max-w-2xl flex-col items-center gap-6 px-6 py-24 text-center">
      <img
        src="/logo-mark.svg"
        alt=""
        aria-hidden="true"
        className="w-24"
      />
      <h1 className="font-display text-3xl">这个页面不在清单里</h1>
      <p className="text-muted-foreground">
        地址可能拼错了,或者这个页面还没做。
        回对话页继续派活,也可以去运行控制台看事件流水。
      </p>
      <div className="flex flex-wrap items-center justify-center gap-3">
        <Button asChild>
          <Link to="/">回到对话</Link>
        </Button>
        <Button asChild variant="outline">
          <Link to="/runs">运行控制台</Link>
        </Button>
      </div>
    </section>
  );
}
