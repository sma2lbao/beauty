/**
 * Markdown 渲染(实现已迁到 @beauty/console-ui/markdown)。
 *
 * 保留该路径作为应用内适配层:chat/run-detail/artifact-preview 的既有引用零改动。
 * 子入口单独隔离 react-markdown 依赖,桌面端只消费状态基元时不会带入。
 */
export { Markdown } from '@beauty/console-ui/markdown';
