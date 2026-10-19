import { DesktopProvider } from '@/lib/desktop-context';
import { getBridge } from '@/lib/bridge';

import { DesktopShell } from '@/components/shell';
import { MissingBridgePage } from '@/components/startup-screens';

/**
 * 渲染进程根组件。
 *
 * 先判断预加载桥接是否存在:浏览器直接打开时给出「请通过桌面应用启动」,
 * 其余情况才挂 DesktopProvider 并读取本机数据。
 */
export function App() {
  const bridge = getBridge();

  if (!bridge) return <MissingBridgePage />;

  return (
    <DesktopProvider bridge={bridge}>
      <DesktopShell />
    </DesktopProvider>
  );
}
