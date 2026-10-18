/**
 * Markdown 渲染(M0-08):消息/计划/产物的统一富文本。
 *
 * - remark-gfm 支持表格、任务列表、删除线、自动链接;
 * - rehype-sanitize 使用默认 schema 清洗 HTML/脚本/危险协议(不启用 raw HTML);
 * - 流式输出时即使代码块/表格未闭合也不会崩,由 react-markdown 容错;
 * - 样式走容器内的 [&_...] 选择器,保持组件库之外的 Tailwind 用法一致。
 */
import type { Components } from 'react-markdown';
import ReactMarkdown from 'react-markdown';
import rehypeSanitize from 'rehype-sanitize';
import remarkGfm from 'remark-gfm';

import { cn } from '@/lib/utils';

const COMPONENTS: Components = {
  h1: ({ children }) => (
    <h1 className="mt-5 mb-2 text-[20px] font-semibold leading-7 text-ha-text">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 className="mt-5 mb-2 text-[17px] font-semibold leading-6 text-ha-text">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="mt-4 mb-1.5 text-[15px] font-semibold leading-6 text-ha-text">
      {children}
    </h3>
  ),
  h4: ({ children }) => (
    <h4 className="mt-3 mb-1 text-[14px] font-semibold text-ha-text">
      {children}
    </h4>
  ),
  p: ({ children }) => <p className="my-2 leading-[23px]">{children}</p>,
  ul: ({ children }) => (
    <ul className="my-2 list-disc pl-5 marker:text-ha-faint">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="my-2 list-decimal pl-5 marker:text-ha-faint">{children}</ol>
  ),
  li: ({ children }) => <li className="my-1">{children}</li>,
  blockquote: ({ children }) => (
    <blockquote className="my-3 border-l-2 border-ha-border-strong pl-3 text-ha-muted">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-4 border-ha-border" />,
  strong: ({ children }) => (
    <strong className="font-semibold text-ha-text">{children}</strong>
  ),
  em: ({ children }) => <em className="italic">{children}</em>,
  a: ({ href, children }) => {
    const safe =
      href === undefined ||
      href.startsWith('http://') ||
      href.startsWith('https://') ||
      href.startsWith('mailto:') ||
      href.startsWith('#') ||
      href.startsWith('/');
    const external = Boolean(href && href.startsWith('http'));
    return (
      <a
        href={safe ? href : undefined}
        target={external ? '_blank' : undefined}
        rel={external ? 'noopener noreferrer' : undefined}
        className="break-words text-ha-accent-soft underline underline-offset-2"
      >
        {children}
      </a>
    );
  },
  code: ({ className, children }) => (
    <code
      className={cn(
        'rounded bg-ha-panel-2 px-1 py-0.5 font-mono text-[12px] text-ha-text-body',
        className,
      )}
    >
      {children}
    </code>
  ),
  pre: ({ children }) => (
    <pre className="my-3 overflow-x-auto rounded-lg border border-ha-border bg-ha-code p-3 font-mono text-[12px] leading-5 text-ha-text-2">
      {children}
    </pre>
  ),
  table: ({ children }) => (
    <div className="my-3 overflow-x-auto">
      <table className="w-full border-collapse text-[12px]">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-ha-panel-2">{children}</thead>,
  th: ({ children }) => (
    <th className="border border-ha-border px-2 py-1 text-left font-semibold text-ha-text">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border border-ha-border px-2 py-1 align-top">{children}</td>
  ),
  img: ({ src, alt }) => (
    <img
      src={src}
      alt={alt ?? ''}
      loading="lazy"
      className="my-3 max-w-full rounded-lg border border-ha-border"
    />
  ),
};

export function Markdown({
  content,
  className,
  streaming = false,
}: {
  content: string;
  className?: string;
  /** 流式输出时保留尾部光标,避免整段重排。 */
  streaming?: boolean;
}) {
  if (!content.trim()) {
    return streaming ? <span className="text-ha-dim">正在回答…</span> : null;
  }
  return (
    <div
      data-testid="markdown"
      className={cn(
        'min-w-0 break-words text-[14px] leading-[23px] text-ha-text-2',
        '[&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_pre_code]:text-inherit',
        '[&_table_code]:bg-transparent [&_table_code]:p-0',
        className,
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSanitize]}
        components={COMPONENTS}
      >
        {content}
      </ReactMarkdown>
      {streaming ? (
        <span
          aria-hidden="true"
          className="ml-0.5 inline-block h-4 w-[2px] animate-pulse bg-ha-muted align-middle"
        />
      ) : null}
    </div>
  );
}
