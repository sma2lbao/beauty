import { Route, Routes } from 'react-router';

import { AuthGuard } from '@/components/auth-guard';
import { ConsoleLayout } from '@/components/console/console-layout';
import { SiteLayout } from '@/components/layout/site-layout';
import { AboutPage } from '@/pages/about';
import { CallbackPage } from '@/pages/callback';
import { ChangesPage } from '@/pages/changes';
import { ChatPage } from '@/pages/chat';
import { KnowledgePage } from '@/pages/knowledge';
import { LandingPage } from '@/pages/landing';
import { LoginPage } from '@/pages/login';
import { NotFoundPage } from '@/pages/not-found';
import { OutboxPage } from '@/pages/outbox';
import { SettingsPage } from '@/pages/settings';
import { WorkspacePage } from '@/pages/workspace';
import { RunsPage } from '@/pages/runs';

export function App() {
  return (
    <Routes>
      {/* 公开页:登录/回调/官网/关于 */}
      <Route path="login" element={<LoginPage />} />
      <Route path="callback" element={<CallbackPage />} />
      <Route path="landing" element={<LandingPage />} />
      <Route element={<SiteLayout />}>
        <Route path="about" element={<AboutPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>

      {/* 控制台:登录门(§10)+ 侧边栏骨架 */}
      <Route element={<ConsoleLayout />}>
        <Route element={<AuthGuard />}>
          <Route index element={<ChatPage />} />
          <Route path="runs" element={<RunsPage />} />
          <Route path="changes" element={<ChangesPage />} />
          <Route path="knowledge" element={<KnowledgePage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="workspace" element={<WorkspacePage />} />
          <Route path="outbox" element={<OutboxPage />} />
        </Route>
      </Route>
    </Routes>
  );
}
