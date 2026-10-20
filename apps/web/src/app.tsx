import { Route, Routes } from 'react-router';

import { AuthGuard } from '@/components/auth-guard';
import { ConsoleLayout } from '@/components/console/console-layout';
import { ExternalRedirect } from '@/components/external-redirect';
import { SiteLayout } from '@/components/layout/site-layout';
import { siteUrl } from '@/lib/site-links';
import { AboutPage } from '@/pages/about';
import { CallbackPage } from '@/pages/callback';
import { ChangesPage } from '@/pages/changes';
import { ConnectorsPage } from '@/pages/connectors';
import { ChatPage } from '@/pages/chat';
import { KnowledgePage } from '@/pages/knowledge';
import { LoginPage } from '@/pages/login';
import { McpPage } from '@/pages/mcp';
import { NotFoundPage } from '@/pages/not-found';
import { OAuthAuthorizePage } from '@/pages/oauth-authorize';
import { OutboxPage } from '@/pages/outbox';
import { SettingsPage } from '@/pages/settings';
import { SkillsPage } from '@/pages/skills';
import { UsagePage } from '@/pages/usage';
import { WorkspacePage } from '@/pages/workspace';
import { RunsPage } from '@/pages/runs';

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
          <Route index element={<ChatPage />} />
          <Route path="runs" element={<RunsPage />} />
          <Route path="changes" element={<ChangesPage />} />
          <Route path="connectors" element={<ConnectorsPage />} />
          <Route path="mcp" element={<McpPage />} />
          <Route path="knowledge" element={<KnowledgePage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="skills" element={<SkillsPage />} />
          <Route path="usage" element={<UsagePage />} />
          <Route path="workspace" element={<WorkspacePage />} />
          <Route path="outbox" element={<OutboxPage />} />
        </Route>
      </Route>
    </Routes>
  );
}
