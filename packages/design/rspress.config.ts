import path from 'node:path';

import { defineConfig } from 'rspress/config';

// v2 把实现收敛进 @rspress/core,但 2.0.0-beta.21 的 CLI(bin/rspress.js)与
// defineConfig 仍由 rspress 元包提供,故两者都声明为 devDependencies 并锁同一版本。
// v2 内建 React 19 支持(以 @unhead/react 管理 <head>),v1 时代因
// react-helmet-async 多副本导致 HelmetDispatcher 报错的 rspack alias 兜底已移除。

// 关闭 rspress 的持久化构建缓存(node_modules/.cache/rspack)。
//
// 它缓存 search index 的文件名哈希,而哈希由文档内容决定、产物却写在 doc_build:
// 只要在 dev 未运行时改过 docs 内容、或跑过一次 docs:build,缓存里的旧哈希就与
// doc_build 中的实际文件名不一致,dev server 随后以未捕获的
// ENOENT(static/search_index.<hash>.json) 直接退出。
//
// 开关只能是环境变量:rspress 装配 rsbuild 配置时会无条件覆盖 performance.buildCache
// (见 @rspress/core/dist/index.js — `'false' !== process.env.RSPRESS_PERSISTENT_CACHE
// ? { buildCache: {...} } : {}`),写进 builderConfig 会被它盖掉。本模块先于 rspress
// 装配被导入,故在这里设置;用 ??= 而非 = 以便外部按需临时打开缓存做对比实验。
//
// 代价:本站冷编译约 0.2s、docs:build 约 1.5s,缓存收益远小于上面这个崩溃陷阱。
process.env.RSPRESS_PERSISTENT_CACHE ??= 'false';

// @beauty/design 的 exports 里用自定义条件 @beauty/source 指向 src/index.ts，
// import/default 则指向尚未构建的 dist/。文档站要直接消费组件源码（改源码即时热更），
// 与 tsconfig.base.json 的 customConditions: ["@beauty/source"] 保持同一套机制。
const BEAUTY_SOURCE_CONDITION = '@beauty/source';

export default defineConfig({
  title: 'Beauty Design',
  description: 'beauty monorepo 的 Web 端 React UI 组件库',
  // 站点标识与图标：三份资源都在 docs/public（等价于站点根，Rspress 的 publicDir
  // 固定为 `<root>/public`），dev 与构建产物统一按根路径引用。
  //   logo.svg      渐变瓦片标识，导航栏与首页 hero 使用
  //   logo-mono.svg 透明底单色字形，跟随系统深浅色
  //   favicon.svg   浏览器页签用小尺寸优化版（更少留白、更粗的月牙）
  // 注意 icon 必须是绝对路径写法：Rspress 会对绝对路径取 basename 拼到
  // `<root>/public` 下，同时以该路径作为页面 <link rel="icon"> 的 URL。
  logo: '/logo.svg',
  logoText: 'Beauty Design',
  icon: '/favicon.svg',
  // 文档内容目录默认为 <cwd>/docs、构建输出默认为 <cwd>/doc_build，
  // 与 docs:* 脚本在包根执行的约定一致，无需显式声明 root / outDir。
  globalStyles: path.join(__dirname, 'docs/styles/global.css'),
  builderConfig: {
    // 不在这里设 performance.buildCache:rspress 会覆盖它,
    // 改由文件顶部的 RSPRESS_PERSISTENT_CACHE=false 关闭(原因见顶部注释)。
    tools: {
      // rsbuild 的 ResolveConfig 只支持 dedupe / alias / aliasStrategy / extensions，
      // 没有 conditionNames（写在 resolve 里会被静默丢弃），因此下沉到 rspack 配置层注入。
      rspack(config) {
        const existing = config.resolve?.conditionNames;
        const list = Array.isArray(existing) ? existing : [];
        const next = { ...config.resolve };

        if (!list.includes(BEAUTY_SOURCE_CONDITION)) {
          // '...' 由 rspack 展开为内置默认条件，避免覆写后丢失默认解析行为
          next.conditionNames = [BEAUTY_SOURCE_CONDITION, '...', ...list];
        }

        // src follows the nodestnext convention: relative imports carry a .js
        // extension, which bundlers map back to .ts/.tsx sources.
        next.extensionAlias = {
          ...next.extensionAlias,
          '.js': ['.ts', '.tsx', '.js'],
        };

        config.resolve = next;
        return config;
      },
    },
  },
  themeConfig: {
    nav: [
      { text: '指南', link: '/guide/introduction' },
      { text: '组件', link: '/components/overview' },
    ],
    sidebar: {
      '/guide/': [
        { text: '介绍', link: '/guide/introduction' },
        { text: '快速开始', link: '/guide/quick-start' },
        { text: '主题定制', link: '/guide/theming' },
        { text: '样式与覆写', link: '/guide/styling' },
      ],
      '/components/': [
        { text: '组件总览', link: '/components/overview' },
        { text: 'Button 按钮', link: '/components/button' },
        { text: 'Input 输入框', link: '/components/input' },
        { text: 'Space 间距', link: '/components/space' },
        { text: 'Switch 开关', link: '/components/switch' },
        { text: 'Tag 标签', link: '/components/tag' },
      ],
    },
    footer: {
      message: 'Beauty Design · 在内部项目中通过 @beauty/design 引用',
    },
  },
});
