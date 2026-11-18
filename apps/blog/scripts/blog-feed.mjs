import fs from 'node:fs';
import path from 'node:path';

import matter from 'gray-matter';

/**
 * 静态导出没有 server route,构建后从内容源生成 out/feed.xml。
 * 由 package.json 的 build 脚本在 next build 之后调用。
 */
const root = process.cwd();
const contentDir = path.join(root, 'content');
const outDir = path.join(root, 'out');
const siteUrl = (
  process.env.NEXT_PUBLIC_BLOG_SITE_URL ?? 'https://blog.beautymoon.cn'
).replace(/\/+$/, '');
const siteTitle = 'beauty moon · 学习笔记';
const siteDescription =
  'TypeScript、Python 与 Agent 工程的学习记录:读书笔记、实验和可以跑起来的小项目。';

function escapeXml(value) {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&apos;');
}

function readPosts() {
  if (!fs.existsSync(contentDir)) {
    return [];
  }

  return fs
    .readdirSync(contentDir)
    .filter((file) => file.endsWith('.mdx'))
    .map((file) => {
      const raw = fs.readFileSync(path.join(contentDir, file), 'utf8');
      const { data } = matter(raw);
      return {
        slug: file.replace(/\.mdx$/, ''),
        title: data.title,
        description: data.description,
        date: data.date,
        tags: Array.isArray(data.tags) ? data.tags : [],
        draft: data.draft === true,
      };
    })
    .filter((post) => !post.draft)
    .sort((a, b) => b.date.localeCompare(a.date));
}

function buildFeed(posts) {
  const items = posts
    .map((post) => {
      const url = `${siteUrl}/posts/${post.slug}`;
      return [
        '    <item>',
        `      <title>${escapeXml(post.title)}</title>`,
        `      <link>${escapeXml(url)}</link>`,
        `      <guid isPermaLink="true">${escapeXml(url)}</guid>`,
        `      <pubDate>${new Date(`${post.date}T00:00:00Z`).toUTCString()}</pubDate>`,
        `      <description>${escapeXml(post.description)}</description>`,
        ...post.tags.map(
          (tag) => `      <category>${escapeXml(tag)}</category>`,
        ),
        '    </item>',
      ].join('\n');
    })
    .join('\n');

  const lastBuild = posts[0]
    ? new Date(`${posts[0].date}T00:00:00Z`).toUTCString()
    : new Date().toUTCString();

  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
    '  <channel>',
    `    <title>${escapeXml(siteTitle)}</title>`,
    `    <link>${escapeXml(siteUrl)}</link>`,
    `    <description>${escapeXml(siteDescription)}</description>`,
    '    <language>zh-CN</language>',
    `    <lastBuildDate>${lastBuild}</lastBuildDate>`,
    `    <atom:link href="${escapeXml(`${siteUrl}/feed.xml`)}" rel="self" type="application/rss+xml" />`,
    items,
    '  </channel>',
    '</rss>',
    '',
  ]
    .filter((line) => line !== '')
    .join('\n');
}

const posts = readPosts();

if (!fs.existsSync(outDir)) {
  fs.mkdirSync(outDir, { recursive: true });
}

fs.writeFileSync(path.join(outDir, 'feed.xml'), buildFeed(posts), 'utf8');
console.warn(`[blog] feed.xml written (${posts.length} posts)`);
