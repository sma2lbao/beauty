import GithubSlugger from 'github-slugger';

export interface TocItem {
  id: string;
  text: string;
  depth: 2 | 3;
}

/**
 * 只用 2/3 级标题生成目录,并与 rehype-slug(github-slugger)保持同一套 id。
 * 跳过代码块,避免把代码注释里的 # 当成标题。
 */
export function extractToc(markdown: string): TocItem[] {
  const slugger = new GithubSlugger();
  const items: TocItem[] = [];
  let inFence = false;

  for (const line of markdown.split('\n')) {
    if (/^\s*```/.test(line)) {
      inFence = !inFence;
      continue;
    }
    if (inFence) {
      continue;
    }

    const match = /^(#{2,3})\s+(.+?)\s*#*\s*$/.exec(line);
    if (!match) {
      continue;
    }

    const depth = match[1].length as 2 | 3;
    const text = match[2]
      .replace(/`([^`]+)`/g, '$1')
      .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
      .trim();
    if (!text) {
      continue;
    }

    items.push({ id: slugger.slug(text), text, depth });
  }

  return items;
}
