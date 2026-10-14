import { Route, Routes } from 'react-router';

import { AuthGuard } from '@/components/auth-guard';
import { SiteLayout } from '@/components/layout/site-layout';
import { AboutPage } from '@/pages/about';
import { CallbackPage } from '@/pages/callback';
import { ChatPage } from '@/pages/chat';
import { KnowledgePage } from '@/pages/knowledge';
import { LoginPage } from '@/pages/login';
import { NotFoundPage } from '@/pages/not-found';
import { OutboxPage } from '@/pages/outbox';
import { SettingsPage } from '@/pages/settings';
import { WorkspacePage } from '@/pages/workspace';
import { RunsPage } from '@/pages/runs';

export function App() {
  return (
    <Routes>
      <Route element={<SiteLayout />}>
        {/* 登录门(§10):token/logto 模式下受保护页需先登录;dev 放行 */}
        <Route element={<AuthGuard />}>
          <Route index element={<ChatPage />} />
          <Route path="runs" element={<RunsPage />} />
          <Route path="knowledge" element={<KnowledgePage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="workspace" element={<WorkspacePage />} />
          <Route path="outbox" element={<OutboxPage />} />
        </Route>
        <Route path="callback" element={<CallbackPage />} />
        <Route path="about" element={<AboutPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
      {/* 登录页是独立整页:未登录时导航没有可去之处,不套站点布局 */}
      <Route path="login" element={<LoginPage />} />
    </Routes>
  );
}
