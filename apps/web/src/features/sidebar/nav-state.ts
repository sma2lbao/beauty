/**
 * 导航的纯函数 / 纯数据派生:与组件文件分开,避免 react-refresh 把
 * 「文件里既有组件又有普通导出」判为需要整体刷新。
 */
import { NAV_GROUPS, type NavGroup, type NavItem } from './nav-data';

/** 当前路由是否落在某个导航项上(与 NavLink 的 end 语义保持一致)。 */
export function isNavItemActive(
  item: Pick<NavItem, 'to' | 'end'>,
  pathname: string,
): boolean {
  if (item.end) {
    return pathname === item.to;
  }
  const base = item.to.split('?')[0];
  return pathname === base || pathname.startsWith(base + '/');
}

/** 常驻项:工作中每会话都会碰的入口。 */
export const WORK_GROUP = NAV_GROUPS.find((group) => group.id === 'work');

/** 「更多」弹层里的剩余分组(资源 / 管理)。 */
export const MORE_GROUPS: NavGroup[] = NAV_GROUPS.filter(
  (group) => group.id !== 'work',
);
