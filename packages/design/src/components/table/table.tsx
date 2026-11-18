import type { ComponentPropsWithoutRef } from 'react';
import { createContext, useContext } from 'react';
import { cva } from 'class-variance-authority';

import { cn } from '../../lib/utils.js';

/** 表格密度:default 用于常规数据表,compact 用于设置页这类密排面板。 */
export type TableDensity = 'default' | 'compact';

/**
 * 密度经 context 传给行列组件,调用方只需在 <Table> 上写一次,
 * 不必给 <TableRow> / <TableCell> 逐层再传。
 */
const TableDensityContext = createContext<TableDensity>('default');

function useTableDensity() {
  return useContext(TableDensityContext);
}

const tableVariants = cva('bd-table', {
  variants: {
    density: {
      default: '',
      compact: 'bd-table--compact',
    },
  },
  defaultVariants: {
    density: 'default',
  },
});

export interface TableProps extends ComponentPropsWithoutRef<'table'> {
  /** 密度;compact 收窄留白并给表头加底色 */
  density?: TableDensity;
}

/**
 * Table:数据表骨架,解剖与 shadcn/ui 的 Table 一致——
 * Table / TableHeader / TableBody / TableFooter / TableRow / TableHead /
 * TableCell / TableCaption 组合使用。
 *
 * 表格只负责行列结构、留白与分隔线;列宽、对齐、等宽数字等业务排版
 * 由调用方在单元格上追加 className。窄屏横滚请在外层包一层
 * `overflow-x: auto` 的容器(组件自身不假设外层布局)。
 */
export function Table({
  density = 'default',
  className,
  children,
  ...rest
}: TableProps) {
  return (
    <TableDensityContext.Provider value={density}>
      <table
        data-slot="table"
        data-density={density}
        className={cn(tableVariants({ density }), className)}
        {...rest}
      >
        {children}
      </table>
    </TableDensityContext.Provider>
  );
}

export type TableHeaderProps = ComponentPropsWithoutRef<'thead'>;

/** 表头区块:默认密度用下边框分隔,compact 密度把底色放在表头行上。 */
export function TableHeader({
  className,
  children,
  ...rest
}: TableHeaderProps) {
  return (
    <thead
      data-slot="table-header"
      className={cn('bd-table__header', className)}
      {...rest}
    >
      {children}
    </thead>
  );
}

export type TableBodyProps = ComponentPropsWithoutRef<'tbody'>;

/** 表体:行与行之间由 CSS 出分隔线,不需要调用方写 divide。 */
export function TableBody({ className, children, ...rest }: TableBodyProps) {
  return (
    <tbody
      data-slot="table-body"
      className={cn('bd-table__body', className)}
      {...rest}
    >
      {children}
    </tbody>
  );
}

export type TableFooterProps = ComponentPropsWithoutRef<'tfoot'>;

/** 表尾:合计行等,带底色与上边框。 */
export function TableFooter({
  className,
  children,
  ...rest
}: TableFooterProps) {
  return (
    <tfoot
      data-slot="table-footer"
      className={cn('bd-table__footer', className)}
      {...rest}
    >
      {children}
    </tfoot>
  );
}

export type TableRowProps = ComponentPropsWithoutRef<'tr'>;

/** 行:表头行与数据行同一套组件,由所在的表头/表体区块决定样式。 */
export function TableRow({ className, children, ...rest }: TableRowProps) {
  return (
    <tr
      data-slot="table-row"
      className={cn('bd-table__row', className)}
      {...rest}
    >
      {children}
    </tr>
  );
}

export type TableHeadProps = ComponentPropsWithoutRef<'th'>;

/** 表头单元格:小号次级文字,默认左对齐。 */
export function TableHead({ className, children, ...rest }: TableHeadProps) {
  const density = useTableDensity();
  return (
    <th
      data-slot="table-head"
      data-density={density}
      className={cn('bd-table__head', className)}
      {...rest}
    >
      {children}
    </th>
  );
}

export type TableCellProps = ComponentPropsWithoutRef<'td'>;

/** 数据单元格:只管留白,内容排版由调用方追加。 */
export function TableCell({ className, children, ...rest }: TableCellProps) {
  const density = useTableDensity();
  return (
    <td
      data-slot="table-cell"
      data-density={density}
      className={cn('bd-table__cell', className)}
      {...rest}
    >
      {children}
    </td>
  );
}

export type TableCaptionProps = ComponentPropsWithoutRef<'caption'>;

/** 表格题注:渲染在表格下方,补充数据口径。 */
export function TableCaption({
  className,
  children,
  ...rest
}: TableCaptionProps) {
  return (
    <caption
      data-slot="table-caption"
      className={cn('bd-table__caption', className)}
      {...rest}
    >
      {children}
    </caption>
  );
}
