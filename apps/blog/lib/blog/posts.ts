import fs from 'node:fs';
import path from 'node:path';

import matter from 'gray-matter';

import { postFrontmatterSchema, type PostFrontmatter } from './schema';

export interface PostMeta {
  slug: string;
  frontmatter: PostFrontmatter;
  readingMinutes: number;
}

export interface Post extends PostMeta {
  content: string;
}

export interface LoadOptions {
  contentDir?: string;
  includeDrafts?: boolean;
}

export interface TagCount {
  tag: string;
  count: number;
}

export interface SeriesSummary {
  name: string;
  posts: PostMeta[];
  latestDate: string;
}

export interface ArchiveYear {
  year: string;
  posts: PostMeta[];
}

const DEFAULT_CONTENT_DIR = path.join(process.cwd(), 'content');
const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]*$/;

function resolveDir(options: LoadOptions): string {
  return options.contentDir ?? DEFAULT_CONTENT_DIR;
}

function parsePostFile(
  dir: string,
  fileName: string,
): { meta: PostMeta; content: string } {
  const slug = fileName.replace(/\.mdx$/, '');
  const raw = fs.readFileSync(path.join(dir, fileName), 'utf8');
  const { data, content } = matter(raw);
  const parsed = postFrontmatterSchema.safeParse(data);

  if (!parsed.success) {
    const detail = parsed.error.issues
      .map((issue) => `${issue.path.join('.') || '(root)'}: ${issue.message}`)
      .join('; ');
    throw new Error(`[blog] content/${fileName} frontmatter 无效: ${detail}`);
  }

  return {
    meta: {
      slug,
      frontmatter: parsed.data,
      readingMinutes: estimateReadingMinutes(content),
    },
    content,
  };
}

function readPosts(
  options: LoadOptions,
): Array<{ meta: PostMeta; content: string }> {
  const dir = resolveDir(options);
  if (!fs.existsSync(dir)) {
    return [];
  }

  return fs
    .readdirSync(dir, { withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith('.mdx'))
    .map((entry) => parsePostFile(dir, entry.name))
    .filter(
      ({ meta }) => (options.includeDrafts ?? false) || !meta.frontmatter.draft,
    )
    .sort(
      (a, b) =>
        b.meta.frontmatter.date.localeCompare(a.meta.frontmatter.date) ||
        a.meta.slug.localeCompare(b.meta.slug),
    );
}

export function getAllPosts(options: LoadOptions = {}): PostMeta[] {
  return readPosts(options).map(({ meta }) => meta);
}

export function getPostBySlug(
  slug: string,
  options: LoadOptions = {},
): Post | null {
  if (!SLUG_PATTERN.test(slug)) {
    return null;
  }

  const file = path.join(resolveDir(options), `${slug}.mdx`);
  if (!fs.existsSync(file)) {
    return null;
  }

  const { meta, content } = parsePostFile(resolveDir(options), `${slug}.mdx`);
  if (meta.frontmatter.draft && !(options.includeDrafts ?? false)) {
    return null;
  }

  return { ...meta, content };
}

export function getAdjacentPosts(
  slug: string,
  options: LoadOptions = {},
): { newer: PostMeta | null; older: PostMeta | null } {
  const posts = getAllPosts(options);
  const index = posts.findIndex((post) => post.slug === slug);

  if (index === -1) {
    return { newer: null, older: null };
  }

  return {
    newer: posts[index - 1] ?? null,
    older: posts[index + 1] ?? null,
  };
}

export function getAllTags(posts: PostMeta[] = getAllPosts()): TagCount[] {
  const counts = new Map<string, number>();
  for (const post of posts) {
    for (const tag of post.frontmatter.tags) {
      counts.set(tag, (counts.get(tag) ?? 0) + 1);
    }
  }

  return [...counts.entries()]
    .map(([tag, count]) => ({ tag, count }))
    .sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag));
}

export function getPostsByTag(
  tag: string,
  options: LoadOptions = {},
): PostMeta[] {
  return getAllPosts(options).filter((post) =>
    post.frontmatter.tags.includes(tag),
  );
}

export function getAllSeries(
  posts: PostMeta[] = getAllPosts(),
): SeriesSummary[] {
  const groups = new Map<string, PostMeta[]>();
  for (const post of posts) {
    const series = post.frontmatter.series;
    if (!series) {
      continue;
    }
    groups.set(series, [...(groups.get(series) ?? []), post]);
  }

  return [...groups.entries()]
    .map(([name, items]) => {
      const ordered = [...items].sort((a, b) => {
        const orderA = a.frontmatter.seriesOrder ?? Number.MAX_SAFE_INTEGER;
        const orderB = b.frontmatter.seriesOrder ?? Number.MAX_SAFE_INTEGER;
        return (
          orderA - orderB ||
          a.frontmatter.date.localeCompare(b.frontmatter.date)
        );
      });
      return {
        name,
        posts: ordered,
        latestDate:
          ordered
            .map((post) => post.frontmatter.date)
            .sort()
            .at(-1) ?? '',
      };
    })
    .sort((a, b) => b.latestDate.localeCompare(a.latestDate));
}

export function getArchive(posts: PostMeta[] = getAllPosts()): ArchiveYear[] {
  const groups = new Map<string, PostMeta[]>();
  for (const post of posts) {
    const year = post.frontmatter.date.slice(0, 4);
    groups.set(year, [...(groups.get(year) ?? []), post]);
  }

  return [...groups.entries()]
    .map(([year, items]) => ({ year, posts: items }))
    .sort((a, b) => b.year.localeCompare(a.year));
}

export function estimateReadingMinutes(content: string): number {
  const text = content
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ');
  const cjk = (text.match(/[\u4e00-\u9fff]/g) ?? []).length;
  const words = text
    .replace(/[\u4e00-\u9fff]/g, ' ')
    .trim()
    .split(/\s+/)
    .filter(Boolean).length;

  return Math.max(1, Math.ceil(cjk / 350 + words / 220));
}
