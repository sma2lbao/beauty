import { Route, Routes } from 'react-router';

import { SiteLayout } from '@/components/layout/site-layout';
import { AboutPage } from '@/pages/about';
import { CallbackPage } from '@/pages/callback';
import { ChatPage } from '@/pages/chat';
import { KnowledgePage } from '@/pages/knowledge';
import { NotFoundPage } from '@/pages/not-found';
import { OutboxPage } from '@/pages/outbox';
import { SettingsPage } from '@/pages/settings';
import { WorkspacePage } from '@/pages/workspace';
import { RunsPage } from '@/pages/runs';

export function App() {
  return (
    <Routes>
      <Route element={<SiteLayout />}>
        <Route index element={<ChatPage />} />
        <Route path="runs" element={<RunsPage />} />
        <Route path="knowledge" element={<KnowledgePage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="workspace" element={<WorkspacePage />} />
        <Route path="outbox" element={<OutboxPage />} />
        <Route path="callback" element={<CallbackPage />} />
        <Route path="about" element={<AboutPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
