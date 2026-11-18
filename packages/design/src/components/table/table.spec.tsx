import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from './table.js';

function renderTable(density?: 'default' | 'compact') {
  return render(
    <Table density={density}>
      <TableCaption>最近 7 天</TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead>服务</TableHead>
          <TableHead>调用数</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        <TableRow>
          <TableCell>api-gateway</TableCell>
          <TableCell>128</TableCell>
        </TableRow>
        <TableRow>
          <TableCell>tool-service</TableCell>
          <TableCell>64</TableCell>
        </TableRow>
      </TableBody>
      <TableFooter>
        <TableRow>
          <TableCell>合计</TableCell>
          <TableCell>192</TableCell>
        </TableRow>
      </TableFooter>
    </Table>,
  );
}

describe('Table', () => {
  it('渲染表头、表体、表尾与题注', () => {
    renderTable();

    expect(screen.getAllByRole('columnheader')).toHaveLength(2);
    // 表体 2 行 + 表尾合计 1 行
    expect(screen.getAllByRole('cell')).toHaveLength(6);
    expect(screen.getByText('api-gateway')).toBeInTheDocument();
    expect(screen.getByText('最近 7 天').tagName).toBe('CAPTION');
    expect(screen.getByText('合计').closest('tfoot')).toHaveAttribute(
      'data-slot',
      'table-footer',
    );
  });

  it('默认密度不加密排修饰类', () => {
    renderTable();

    const table = screen.getByRole('table');
    expect(table).toHaveClass('bd-table');
    expect(table).not.toHaveClass('bd-table--compact');
    expect(table).toHaveAttribute('data-density', 'default');
  });

  it('compact 密度把标记下发到表头与单元格', () => {
    renderTable('compact');

    const table = screen.getByRole('table');
    expect(table).toHaveClass('bd-table--compact');
    expect(table).toHaveAttribute('data-density', 'compact');
    expect(screen.getAllByRole('columnheader')[0]).toHaveAttribute(
      'data-density',
      'compact',
    );
    expect(screen.getByText('api-gateway')).toHaveAttribute(
      'data-density',
      'compact',
    );
  });

  it('每个结构件都带 data-slot,便于宿主做覆盖样式', () => {
    const { container } = renderTable('compact');

    const slots = Array.from(container.querySelectorAll('[data-slot]')).map(
      (element) => element.getAttribute('data-slot'),
    );

    expect(slots).toEqual(
      expect.arrayContaining([
        'table',
        'table-caption',
        'table-header',
        'table-body',
        'table-footer',
        'table-row',
        'table-head',
        'table-cell',
      ]),
    );
  });

  it('追加 className 与单元格属性透传', () => {
    render(
      <Table className="my-table">
        <TableBody>
          <TableRow>
            <TableCell colSpan={2} className="text-right">
              空
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>,
    );

    expect(screen.getByRole('table')).toHaveClass('bd-table', 'my-table');
    const cell = screen.getByRole('cell');
    expect(cell).toHaveClass('bd-table__cell', 'text-right');
    expect(cell).toHaveAttribute('colspan', '2');
  });
});
