import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from './card.js';

describe('Card', () => {
  it('渲染六个区块并带语义类名', () => {
    render(
      <Card data-testid="card">
        <CardHeader>
          <CardTitle>月度报告</CardTitle>
          <CardDescription>生成时间 2026-10-17</CardDescription>
        </CardHeader>
        <CardContent>正文内容</CardContent>
        <CardFooter>脚部操作</CardFooter>
      </Card>,
    );

    const card = screen.getByTestId('card');
    expect(card).toHaveAttribute('data-slot', 'card');
    expect(card).toHaveClass('bd-card');

    expect(screen.getByText('月度报告')).toHaveClass('bd-card__title');
    expect(screen.getByText('生成时间 2026-10-17')).toHaveClass(
      'bd-card__description',
    );
    expect(screen.getByText('正文内容')).toHaveClass('bd-card__content');
    expect(screen.getByText('脚部操作')).toHaveClass('bd-card__footer');
  });

  it('支持传入 className 与其余 div 属性', () => {
    render(
      <Card className="my-card" id="custom">
        <CardHeader className="my-header" data-testid="header" />
      </Card>,
    );

    const card = document.getElementById('custom');
    expect(card).toHaveClass('bd-card', 'my-card');
    expect(screen.getByTestId('header')).toHaveClass(
      'bd-card__header',
      'my-header',
    );
  });
});
