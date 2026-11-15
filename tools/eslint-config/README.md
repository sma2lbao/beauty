# @beauty/eslint-config

beauty-moon 仓库共享的 ESLint 10 flat config。所有 ESLint 插件依赖都收敛在本包
(`dependencies`),各项目只依赖 `@beauty/eslint-config` 一个包,无需逐个安装插件。

## 提供的配置

| 导出      | 适用项目                               | 内容                                                                  |
| --------- | -------------------------------------- | --------------------------------------------------------------------- |
| `base()`  | 纯 TS/JS 包、Node 工具、仓库根配置文件 | @eslint/js + typescript-eslint recommended + Prettier 兼容            |
| `react()` | React 项目(apps/web、packages/design)  | base + react / react-hooks / react-refresh(React 19,新 JSX transform) |

两者都是**工厂函数**,返回 flat config 数组,方便后续按需扩展选项。

## 使用

在项目根创建 `eslint.config.mjs`:

```js
// React 项目
import { react } from '@beauty/eslint-config';

export default react();
```

```js
// 纯 TS 包
import { base } from '@beauty/eslint-config';

export default base();
```

运行(nx.json 已注册 `@nx/eslint` 插件,含 eslint.config 的项目自动获得 lint target):

```sh
npx nx lint @beauty/web          # 单项目
npx nx run-many -t lint          # 全仓库
npx nx run-many -t lint -- --fix # 自动修复
```

## 约定

- **格式交给 Prettier**:配置末尾应用 `eslint-config-prettier`,关闭全部格式类规则。
- **console**:仅允许 `console.warn` / `console.error`,其余告警。
- **未使用变量**:`_` 前缀的参数/变量豁免。
- **全局忽略**:`dist/`、`out-tsc/`、`doc_build/`、`coverage/`、`reports/` 及 Python 项目目录(ruff 管 Python)。

## ESLint 10 兼容说明

- 插件/配置均已支持 ESLint 10(@eslint/js 10、typescript-eslint 8.72+、react-hooks 7、react-refresh 0.5、@nx/eslint 23)。
- **eslint-plugin-react 7.37.5 尚未声明 ESLint 10 支持**(peer 只到 `^9.7`,运行期 detectReactVersion 会调用已移除的 `context.getFilename()`)。
  过渡方案:配置里把 `settings.react.version` 钉成 `19.3` 以绕开版本探测分支,其余规则实测可用;安装时 npm 只会给出 ERESOLVE 警告。
  上游发布支持后:改回 `'detect'` 并移除本段说明。

## 新增项目接入 checklist

1. `npm install -D @beauty/eslint-config --workspace=<项目>`(或手动改项目 `package.json`);
2. 项目根创建 3 行的 `eslint.config.mjs`(见上);
3. `npx nx lint <项目名>` 验证。
