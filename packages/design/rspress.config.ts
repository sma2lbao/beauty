import path from 'node:path';

import { defineConfig } from 'rspress/config';

// v2 把实现收敛进 @rspress/core,但 2.0.0-beta.21 的 CLI(bin/rspress.js)与
// defineConfig 仍由 rspress 元包提供,故两者都声明为 devDependencies 并锁同一版本。
// v2 内建 React 19 支持(以 @unhead/react 管理 <head>),v1 时代因
// react-helmet-async 多副本导致 HelmetDispatcher 报错的 rspack alias 兜底已移除。
//
// 开发态注意:rspack 持久化缓存(packages/design/node_modules/.cache/rspack)会缓存
// search index 的文件名哈希;一旦它与 doc_build 里的实际产物不一致,dev server 会在
// 编译页面时以 ENOENT(static/search_index.<hash>.json)崩溃。此时删除
// packages/design/node_modules/.cache/rspack 与 doc_build 后重启即可恢复。

// @beauty/design 的 exports 里用自定义条件 @beauty/source 指向 src/index.ts，
// import/default 则指向尚未构建的 dist/。文档站要直接消费组件源码（改源码即时热更），
// 与 tsconfig.base.json 的 customConditions: ["@beauty/source"] 保持同一套机制。
const BEAUTY_SOURCE_CONDITION = '@beauty/source';

export default defineConfig({
  title: 'Beauty Design',
  description: 'beauty monorepo 的 Web 端 React UI 组件库',
  // 文档内容目录默认为 <cwd>/docs、构建输出默认为 <cwd>/doc_build，
  // 与 docs:* 脚本在包根执行的约定一致，无需显式声明 root / outDir。
  globalStyles: path.join(__dirname, 'docs/styles/global.css'),
  builderConfig: {
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
