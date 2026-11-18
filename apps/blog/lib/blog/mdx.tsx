import { compileMDX } from 'next-mdx-remote/rsc';
import rehypeAutolinkHeadings from 'rehype-autolink-headings';
import rehypePrettyCode from 'rehype-pretty-code';
import rehypeSlug from 'rehype-slug';
import remarkGfm from 'remark-gfm';

import { mdxComponents } from '@/components/mdx-components';

/**
 * 在服务端把 MDX 编译成 RSC 内容。
 * 语法高亮发生在构建期,不会把 shiki 发到浏览器。
 */
export async function renderMdx(source: string) {
  const { content } = await compileMDX({
    source,
    components: mdxComponents,
    options: {
      parseFrontmatter: false,
      mdxOptions: {
        remarkPlugins: [remarkGfm],
        rehypePlugins: [
          rehypeSlug,
          [
            rehypeAutolinkHeadings,
            {
              behavior: 'wrap',
              properties: {
                className: ['heading-anchor'],
                ariaLabel: '本节永久链接',
              },
            },
          ],
          [
            rehypePrettyCode,
            {
              theme: 'github-dark-default',
              keepBackground: false,
              defaultLang: { block: 'text', inline: 'text' },
            },
          ],
        ],
      },
    },
  });

  return content;
}
