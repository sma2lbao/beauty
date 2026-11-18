import type { TocItem } from '@/lib/blog/toc';

export function PostToc({ items }: { items: TocItem[] }) {
  if (items.length < 2) {
    return null;
  }

  return (
    <nav aria-label="本页目录" className="lg:sticky lg:top-24">
      <p className="font-mono text-[9.5px] uppercase tracking-[0.2em] text-ha-faint">
        On this page
      </p>
      <ol className="mt-4 space-y-2 border-l border-ha-border">
        {items.map((item) => (
          <li key={item.id}>
            <a
              href={`#${item.id}`}
              className={[
                'block border-l border-transparent py-0.5 leading-5 transition-colors hover:border-ha-accent-soft hover:text-ha-text-2',
                item.depth === 3
                  ? 'pl-6 text-[11.5px] text-ha-dim'
                  : 'pl-4 text-[12px] text-ha-muted',
              ].join(' ')}
            >
              {item.text}
            </a>
          </li>
        ))}
      </ol>
    </nav>
  );
}
