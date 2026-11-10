/**
 * 当前账号的角色(P0-7):导航入口与 /admin 页共读 accountApi.me 的
 * react-query 缓存(queryKeys.accountMe),不引入新的全局状态。
 *
 * 这里的角色只服务展示;真正的权限由服务端 require_admin 每请求查库校验,
 * 所以读不到 role 时(旧版响应)按普通用户处理 —— 失败关闭,入口隐藏。
 */
import { useQuery } from '@tanstack/react-query';

import { accountApi } from '@/lib/account-api';
import { queryKeys } from '@/lib/query-keys';

export function useAccountRole() {
  const query = useQuery({
    queryKey: queryKeys.accountMe,
    queryFn: accountApi.me,
  });
  const role = query.data?.role ?? 'user';
  return {
    account: query.data,
    role,
    isAdmin: role === 'admin',
    isLoading: query.isPending,
    error: query.error,
    refetch: query.refetch,
  };
}
