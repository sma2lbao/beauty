import type { ReactNode } from 'react';
import type { Components } from 'react-markdown';
import ReactMarkdown from 'react-markdown';
import rehypeSanitize from 'rehype-sanitize';
import remarkGfm from 'remark-gfm';

import { cn } from '../../lib/utils.js';
import {
  highlightCode,
  languageFromClassName,
  type CodeTokenKind,
} from './highlight.js';

/**
 * Markdown 富文本:消息、计划、产物正文的统一渲染。
 *
 * - remark-gfm 支持表格、任务列表、删除线、自动链接;
 * - rehype-sanitize 用默认 schema 清洗 HTML/脚本/危险协议(不启用 raw HTML);
 * - 流式输出时即使代码块/表格未闭合也不会崩,由 react-markdown 容忍;
 * - 样式是普通 CSS(bd-markdown__* 语义类 + --bd-* 令牌),源码里不出现 Tailwind 工具类,
 *   宿主不装 Tailwind 也能得到同样的排版。
 *
 * 依赖较重(react-markdown / remark-gfm / rehype-sanitize),因此同时提供
 * "@beauty/design/markdown" 子入口:只消费按钮、表格等基础组件的宿主
 * 可以完全绕开这条依赖链。
 */

/** 高亮 token 的语义类:颜色全部来自 --bd-* 令牌。 */
const TOKEN_CLASS: Record<CodeTokenKind, string | undefined> = {
  keyword: 'bd-markdown__token--keyword',
  string: 'bd-markdown__token--string',
  comment: 'bd-markdown__token--comment',
  number: 'bd-markdown__token--number',
  plain: undefined,
};

/** 代码块文本可能被 react-markdown 拆成多段 children。 */
function textOf(children: ReactNode): string {
  if (typeof children === 'string') return children;
  if (typeof children === 'number') return String(children);
  if (Array.isArray(children)) return children.map(textOf).join('');
  return '';
}

const COMPONENTS: Components = {
  h1: ({ children }) => <h1 className="bd-markdown__h1">{children}</h1>,
  h2: ({ children }) => <h2 className="bd-markdown__h2">{children}</h2>,
  h3: ({ children }) => <h3 className="bd-markdown__h3">{children}</h3>,
  h4: ({ children }) => <h4 className="bd-markdown__h4">{children}</h4>,
  p: ({ children }) => <p className="bd-markdown__p">{children}</p>,
  ul: ({ children }) => <ul className="bd-markdown__ul">{children}</ul>,
  ol: ({ children }) => <ol className="bd-markdown__ol">{children}</ol>,
  li: ({ children }) => <li className="bd-markdown__li">{children}</li>,
  blockquote: ({ children }) => (
    <blockquote className="bd-markdown__blockquote">{children}</blockquote>
  ),
  hr: () => <hr className="bd-markdown__hr" />,
  strong: ({ children }) => (
    <strong className="bd-markdown__strong">{children}</strong>
  ),
  em: ({ children }) => <em className="bd-markdown__em">{children}</em>,
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
        className="bd-markdown__link"
      >
        {children}
      </a>
    );
  },
  code: ({ className, children }) => {
    const language = languageFromClassName(className);
    const text = language ? textOf(children) : '';
    if (language && text) {
      const tokens = highlightCode(text, language);
      return (
        <code className={cn('bd-markdown__code', 'bd-markdown__code--block')}>
          {tokens.map((token, index) =>
            token.kind === 'plain' ? (
              <span key={index}>{token.text}</span>
            ) : (
              <span
                key={index}
                className={cn('bd-markdown__token', TOKEN_CLASS[token.kind])}
              >
                {token.text}
              </span>
            ),
          )}
        </code>
      );
    }
    return (
      <code className={cn('bd-markdown__code', 'bd-markdown__code--inline')}>
        {children}
      </code>
    );
  },
  pre: ({ children }) => <pre className="bd-markdown__pre">{children}</pre>,
  table: ({ children }) => (
    <div className="bd-markdown__table-wrap">
      <table className="bd-markdown__table">{children}</table>
    </div>
  ),
  thead: ({ children }) => (
    <thead className="bd-markdown__thead">{children}</thead>
  ),
  th: ({ children }) => <th className="bd-markdown__th">{children}</th>,
  td: ({ children }) => <td className="bd-markdown__td">{children}</td>,
  img: ({ src, alt }) => (
    <img
      src={src}
      alt={alt ?? ''}
      loading="lazy"
      className="bd-markdown__img"
    />
  ),
};

export interface MarkdownProps {
  /** Markdown 源码 */
  content: string;
  /** 追加到根节点的类名 */
  className?: string;
  /** 流式输出时保留尾部光标,避免整段重排 */
  streaming?: boolean;
}

export function Markdown({
  content,
  className,
  streaming = false,
}: MarkdownProps) {
  if (!content.trim()) {
    return streaming ? (
      <span className="bd-markdown__placeholder">正在回答…</span>
    ) : null;
  }
  return (
    <div data-slot="markdown" className={cn('bd-markdown', className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSanitize]}
        components={COMPONENTS}
      >
        {content}
      </ReactMarkdown>
      {streaming ? (
        <span aria-hidden="true" className="bd-markdown__caret" />
      ) : null}
    </div>
  );
}
