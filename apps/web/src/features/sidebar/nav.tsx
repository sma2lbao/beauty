/**
 * 侧栏主导航:只留「工作组」三项,其余入口收进「更多」弹层(见 more-nav.tsx)。
 *
 * 演进路径:
 * - 最初 9 项主导航 + 3 项次级导航平铺,900px 高的窗口里导航吃掉 44% 高度,
 *   会话列表只剩 4 行;
 * - C 步骤按频次分成 工作/资源/管理 三组,管理组默认折叠,已缓解但仍有
 *   3 个组头和 5 项资源入口常驻;
 * - A 步骤把「每会话都会碰」的三项直接铺开,其余分组交给「更多」弹层:
 *   一次点击可达,但不再占版面。分组数据仍来自 nav-data,只是换了渲染器。
 */
import { ConsoleNavLink } from '@/components/console/ui';

import { WORK_GROUP } from './nav-state';

/** 高频道具:工作台 / 任务编排 / 变更。 */
export function PrimaryNav() {
  if (!WORK_GROUP) {
    return null;
  }
  return (
    <>
      {WORK_GROUP.items.map((item) => (
        <ConsoleNavLink key={item.to} {...item} />
      ))}
    </>
  );
}
