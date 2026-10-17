import type { ComponentPropsWithoutRef } from 'react';

import { cn } from '../../lib/utils.js';

/**
 * Card 卡片:内容分块容器。
 *
 * 结构与 shadcn/ui 的 Card 对齐(Card / CardHeader / CardTitle /
 * CardDescription / CardContent / CardFooter),语义类名见同目录 card.css。
 * 颜色/圆角/阴影全部来自 --bd-* 令牌,样式只在 CSS 里写。
 */

export type CardProps = ComponentPropsWithoutRef<'div'>;

export function Card({ className, ...rest }: CardProps) {
  return (
    <div data-slot="card" className={cn('bd-card', className)} {...rest} />
  );
}

export type CardHeaderProps = ComponentPropsWithoutRef<'div'>;

export function CardHeader({ className, ...rest }: CardHeaderProps) {
  return (
    <div
      data-slot="card-header"
      className={cn('bd-card__header', className)}
      {...rest}
    />
  );
}

export type CardTitleProps = ComponentPropsWithoutRef<'div'>;

export function CardTitle({ className, ...rest }: CardTitleProps) {
  return (
    <div
      data-slot="card-title"
      className={cn('bd-card__title', className)}
      {...rest}
    />
  );
}

export type CardDescriptionProps = ComponentPropsWithoutRef<'div'>;

export function CardDescription({ className, ...rest }: CardDescriptionProps) {
  return (
    <div
      data-slot="card-description"
      className={cn('bd-card__description', className)}
      {...rest}
    />
  );
}

export type CardContentProps = ComponentPropsWithoutRef<'div'>;

export function CardContent({ className, ...rest }: CardContentProps) {
  return (
    <div
      data-slot="card-content"
      className={cn('bd-card__content', className)}
      {...rest}
    />
  );
}

export type CardFooterProps = ComponentPropsWithoutRef<'div'>;

export function CardFooter({ className, ...rest }: CardFooterProps) {
  return (
    <div
      data-slot="card-footer"
      className={cn('bd-card__footer', className)}
      {...rest}
    />
  );
}
