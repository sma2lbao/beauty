import { Button } from '@beauty/design';

import { ADMIN_PAGE_SIZE } from './use-admin-model';

/**
 * 账号/审计共用的 offset 分页条。服务端返回 total,这里只做前后翻页;
 * 过滤条件变化时由 model 把 offset 归零,所以不需要页码跳转。
 */
export function AdminPagination({
  offset,
  total,
  onChange,
}: {
  offset: number;
  total: number;
  onChange: (offset: number) => void;
}) {
  const start = total === 0 ? 0 : offset + 1;
  const end = Math.min(offset + ADMIN_PAGE_SIZE, total);
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-t border-ha-border px-4 py-3 text-[11px] text-ha-dim">
      <span>
        {total === 0 ? '共 0 条' : '第 ' + start + '–' + end + ' 条 / 共 ' + total + ' 条'}
      </span>
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={offset === 0}
          onClick={() => onChange(Math.max(0, offset - ADMIN_PAGE_SIZE))}
        >
          上一页
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={offset + ADMIN_PAGE_SIZE >= total}
          onClick={() => onChange(offset + ADMIN_PAGE_SIZE)}
        >
          下一页
        </Button>
      </div>
    </div>
  );
}
