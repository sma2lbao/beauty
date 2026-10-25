#!/usr/bin/env node
/**
 * check-audit-allowlist.cjs —— W4 R-35 允许清单校验。
 *
 * 规则:
 *   1) security/audit-allowlist.json 可解析且 entries 为数组;
 *   2) 每条必含 id/advisory/ecosystem/reason/owner/expires;
 *   3) expires 未过期(过期即失败——强制每条允许都带期限,不允许无限期豁免);
 *   4) id 形如 GHSA-xxxx-xxxx-xxxx / OSV-xxx(防拼错的占位条目)。
 * 零依赖(Node 内置 fs)。过期或字段缺失退出 1。
 */
'use strict';

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const FILE = path.join(ROOT, 'security', 'audit-allowlist.json');
const REQUIRED = ['id', 'advisory', 'ecosystem', 'reason', 'owner', 'expires'];
const ID_RE = /^(GHSA|OSV|RUSTSEC|PYSEC)-[a-z0-9-]+$/i;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

let doc;
try {
  doc = JSON.parse(fs.readFileSync(FILE, 'utf8'));
} catch (error) {
  console.error('[FAIL] allowlist 不可解析: ' + error.message);
  process.exit(1);
}

const entries = Array.isArray(doc.entries) ? doc.entries : null;
if (!entries) {
  console.error('[FAIL] allowlist.entries 必须是数组');
  process.exit(1);
}

const problems = [];
const today = new Date().toISOString().slice(0, 10);

for (const entry of entries) {
  const label = entry.id || '(missing id)';
  for (const key of REQUIRED) {
    if (!entry[key]) {
      problems.push(label + ': 缺字段 ' + key);
    }
  }
  if (entry.id && !ID_RE.test(entry.id)) {
    problems.push(entry.id + ': id 不是 GHSA/OSV 等标准格式');
  }
  if (entry.expires && !DATE_RE.test(entry.expires)) {
    problems.push(label + ': expires 不是 YYYY-MM-DD');
  }
  if (entry.expires && entry.expires < today) {
    problems.push(label + ': 已过期(' + entry.expires + '),请修复或更新清单');
  }
}

if (problems.length > 0) {
  for (const p of problems) console.error('  [FAIL] ' + p);
  console.error('允许清单校验失败: ' + problems.length + ' 处');
  process.exit(1);
}

console.log('允许清单校验通过: ' + entries.length + ' 条,全部在有效期内');
