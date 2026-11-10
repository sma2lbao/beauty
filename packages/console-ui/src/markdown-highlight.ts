/**
 * 代码块轻量高亮:不引入 highlight.js/shiki,按语言做单遍扫描 token 化。
 *
 * 只解决"大段代码不可读"的问题:关键字/字符串/注释/数字四类 + 其余原样。
 * 未知语言或无语言时整段原样返回(调用方直接当纯文本渲染)。
 * 目录里流式输出经常是半截代码,扫描器必须容忍未闭合的字符串/注释。
 */

export type CodeTokenKind =
  | 'keyword'
  | 'string'
  | 'comment'
  | 'number'
  | 'plain';

export interface CodeToken {
  text: string;
  kind: CodeTokenKind;
}

type Language = 'javascript' | 'typescript' | 'json' | 'python' | 'bash';

const ALIASES: Record<string, Language> = {
  js: 'javascript',
  jsx: 'javascript',
  javascript: 'javascript',
  mjs: 'javascript',
  cjs: 'javascript',
  ts: 'typescript',
  tsx: 'typescript',
  typescript: 'typescript',
  mts: 'typescript',
  cts: 'typescript',
  json: 'json',
  py: 'python',
  python: 'python',
  python3: 'python',
  sh: 'bash',
  bash: 'bash',
  shell: 'bash',
  zsh: 'bash',
};

const JS_KEYWORDS = [
  'as',
  'async',
  'await',
  'break',
  'case',
  'catch',
  'class',
  'const',
  'continue',
  'debugger',
  'default',
  'delete',
  'do',
  'else',
  'enum',
  'export',
  'extends',
  'false',
  'finally',
  'for',
  'from',
  'function',
  'if',
  'implements',
  'import',
  'in',
  'instanceof',
  'interface',
  'let',
  'new',
  'null',
  'of',
  'return',
  'satisfies',
  'static',
  'super',
  'switch',
  'this',
  'throw',
  'true',
  'try',
  'type',
  'typeof',
  'undefined',
  'var',
  'void',
  'while',
  'with',
  'yield',
];

const TS_KEYWORDS = [
  ...JS_KEYWORDS,
  'abstract',
  'declare',
  'keyof',
  'namespace',
  'never',
  'readonly',
  'unknown',
];

const PYTHON_KEYWORDS = [
  'False',
  'None',
  'True',
  'and',
  'as',
  'assert',
  'async',
  'await',
  'break',
  'class',
  'continue',
  'def',
  'del',
  'elif',
  'else',
  'except',
  'finally',
  'for',
  'from',
  'global',
  'if',
  'import',
  'in',
  'is',
  'lambda',
  'nonlocal',
  'not',
  'or',
  'pass',
  'raise',
  'return',
  'try',
  'while',
  'with',
  'yield',
];

const BASH_KEYWORDS = [
  'case',
  'do',
  'done',
  'elif',
  'else',
  'esac',
  'fi',
  'for',
  'function',
  'if',
  'in',
  'select',
  'then',
  'until',
  'while',
];

const KEYWORDS: Record<Language, Set<string>> = {
  javascript: new Set(JS_KEYWORDS),
  typescript: new Set(TS_KEYWORDS),
  json: new Set(['true', 'false', 'null']),
  python: new Set(PYTHON_KEYWORDS),
  bash: new Set(BASH_KEYWORDS),
};

/** "language-ts" → "ts";无 language 前缀返回 null。 */
export function languageFromClassName(
  className?: string | null,
): string | null {
  if (!className) return null;
  const match = /(?:^|\s)language-([\w+-]+)/.exec(className);
  return match ? match[1].toLowerCase() : null;
}

/** 把语言标记归一;不支持的语言返回 null(调用方原样渲染)。 */
export function normalizeLanguage(language?: string | null): Language | null {
  if (!language) return null;
  return ALIASES[language.toLowerCase()] ?? null;
}

const NUMBER =
  /^(?:0[xX][0-9a-fA-F]+|0[bB][01]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/;
const IDENT = /^[A-Za-z_$][\w$]*/;

export function highlightCode(
  code: string,
  language?: string | null,
): CodeToken[] {
  const lang = normalizeLanguage(language);
  if (!lang) {
    return code ? [{ text: code, kind: 'plain' }] : [];
  }
  const keywords = KEYWORDS[lang];
  const tokens: CodeToken[] = [];
  let plain = '';
  const flush = (): void => {
    if (plain) {
      tokens.push({ text: plain, kind: 'plain' });
      plain = '';
    }
  };
  const emit = (text: string, kind: CodeTokenKind): void => {
    if (!text) return;
    flush();
    tokens.push({ text, kind });
  };

  const blockComments = lang !== 'python' && lang !== 'bash' && lang !== 'json';
  const lineComments = lang === 'javascript' || lang === 'typescript';
  const hashComments = lang === 'bash';
  const backtickStrings = lang === 'javascript' || lang === 'typescript';
  const tripleQuotes = lang === 'python';

  let i = 0;
  while (i < code.length) {
    const rest = code.slice(i);
    const char = code[i];

    if (blockComments && rest.startsWith('/*')) {
      const end = code.indexOf('*/', i + 2);
      const stop = end < 0 ? code.length : end + 2;
      emit(code.slice(i, stop), 'comment');
      i = stop;
      continue;
    }
    if (lineComments && rest.startsWith('//')) {
      const end = code.indexOf('\n', i);
      const stop = end < 0 ? code.length : end;
      emit(code.slice(i, stop), 'comment');
      i = stop;
      continue;
    }
    if (
      (hashComments || (lang === 'python' && rest.startsWith('#'))) &&
      char === '#'
    ) {
      const end = code.indexOf('\n', i);
      const stop = end < 0 ? code.length : end;
      emit(code.slice(i, stop), 'comment');
      i = stop;
      continue;
    }
    if (char === '"' || char === "'" || (char === '`' && backtickStrings)) {
      const quoteLen = tripleQuotes && rest.startsWith(char.repeat(3)) ? 3 : 1;
      const quote = char.repeat(quoteLen);
      let j = i + quoteLen;
      while (j < code.length) {
        if (code[j] === '\\') {
          j += 2;
          continue;
        }
        if (code.startsWith(quote, j)) {
          j += quoteLen;
          break;
        }
        j += 1;
      }
      emit(code.slice(i, Math.min(j, code.length)), 'string');
      i = Math.min(j, code.length);
      continue;
    }
    const number = NUMBER.exec(rest);
    if (number) {
      emit(number[0], 'number');
      i += number[0].length;
      continue;
    }
    const ident = IDENT.exec(rest);
    if (ident) {
      emit(ident[0], keywords.has(ident[0]) ? 'keyword' : 'plain');
      i += ident[0].length;
      continue;
    }
    plain += char;
    i += 1;
  }
  flush();
  return tokens;
}
