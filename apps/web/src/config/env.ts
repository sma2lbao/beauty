/**
 * 构建期环境变量唯一入口(R-08):apps/web 全部 `import.meta.env` 读取收敛到本文件,
 * 其余代码只允许从这里 import。默认值与收敛前各处逐字一致:
 * - API base 默认 `''`(dev 同源,Vite 代理到各服务);
 * - authMode 默认 'token';logto 三项默认 undefined(缺配置时按需抛错);
 * - siteUrl:VITE_SITE_URL > PROD 分支 > dev :3000(原 site-links.ts 逻辑原样搬入)。
 *
 * 求值语义保持与现状一致:模块加载时一次性求值(`vi.stubEnv` 后须 `vi.resetModules()`
 * 重新 import,与改造前 lib/auth 的用法相同)。
 */

export type AuthMode = 'token' | 'session' | 'logto';

/** 鉴权模式(VITE_AUTH_MODE),默认 'token'。 */
export const authMode: AuthMode =
  (import.meta.env.VITE_AUTH_MODE as AuthMode | undefined) ?? 'token';

/** account-service 基址(VITE_ACCOUNT_API_URL),默认同源。 */
export const accountBase: string =
  (import.meta.env.VITE_ACCOUNT_API_URL as string | undefined) ?? '';

/** 运行编排服务基址(VITE_RUNS_API_URL),默认同源;runs/conversations/notifications/outbox 共用。 */
export const runsBase: string =
  (import.meta.env.VITE_RUNS_API_URL as string | undefined) ?? '';

/** 知识库服务基址(VITE_KNOWLEDGE_API_URL),默认同源。 */
export const knowledgeBase: string =
  (import.meta.env.VITE_KNOWLEDGE_API_URL as string | undefined) ?? '';

/** 工作区服务基址(VITE_WORKSPACE_API_URL),默认同源。 */
export const workspaceBase: string =
  (import.meta.env.VITE_WORKSPACE_API_URL as string | undefined) ?? '';

/** Logto OIDC 配置(VITE_LOGTO_*):缺省 undefined,logto 模式下缺配置抛错(见 lib/auth)。 */
export const logto = {
  endpoint: import.meta.env.VITE_LOGTO_ENDPOINT as string | undefined,
  appId: import.meta.env.VITE_LOGTO_APP_ID as string | undefined,
  resource: import.meta.env.VITE_LOGTO_RESOURCE as string | undefined,
};

/** 是否生产构建(原 site-links.ts 的 import.meta.env.PROD 判定搬入)。 */
export const isProd: boolean = import.meta.env.PROD;

/**
 * 官网基址(VITE_SITE_URL),原 site-links.ts 的默认逻辑原样搬入:
 * 本地 dev 默认 Next.js :3000;生产默认 www 域名;结尾斜杠统一剥掉。
 */
export const siteUrl = (
  import.meta.env.VITE_SITE_URL ??
  (isProd ? 'https://www.beautymoon.com' : 'http://localhost:3000')
).replace(/\/+$/, '');
