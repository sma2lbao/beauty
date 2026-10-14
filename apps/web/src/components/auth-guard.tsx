import { useEffect, useState } from 'react';
import { Navigate, Outlet, useLocation } from 'react-router';

import { accessToken, authMode } from '@/lib/auth';

type Verdict = 'pending' | 'allowed' | 'denied';

/**
 * 登录守卫(架构 §10):`token`/`logto` 模式下,拿不到 access token 的路由一律
 * 送回 `/login` 并记住来路(登录后跳回);`dev` 模式(后端鉴权关闭)直接放行。
 *
 * 校验随路由切换重跑:令牌被清掉后,下一次导航即回登录页,不用等接口 401。
 */
export function AuthGuard() {
  const location = useLocation();
  // dev 模式同步放行,不走异步检查(首屏不闪 loading)
  const [verdict, setVerdict] = useState<Verdict>(() =>
    authMode() === 'dev' ? 'allowed' : 'pending',
  );

  useEffect(() => {
    if (authMode() === 'dev') {
      return;
    }
    let cancelled = false;
    accessToken()
      .then((token) => {
        if (!cancelled) {
          setVerdict(token ? 'allowed' : 'denied');
        }
      })
      .catch(() => {
        // IdP 不可达等校验异常按未登录处理
        if (!cancelled) {
          setVerdict('denied');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [location.pathname]);

  if (verdict === 'pending') {
    return (
      <p
        className="py-24 text-center text-sm text-muted-foreground"
        data-testid="auth-guard-pending"
      >
        正在确认登录状态…
      </p>
    );
  }

  if (verdict === 'denied') {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  return <Outlet />;
}
