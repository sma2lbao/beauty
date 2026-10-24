#!/usr/bin/env node
/**
 * check-docs-links.cjs —— W1 R-16 文档链接检查（check-docs.sh 的第 2-4 项）。
 *
 * 扫描 docs 下全部 Markdown：
 *   A. 相对链接目标存在（文件或目录；http/mailto 跳过）；
 *   B. 带 anchor 的链接：在目标文件内解析标题锚点，锚点不存在即失败；
 *   C. 归档防护：非归档文档不得把 archive 目录下文件当作现行依据链接
 *      （防双真源回流，playbook §12.3；归档文件彼此间历史引用不受限）。
 *
 * 零依赖（Node 内置 fs/path），只检查圆括号形式 Markdown 链接。
 */
'use strict';

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const DOCS = path.join(ROOT, 'docs');
const problems = [];

function walk(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(p, out);
    else if (entry.name.endsWith('.md')) out.push(p);
  }
  return out;
}

/** GitHub 风格标题锚点：小写、去 markdown 记号与标点、空白转连字符、中文保留。 */
function headingSlug(heading) {
  return heading
    .trim()
    .toLowerCase()
    .replace(/[\`*_~]/g, '')
    .replace(/[^\p{L}\p{N}\s-]/gu, '')
    .replace(/\s+/g, '-');
}

/** 提取文件全部标题锚点（跳过代码围栏内的 # 行）。 */
function fileHeadings(file) {
  const slugs = new Set();
  let inCodeFence = false;
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    if (/^\`\`\`/.test(line.trim())) {
      inCodeFence = !inCodeFence;
      continue;
    }
    if (inCodeFence) continue;
    const m = /^(#{1,6})\s+(.*)$/.exec(line);
    if (m) slugs.add(headingSlug(m[2].replace(/\s*#+\s*$/, '')));
  }
  return slugs;
}

const files = walk(DOCS).sort();
const linkRe = /\]\(([^\s)]+)\)/g;

for (const file of files) {
  const rel = path.relative(ROOT, file);
  const text = fs.readFileSync(file, 'utf8');
  const isArchived = rel.includes('docs/archive/');
  let m;
  while ((m = linkRe.exec(text))) {
    const raw = m[1];
    if (/^(https?:|mailto:)/i.test(raw)) continue;
    const [targetPart, anchorPart] = raw.split(/#(.+)/, 2);
    const anchor = anchorPart || null;

    // A. 目标存在性（裸 #x 为页内锚点，交给 B）
    if (targetPart) {
      const abs = path.resolve(path.dirname(file), targetPart);
      if (!fs.existsSync(abs)) {
        problems.push(rel + ': 断链 -> (' + raw + ')');
        continue;
      }
    }

    // B. 锚点存在性（file#x 或同页 #x）
    if (anchor) {
      let anchorFile = file;
      if (targetPart) {
        const abs = path.resolve(path.dirname(file), targetPart);
        if (fs.existsSync(abs) && fs.statSync(abs).isFile()) anchorFile = abs;
      }
      const slugs = fileHeadings(anchorFile);
      if (!slugs.has(anchor)) {
        problems.push(
          rel +
            ': 锚点不存在 -> (' +
            raw +
            ')，目标 ' +
            path.relative(ROOT, anchorFile),
        );
      }
    }

    // C. 归档防护：现行文档把 archive 文件链在「当前/唯一/真源」行上即失败
    if (!isArchived && /archive\//.test(targetPart)) {
      const lineStart = text.lastIndexOf('\n', m.index) + 1;
      const lineEnd = text.indexOf('\n', m.index);
      const line = text.slice(lineStart, lineEnd === -1 ? undefined : lineEnd);
      if (/当前|唯一|真源/.test(line)) {
        problems.push(rel + ': 归档文件被当作现行依据 -> (' + raw + ')');
      }
    }
  }
}

if (problems.length > 0) {
  for (const p of problems) console.error('  ' + p);
  console.error('链接检查失败: ' + problems.length + ' 处');
  process.exit(1);
}
console.log('链接检查通过: ' + files.length + ' 个文件');
