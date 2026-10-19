/**
 * 桌面打包配置:MVP 只保证本机打包可跑通(--dir 免签名的目录产物)。
 * 正式分发前再补 icon / 签名 / notarize / 自动更新。
 */
export default {
  appId: 'com.beautymoon.desktop',
  productName: 'beauty moon',
  directories: {
    output: 'dist/package',
    buildResources: 'resources',
  },
  // 只打运行时产物,源码与测试不进包
  files: ['dist/main/**/*', 'dist/preload/**/*', 'dist/renderer/**/*', 'package.json'],
  asar: true,
  mac: {
    category: 'public.app-category.productivity',
    target: ['dir'],
  },
  win: {
    target: ['dir'],
  },
  linux: {
    target: ['dir'],
  },
};
