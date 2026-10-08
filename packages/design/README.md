# @beauty/design

beauty monorepo 的 Web 端 React UI 组件库,基于设计令牌(CSS 自定义属性)提供一套克制、可预期的组件与亮暗双主题,TypeScript 类型完备、零运行时依赖。

## 使用

```tsx
import { Button } from '@beauty/design';
import '@beauty/design/styles.css';

<Button variant="primary">保存</Button>
```

文档站(Rspress)本地开发:

```bash
npm run docs:dev -w @beauty/design
```

## 构建

```bash
nx build @beauty/design
```

## 单元测试

```bash
nx test @beauty/design
```
