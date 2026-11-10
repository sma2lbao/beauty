import { describe, expect, it } from 'vitest';

import {
  highlightCode,
  languageFromClassName,
  normalizeLanguage,
} from './markdown-highlight.js';

const kinds = (code: string, language?: string) =>
  highlightCode(code, language).map((token) => [token.kind, token.text]);

describe('highlightCode', () => {
  it('识别 TS 关键字、字符串、注释与数字', () => {
    const tokens = kinds("// 注释\nconst n = 42;\nconst s = 'hello';", 'ts');
    expect(tokens).toContainEqual(['comment', '// 注释']);
    expect(tokens).toContainEqual(['keyword', 'const']);
    expect(tokens).toContainEqual(['number', '42']);
    expect(tokens).toContainEqual(['string', "'hello'"]);
    const keywordCount = tokens.filter(([kind]) => kind === 'keyword').length;
    expect(keywordCount).toBe(2);
  });

  it('字符串里的关键字不被误判,且转义引号不会提前结束字符串', () => {
    const tokens = kinds("const a = 'const \\' x';", 'javascript');
    const strings = tokens.filter(([kind]) => kind === 'string');
    expect(strings).toHaveLength(1);
    expect(strings[0][1]).toContain('const');
    expect(tokens.filter(([kind]) => kind === 'keyword')).toHaveLength(1);
  });

  it('Python 三引号整段算字符串,井号注释生效', () => {
    const tokens = kinds(
      'def f():\n    """doc\nstring"""\n    # note\n    return True',
      'python',
    );
    expect(tokens).toContainEqual(['keyword', 'def']);
    expect(tokens).toContainEqual(['keyword', 'return']);
    expect(
      tokens.some(
        ([kind, text]) => kind === 'string' && text.startsWith('"""doc'),
      ),
    ).toBe(true);
    expect(tokens).toContainEqual(['comment', '# note']);
  });

  it('JSON 只认 true/false/null 与字符串,不做注释', () => {
    const tokens = kinds('{"a": true, "b": null, "c": 1}', 'json');
    expect(tokens).toContainEqual(['string', '"a"']);
    expect(tokens).toContainEqual(['keyword', 'true']);
    expect(tokens).toContainEqual(['keyword', 'null']);
    expect(tokens).toContainEqual(['number', '1']);
    expect(tokens.some(([kind]) => kind === 'comment')).toBe(false);
  });

  it('未闭合字符串/注释不崩,尾段原样输出', () => {
    const tokens = kinds("const s = 'never closed", 'ts');
    expect(tokens.map(([, text]) => text).join('')).toBe(
      "const s = 'never closed",
    );
    const unterminated = kinds('/* open', 'ts');
    expect(unterminated).toEqual([['comment', '/* open']]);
  });

  it('未知/缺失语言整段原样返回', () => {
    expect(highlightCode('select 1;', 'sql')).toEqual([
      { kind: 'plain', text: 'select 1;' },
    ]);
    expect(highlightCode('plain text')).toEqual([
      { kind: 'plain', text: 'plain text' },
    ]);
    expect(highlightCode('', 'ts')).toEqual([]);
  });
});

describe('language helpers', () => {
  it('从 className 提取语言并归一别名', () => {
    expect(languageFromClassName('language-ts extra')).toBe('ts');
    expect(languageFromClassName('foo')).toBeNull();
    expect(languageFromClassName(undefined)).toBeNull();
    expect(normalizeLanguage('TSX')).toBe('typescript');
    expect(normalizeLanguage('shell')).toBe('bash');
    expect(normalizeLanguage('sql')).toBeNull();
  });
});
