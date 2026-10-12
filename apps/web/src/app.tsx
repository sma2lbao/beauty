import { Route, Routes } from 'react-router';

import { SiteLayout } from '@/components/layout/site-layout';
import { AboutPage } from '@/pages/about';
import { CallbackPage } from '@/pages/callback';
import { HomePage } from '@/pages/home';
import { KnowledgePage } from '@/pages/knowledge';
import { NotFoundPage } from '@/pages/not-found';
import { ProductsPage } from '@/pages/products';
import { RunsPage } from '@/pages/runs';

export function App() {
  return (
    <Routes>
      <Route element={<SiteLayout />}>
        <Route index element={<HomePage />} />
        <Route path="products" element={<ProductsPage />} />
        <Route path="runs" element={<RunsPage />} />
        <Route path="knowledge" element={<KnowledgePage />} />
        <Route path="callback" element={<CallbackPage />} />
        <Route path="about" element={<AboutPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
