# @beauty/site

beauty moon 官网(Next.js App Router,静态导出)。

```sh
npx nx run @beauty/site:dev     # http://localhost:3000
npx nx build @beauty/site       # 产物在 apps/site/out
npx nx run @beauty/site:typecheck
```

- 页面:/、/about、/pricing、/docs、/changelog
- 静态导出:`next.config.ts` 的 `output: 'export'`;Vercel 也支持直接识别
- 控制台跳转地址由 `NEXT_PUBLIC_APP_URL` 配置(默认本地 Vite :5173)
- 站点域名由 `NEXT_PUBLIC_SITE_URL` 配置(用于 sitemap/OG)
