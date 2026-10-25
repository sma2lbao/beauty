import { lazy, Suspense } from 'react';
import { Outlet, Route, Routes } from 'react-router';

import { AuthGuard } from '@/components/auth-guard';
import { ConsoleLayout } from '@/components/console/console-layout';
import { ExternalRedirect } from '@/components/external-redirect';
import { SiteLayout } from '@/components/layout/site-layout';
import { RouteFallback } from '@/components/route-fallback';
import { siteUrl } from '@/lib/site-links';
import { AboutPage } from '@/pages/about';
import { CallbackPage } from '@/pages/callback';
import { LoginPage } from '@/pages/login';
import { NotFoundPage } from '@/pages/not-found';
import { OAuthAuthorizePage } from '@/pages/oauth-authorize';

/**
 * 控制台内页按路由懒加载:入口只含骨架、登录页与官网小页,
 * 各页面各自成 chunk,首屏不再下载全部 15 页。
 */
const ChatPage = lazy(() =>
  import('@/pages/chat').then((module) => ({ default: module.ChatPage })),
);
const RunsPage = lazy(() =>
  import('@/pages/runs').then((module) => ({ default: module.RunsPage })),
);
const ChangesPage = lazy(() =>
  import('@/pages/changes').then((module) => ({ default: module.ChangesPage })),
);
const McpPage = lazy(() =>
  import('@/pages/mcp').then((module) => ({ default: module.McpPage })),
);
const KnowledgePage = lazy(() =>
  import('@/pages/knowledge').then((module) => ({
    default: module.KnowledgePage,
  })),
);
const MemoryPage = lazy(() =>
  import('@/pages/memory').then((module) => ({ default: module.MemoryPage })),
);
const SettingsPage = lazy(() =>
  import('@/pages/settings').then((module) => ({
    default: module.SettingsPage,
  })),
);
const SkillsPage = lazy(() =>
  import('@/pages/skills').then((module) => ({ default: module.SkillsPage })),
);
const UsagePage = lazy(() =>
  import('@/pages/usage').then((module) => ({ default: module.UsagePage })),
);
const WorkspacePage = lazy(() =>
  import('@/pages/workspace').then((module) => ({
    default: module.WorkspacePage,
  })),
);
const OutboxPage = lazy(() =>
  import('@/pages/outbox').then((module) => ({ default: module.OutboxPage })),
);

export function App() {
  return (
    <Routes>
      {/* 公开页:登录/回调/官网/关于 */}
      <Route path="login" element={<LoginPage />} />
      <Route path="callback" element={<CallbackPage />} />
      {/* 官网已迁到 apps/site;旧深链兼容跳转,不再维护第二份 Landing */}
      <Route path="landing" element={<ExternalRedirect to={siteUrl('/')} />} />
      <Route element={<SiteLayout />}>
        <Route path="about" element={<AboutPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>

      {/* OAuth 2.1 授权页:登录门 + 独立布局 */}
      <Route element={<AuthGuard />}>
        <Route path="oauth/authorize" element={<OAuthAuthorizePage />} />
      </Route>

      {/* 控制台:登录门(§10)+ 侧边栏骨架 */}
      <Route element={<ConsoleLayout />}>
        <Route element={<AuthGuard />}>
          {/* Suspense 放在骨架内层:切页时侧栏/顶栏保持,仅主区域出骨架 */}
          <Route
            element={
              <Suspense fallback={<RouteFallback />}>
                <Outlet />
              </Suspense>
            }
          >
            <Route index element={<ChatPage />} />
            <Route path="runs" element={<RunsPage />} />
            <Route path="changes" element={<ChangesPage />} />
            <Route path="mcp" element={<McpPage />} />
            <Route path="knowledge" element={<KnowledgePage />} />
            <Route path="memory" element={<MemoryPage />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="skills" element={<SkillsPage />} />
            <Route path="usage" element={<UsagePage />} />
            <Route path="workspace" element={<WorkspacePage />} />
            <Route path="outbox" element={<OutboxPage />} />
          </Route>
        </Route>
      </Route>
    </Routes>
  );
}
