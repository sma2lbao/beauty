/**
 * 控制台展示层 barrel。
 *
 * 实现按用途拆在 ./presentation/ 下(run/events/tools/diff/format),
 * 这里只做再导出,既有的 `from '@/lib/presentation'` 引用方零改动。
 */
export * from './presentation/format';
export * from './presentation/run';
export * from './presentation/events';
export * from './presentation/tools';
export * from './presentation/diff';
