import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';

import { handleSignInCallback, takeLoginRedirect } from '@/lib/auth';

/** OIDC 回调页:处理授权码换取令牌,然后回控制台(§10)。 */
export function CallbackPage() {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    handleSignInCallback(window.location.href)
      .then(() => {
        if (!cancelled) {
          navigate(takeLoginRedirect(), { replace: true });
        }
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : '登录回调处理失败');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [navigate]);

  return (
    <section className="mx-auto max-w-md px-6 py-24">
      <h1 className="text-[22px] font-semibold text-ha-text">正在完成登录…</h1>
      {error ? (
        <p className="mt-4 text-sm text-destructive">{error}</p>
      ) : (
        <p className="mt-4 text-sm text-muted-foreground">
          正在处理授权回调,完成后自动回到你刚才的页面。
        </p>
      )}
    </section>
  );
}
