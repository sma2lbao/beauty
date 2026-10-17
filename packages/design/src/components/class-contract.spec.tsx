import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
  Checkbox,
  Empty,
  Input,
  Select,
  Skeleton,
  Space,
  Spin,
  Switch,
  Tag,
  Textarea,
} from '../index.js';

/**
 * 类名契约:组件只输出 `bd-` 前缀的语义类(BEM + data-* 状态),
 * 不允许出现 Tailwind 工具类。样式全部来自组件自带的普通 CSS。
 */
const SLOTS = [
  'button',
  'card',
  'empty',
  'empty-figure',
  'empty-description',
  'empty-actions',
  'checkbox-root',
  'checkbox',
  'checkbox-box',
  'checkbox-label',
  'card-header',
  'card-title',
  'card-description',
  'card-content',
  'card-footer',
  'skeleton',
  'input-group',
  'input',
  'select',
  'select-control',
  'select-arrow',
  'input-prefix',
  'input-suffix',
  'switch',
  'switch-thumb',
  'tag',
  'tag-close',
  'space',
  'spin',
  'spin-indicator',
  'spin-label',
  'textarea',
];

describe('类名契约', () => {
  it('全部组件只使用 bd- 前缀的语义类', () => {
    const { container } = render(
      <>
        <Button>按钮</Button>
        <Button variant="outline" size="lg" block>
          大按钮
        </Button>
        <Input placeholder="昵称" prefix="¥" suffix="元" />
        <Textarea placeholder="简介" rows={3} />
        <Checkbox label="同意条款" />
        <Select aria-label="城市">
          <option value="sh">上海</option>
        </Select>
        <Spin label="加载中" />
        <Empty
          description="暂无数据"
          actions={<Button size="sm">刷新</Button>}
        />
        <Card>
          <CardHeader>
            <CardTitle>标题</CardTitle>
            <CardDescription>描述</CardDescription>
          </CardHeader>
          <CardContent>内容</CardContent>
          <CardFooter>脚部</CardFooter>
        </Card>
        <Skeleton />
        <Switch defaultChecked />
        <Tag color="success" closable>
          标签
        </Tag>
        <Space direction="vertical" align="center" wrap size="lg">
          <span>内容</span>
        </Space>
      </>,
    );

    for (const slot of SLOTS) {
      const element = container.querySelector(`[data-slot="${slot}"]`);
      expect(element, `缺少 data-slot=${slot}`).not.toBeNull();
      const names = ((element as Element).getAttribute('class') ?? '')
        .split(/\s+/)
        .filter(Boolean);
      expect(names.length, `${slot} 没有类名`).toBeGreaterThan(0);
      for (const name of names) {
        expect(name.startsWith('bd-'), `${slot} 出现非语义类:${name}`).toBe(
          true,
        );
      }
    }
  });
});
