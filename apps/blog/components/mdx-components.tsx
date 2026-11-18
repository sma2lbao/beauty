import type { ComponentPropsWithoutRef, ReactNode } from 'react';

type HeadingProps = ComponentPropsWithoutRef<'h2'> & { children?: ReactNode };

function ExternalAwareLink({
  href = '',
  children,
  ...props
}: ComponentPropsWithoutRef<'a'>) {
  const external = /^https?:\/\//.test(href);

  return (
    <a
      href={href}
      {...(external ? { target: '_blank', rel: 'noreferrer noopener' } : null)}
      {...props}
    >
      {children}
      {external ? <span aria-hidden> ↗</span> : null}
    </a>
  );
}

export const mdxComponents = {
  h2: ({ children, ...props }: HeadingProps) => <h2 {...props}>{children}</h2>,
  h3: ({ children, ...props }: HeadingProps) => <h3 {...props}>{children}</h3>,
  a: ExternalAwareLink,
  // 静态导出没有图片优化服务,内容图用原生 img 并显式给尺寸
  img: (props: ComponentPropsWithoutRef<'img'>) => (
    <img loading="lazy" decoding="async" alt="" {...props} />
  ),
  pre: (props: ComponentPropsWithoutRef<'pre'>) => <pre {...props} />,
  code: (props: ComponentPropsWithoutRef<'code'>) => <code {...props} />,
};
