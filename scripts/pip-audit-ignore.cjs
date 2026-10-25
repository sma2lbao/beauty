#!/usr/bin/env node
/**
 * pip-audit-ignore.cjs —— 从 security/audit-allowlist.json 生成 pip-audit 的 --ignore-vuln 参数。
 *
 * 规则:
 *   1) 只取 ecosystem === 'pip' 且未过期(expires >= 今天)的条目;
 *   2) 输出形如 `--ignore-vuln CVE-2026-112907`(每行一个参数,供 shell 直接拼到命令尾部);
 *   3) 过期条目由 check-audit-allowlist.cjs 负责失败,这里与之互补(不静默放行过期项)。
 * 零依赖(Node 内置 fs)。用法: uvx pip-audit -r req.txt --no-deps $(node scripts/pip-audit-ignore.cjs)
 */
'use strict';

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const FILE = path.join(ROOT, 'security', 'audit-allowlist.json');
const today = new Date().toISOString().slice(0, 10);

const doc = JSON.parse(fs.readFileSync(FILE, 'utf8'));
const entries = Array.isArray(doc.entries) ? doc.entries : [];
const flags = [];

for (const entry of entries) {
  if (entry.ecosystem !== 'pip') continue;
  if (!entry.id || !entry.expires) continue;
  if (entry.expires < today) continue; // 过期项不放行,交给校验脚本报错
  flags.push('--ignore-vuln', entry.id);
}

process.stdout.write(flags.join(' '));