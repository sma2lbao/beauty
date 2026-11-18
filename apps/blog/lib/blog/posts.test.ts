import path from 'node:path';

import { describe, expect, it } from 'vitest';

import {
  estimateReadingMinutes,
  getAdjacentPosts,
  getAllPosts,
  getAllSeries,
  getAllTags,
  getArchive,
  getPostBySlug,
  getPostsByTag,
} from './posts';
import { extractToc } from './toc';

const contentDir = path.join(process.cwd(), 'content');
const fixtureDir = path.join(process.cwd(), 'lib/blog/__fixtures__/invalid');

describe('getAllPosts', () => {
  it('默认只返回已发布笔记,并按日期倒序', () => {
    const posts = getAllPosts({ contentDir });

    expect(posts.length).toBeGreaterThanOrEqual(3);
    const dates = posts.map((post) => post.frontmatter.date);
    expect(dates).toEqual([...dates].sort((a, b) => b.localeCompare(a)));
    expect(posts.every((post) => !post.frontmatter.draft)).toBe(true);
  });

  it('includeDrafts 时把草稿排进时间线', () => {
    const posts = getAllPosts({ contentDir, includeDrafts: true });

    const published = getAllPosts({ contentDir });
    expect(posts).toHaveLength(published.length + 1);
    expect(posts[0]?.slug).toBe('draft-agent-memory');
    expect(posts[0]?.frontmatter.draft).toBe(true);
  });

  it('缺少内容目录时返回空数组', () => {
    expect(
      getAllPosts({ contentDir: path.join(process.cwd(), 'content-missing') }),
    ).toEqual([]);
  });

  it('frontmatter 不合法时带文件名报错', () => {
    expect(() => getAllPosts({ contentDir: fixtureDir })).toThrow(
      /content\/bad\.mdx frontmatter 无效/,
    );
  });
});

describe('getPostBySlug', () => {
  it('返回正文与阅读时长', () => {
    const post = getPostBySlug('typescript-type-level-notes', { contentDir });

    expect(post?.frontmatter.title).toContain('类型');
    expect(post?.content).toContain('DeepPartial');
    expect(post?.readingMinutes).toBeGreaterThan(0);
  });

  it('拒绝路径穿越与不存在的 slug', () => {
    expect(getPostBySlug('../package', { contentDir })).toBeNull();
    expect(getPostBySlug('missing-post', { contentDir })).toBeNull();
  });

  it('草稿默认不可见,显式包含时可读', () => {
    expect(getPostBySlug('draft-agent-memory', { contentDir })).toBeNull();
    expect(
      getPostBySlug('draft-agent-memory', {
        contentDir,
        includeDrafts: true,
      })?.frontmatter.draft,
    ).toBe(true);
  });
});

describe('getAdjacentPosts', () => {
  it('按时间线给出更新/更早的邻居', () => {
    const posts = getAllPosts({ contentDir });
    const index = posts.findIndex(
      (post) => post.slug === 'typescript-type-level-notes',
    );
    const adjacent = getAdjacentPosts(posts[index]!.slug, { contentDir });

    expect(adjacent.newer?.slug).toBe(posts[index - 1]?.slug);
    expect(adjacent.older?.slug).toBe(posts[index + 1]?.slug);
  });

  it('首尾与未知 slug 的边界', () => {
    const posts = getAllPosts({ contentDir });
    const newest = posts[0]?.slug;
    const oldest = posts.at(-1)?.slug;
    expect(newest).toBeDefined();
    expect(oldest).toBeDefined();
    expect(getAdjacentPosts(newest!, { contentDir }).newer).toBeNull();
    expect(getAdjacentPosts(oldest!, { contentDir }).older).toBeNull();
    expect(getAdjacentPosts('missing', { contentDir })).toEqual({
      newer: null,
      older: null,
    });
  });
});

describe('聚合', () => {
  it('标签按出现次数排序并统计', () => {
    const tags = getAllTags(getAllPosts({ contentDir }));

    const posts = getAllPosts({ contentDir });
    const uniqueTags = new Set(posts.flatMap((post) => post.frontmatter.tags));
    const countTag = (tag: string) =>
      posts.filter((post) => post.frontmatter.tags.includes(tag)).length;
    expect(tags).toContainEqual({
      tag: 'typescript',
      count: countTag('typescript'),
    });
    expect(tags).toContainEqual({ tag: 'labs', count: countTag('labs') });
    expect(tags).toContainEqual({ tag: 'http', count: countTag('http') });
    expect(tags).toHaveLength(uniqueTags.size);
  });

  it('按标签过滤', () => {
    const posts = getPostsByTag('python', { contentDir });

    expect(posts).toHaveLength(1);
    expect(posts[0]?.slug).toBe('python-asyncio-notes');
  });

  it('系列按 seriesOrder 排序', () => {
    const series = getAllSeries(getAllPosts({ contentDir }));

    expect(series.map((item) => item.name)).toEqual(
      expect.arrayContaining([
        'TypeScript 进阶',
        'Python 工程习惯',
        'HTTP 基础',
        'JavaScript 核心',
        '浏览器机制',
        'Web 安全',
        'SQL 基础',
      ]),
    );
    const httpSeries = series.find((item) => item.name === 'HTTP 基础');
    expect(httpSeries?.posts.map((post) => post.slug)).toEqual([
      'http-caching',
      'http-cookie',
      'set-cookie',
      'http2',
    ]);
  });

  it('归档按年份分组', () => {
    const archive = getArchive(getAllPosts({ contentDir }));

    const posts = getAllPosts({ contentDir });
    const years = [
      ...new Set(posts.map((post) => post.frontmatter.date.slice(0, 4))),
    ].sort((a, b) => b.localeCompare(a));
    expect(archive.map((item) => item.year)).toEqual(years);
    for (const item of archive) {
      expect(item.posts).toHaveLength(
        posts.filter((post) => post.frontmatter.date.startsWith(item.year))
          .length,
      );
    }
  });
});

describe('extractToc', () => {
  it('只收 2/3 级标题,并生成稳定 id', () => {
    const toc = extractToc(`
# 一级不收录

## 二级标题

正文

### 三级 \`code\`

## 二级标题

\`\`\`ts
## 代码里的井号不是标题
\`\`\`
`);

    expect(toc).toEqual([
      { id: '二级标题', text: '二级标题', depth: 2 },
      { id: '三级-code', text: '三级 code', depth: 3 },
      { id: '二级标题-1', text: '二级标题', depth: 2 },
    ]);
  });

  it('没有标题时返回空数组', () => {
    expect(extractToc('只有正文。')).toEqual([]);
  });
});

describe('estimateReadingMinutes', () => {
  it('至少为 1 分钟', () => {
    expect(estimateReadingMinutes('')).toBe(1);
    expect(estimateReadingMinutes('```ts\nconst a = 1;\n```')).toBe(1);
  });
});
