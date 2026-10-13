import { BrandLogo } from '@/components/brand/logo';

export function SiteFooter() {
  return (
    <footer className="border-t">
      <div className="mx-auto flex max-w-5xl flex-col gap-3 px-6 py-8 sm:flex-row sm:items-center sm:justify-between">
        <BrandLogo />
        <p className="text-sm text-muted-foreground">
          © 2025 beauty moon · 把办公里的重复活交给 Agent
        </p>
      </div>
    </footer>
  );
}
