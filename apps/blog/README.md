# @beauty/blog

学习博客,独立于官网的 Next.js 静态站。

## 开发

```sh
npx nx run @beauty/blog:dev     # http://localhost:3001
npx nx run @beauty/blog:build   # 静态导出到 apps/blog/out
npx nx run @beauty/blog:test
npx nx run @beauty/blog:typecheck
```

## 结构

- `content/*.mdx`:文章与 frontmatter(zod 校验在 `lib/blog/schema.ts`);
- `lib/blog/`:内容读取、聚合、TOC、MDX 编译;
- `app/`:列表、详情、标签、系列、归档、sitemap/robots;
- `scripts/blog-feed.mjs`:构建后生成 `out/feed.xml`(静态导出没有 server route)。

## 写一篇笔记

在 `content/` 新建 `<slug>.mdx`,`slug` 只允许小写字母、数字和连字符:

```yaml
---
title: '标题'
description: '列表页摘要,200 字以内'
date: '2026-11-18'
tags: ['typescript', '工程化']
series: 'TypeScript 进阶' # 可选
seriesOrder: 1 # 可选
draft: false # true 时只在 dev 可见
lang: ts # ts | python | mixed
demo: # 可选,实验项目元信息
  path: labs/ts/port-scanner
  run: npm run lab:dev
  port: 4100
  status: runnable
---
```

## 设计

见 `docs/design/blog.md`。
