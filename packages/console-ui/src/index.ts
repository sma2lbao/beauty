/**
 * @beauty/console-ui:web 控制台与 desktop 渲染进程共用的展示层。
 *
 * 只放「纯展示 + props 驱动」的零件:类名合并、语义色、状态基元、diff/代码查看。
 * 不含路由、数据请求、领域类型(状态到 tone 的映射留在各应用)。
 *
 * 语义色(tone)与状态基元(StatusDot/StatusText/ToneBadge/StatusPill)
 * 的实现在 @beauty/design,本包按控制台旧名称再导出,换实现不换调用点。
 *
 * diff/代码查看与 Markdown 的实现也在 @beauty/design,本包按旧路径再导出;
 * Markdown 依赖较重,单独走 "@beauty/console-ui/markdown" 子入口。
 */
export * from './cn.js';
export * from './tone.js';
export * from './status.js';
export * from './diff.js';
export * from './diff-view.js';
