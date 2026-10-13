export function SiteFooter() {
  return (
    <footer className="border-t">
      <div className="mx-auto flex max-w-5xl flex-col gap-3 px-6 py-8 sm:flex-row sm:items-center sm:justify-between">
        <p className="flex items-center gap-2 font-display text-lg">
          <img
            src="/logo-mark.svg"
            alt=""
            aria-hidden="true"
            className="h-5 w-5"
          />
          beauty moon
        </p>
        <p className="text-sm text-muted-foreground">
          © 2025 beauty moon · 把办公里的重复活交给 Agent
        </p>
      </div>
    </footer>
  );
}
