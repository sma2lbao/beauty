import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { Dialog } from './dialog.js';

describe('Dialog', () => {
  it('open 时打开并渲染标题 / 描述 / 正文 / 底部', () => {
    render(
      <Dialog
        open
        onOpenChange={() => {}}
        title="删除确认"
        description="该操作不可撤销"
        footer={<button type="button">确定</button>}
      >
        正文内容
      </Dialog>,
    );

    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute('data-slot', 'dialog');
    // jsdom 无 showModal,组件降级为 open 属性
    expect(dialog).toHaveAttribute('open');
    expect(screen.getByText('删除确认')).toHaveAttribute(
      'data-slot',
      'dialog-title',
    );
    expect(screen.getByText('该操作不可撤销')).toHaveAttribute(
      'data-slot',
      'dialog-description',
    );
    expect(screen.getByText('正文内容')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '确定' })).toBeInTheDocument();
  });

  it('关闭按钮回调 onOpenChange(false)', () => {
    const handleChange = vi.fn();
    render(<Dialog open onOpenChange={handleChange} title="标题" />);

    fireEvent.click(screen.getByRole('button', { name: '关闭' }));
    expect(handleChange).toHaveBeenCalledWith(false);
  });

  it('点遮罩关闭,点面板内部不关闭', () => {
    const handleChange = vi.fn();
    const { container } = render(
      <Dialog open onOpenChange={handleChange} title="标题">
        内容
      </Dialog>,
    );

    const dialog = screen.getByRole('dialog');
    const panel = container.querySelector('[data-slot="dialog-panel"]');

    fireEvent.click(panel as Element);
    expect(handleChange).not.toHaveBeenCalled();

    fireEvent.click(dialog);
    expect(handleChange).toHaveBeenCalledWith(false);
  });

  it('closeOnBackdrop=false 时点遮罩不关闭', () => {
    const handleChange = vi.fn();
    render(
      <Dialog
        open
        onOpenChange={handleChange}
        closeOnBackdrop={false}
        title="标题"
      />,
    );

    fireEvent.click(screen.getByRole('dialog'));
    expect(handleChange).not.toHaveBeenCalled();
  });

  it('ESC 触发的原生 close 事件回调 onOpenChange(false)', () => {
    const handleChange = vi.fn();
    render(<Dialog open onOpenChange={handleChange} title="标题" />);

    fireEvent(screen.getByRole('dialog'), new Event('close'));
    expect(handleChange).toHaveBeenCalledWith(false);
  });

  it('标题与描述通过 aria-labelledby / aria-describedby 关联', () => {
    render(
      <Dialog open onOpenChange={() => {}} title="标题" description="描述" />,
    );

    const dialog = screen.getByRole('dialog');
    const title = screen.getByText('标题');
    const description = screen.getByText('描述');

    expect(dialog).toHaveAttribute('aria-labelledby', title.id);
    expect(dialog).toHaveAttribute('aria-describedby', description.id);
  });

  it('closable=false 时不渲染关闭按钮', () => {
    render(
      <Dialog open onOpenChange={() => {}} title="标题" closable={false} />,
    );

    expect(
      screen.queryByRole('button', { name: '关闭' }),
    ).not.toBeInTheDocument();
  });
});
