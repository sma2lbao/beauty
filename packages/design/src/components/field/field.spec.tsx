import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Field } from './field.js';

describe('Field', () => {
  it('渲染 label 与控件并建立关联', () => {
    render(
      <Field label="邮箱" htmlFor="email">
        <input id="email" />
      </Field>,
    );

    const label = screen.getByText('邮箱');
    expect(label.tagName).toBe('LABEL');
    expect(label).toHaveAttribute('for', 'email');
    expect(screen.getByLabelText('邮箱')).toBeInTheDocument();
    expect(screen.getByText('邮箱').closest('[data-slot="field"]')).toHaveClass(
      'bd-field',
    );
  });

  it('展示辅助说明', () => {
    render(
      <Field label="密码" hint="至少 8 位">
        <input />
      </Field>,
    );

    expect(screen.getByText('至少 8 位')).toHaveAttribute(
      'data-slot',
      'field-hint',
    );
  });

  it('有错误时进入 invalid 态并优先展示错误', () => {
    render(
      <Field label="邮箱" hint="会发送验证码" error="邮箱格式不正确">
        <input />
      </Field>,
    );

    const field = screen.getByText('邮箱').closest('[data-slot="field"]');
    expect(field).toHaveAttribute('data-invalid', 'true');
    expect(screen.getByRole('alert')).toHaveTextContent('邮箱格式不正确');
    expect(screen.queryByText('会发送验证码')).not.toBeInTheDocument();
  });

  it('required 展示星号且对辅助技术隐藏', () => {
    const { container } = render(
      <Field label="名称" required>
        <input />
      </Field>,
    );

    const mark = container.querySelector('.bd-field__required');
    expect(mark).toHaveTextContent('*');
    expect(mark).toHaveAttribute('aria-hidden', 'true');
  });

  it('支持追加 className', () => {
    const { container } = render(
      <Field label="名称" className="my-field">
        <input />
      </Field>,
    );

    expect(container.querySelector('[data-slot="field"]')).toHaveClass(
      'bd-field',
      'my-field',
    );
  });
});
