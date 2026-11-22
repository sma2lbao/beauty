#!/usr/bin/env node
/**
 * check-doc-drift.cjs —— 文档漂移门禁（check-docs.sh 的第 5 项）。
 *
 * 文档里的"事实"必须能从代码/配置机械核对，否则会在重构后静默过期。
 * 本脚本只校验可判定的单一真源，不评审文风：
 *
 *   1. 服务端口：AGENTS.md 项目表、architecture.md 服务表、local-dev.md 端口表、
 *      AGENTS.md 的 serve 注释，全部对齐 scripts/dev.sh（端口真源）；
 *   2. Python 覆盖率阈值：AGENTS.md 的清单对齐各 pyproject.toml 的 --cov-fail-under；
 *   3. CI 规模：AGENTS.md 的 job 总数与各 matrix 数量对齐 .github/workflows/ci.yml；
 *   4. 前端业务域数量：AGENTS.md 与 apps/web/README.md 对齐 apps/web/src/features 目录数；
 *   5. 版本字段数量：AGENTS.md 对齐 scripts/check-versions.sh 的断言清单；
 *   6. 已删除路径：现行文档不得再引用（如 packages/console-ui）；
 *   7. 工程文档里的仓库路径必须存在（apps/ packages/ tools/ deploy/ 与
 *      services/<已知服务>/ 开头的完整路径；允许 .ts/.tsx 等扩展名互换）。
 *
 * 约定：这里只检查"当前事实型"工程文档。archive/、gap-2026-10/、
 * refactoring/rfc|retro|verification|baseline（时点快照）与设计文档的
 * 实施记录不做路径校验——它们记录当时状态，改动反而破坏历史。
 */
'use strict';

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
process.chdir(ROOT);

const problems = [];
function fail(msg) {
  problems.push(msg);
}
function read(file) {
  return fs.readFileSync(file, 'utf8');
}
function exists(p) {
  try {
    fs.accessSync(p);
    return true;
  } catch {
    return false;
  }
}
function listDirs(dir) {
  if (!exists(dir)) return [];
  return fs
    .readdirSync(dir, { withFileTypes: true })
    .filter((e) => e.isDirectory())
    .map((e) => e.name)
    .sort();
}

// ── 1. 服务端口：scripts/dev.sh 是真源 ─────────────────────────────────────
const devSh = read('scripts/dev.sh');
const servicePorts = new Map();
for (const m of devSh.matchAll(/"([a-z][a-z0-9-]*)\|(\d{4})\|([^"]+)"/g)) {
  servicePorts.set(m[1], { port: m[2], health: m[3] });
}
if (servicePorts.size !== 8) {
  fail('scripts/dev.sh 解析出 ' + servicePorts.size + ' 个服务，期望 8 个（解析规则可能失效）');
}

const agentsMd = read('AGENTS.md');
for (const [svc, info] of servicePorts) {
  const rowRe = new RegExp('^\\| \x60services/' + svc + '\x60[^\\n]*$', 'm');
  const row = rowRe.exec(agentsMd);
  if (!row) {
    fail('AGENTS.md 项目表缺服务 ' + svc + '（dev.sh 端口 ' + info.port + '）');
  } else if (!row[0].includes(':' + info.port)) {
    fail('AGENTS.md 项目表 ' + svc + ' 端口与 dev.sh 不一致（期望 :' + info.port + '）');
  }
  const serveRe = new RegExp('^npx nx run ' + svc + ':serve\\s+#[^\\n]*:(\\d{4})', 'm');
  const serve = serveRe.exec(agentsMd);
  if (serve && serve[1] !== info.port) {
    fail('AGENTS.md serve 注释 ' + svc + ' 端口 ' + serve[1] + '，dev.sh 是 ' + info.port);
  }
}

const archMd = read('docs/architecture.md');
for (const [svc, info] of servicePorts) {
  const rowRe = new RegExp('^\\| (?:services/)?' + svc + '\\s+\\| 容器 :(\\d{4})', 'm');
  const row = rowRe.exec(archMd);
  if (!row) {
    fail('docs/architecture.md 服务表缺 ' + svc + ' 的「容器 :端口」行');
  } else if (row[1] !== info.port) {
    fail('docs/architecture.md 中 ' + svc + ' 端口 ' + row[1] + '，dev.sh 是 ' + info.port);
  }
}

const localDevMd = read('docs/local-dev.md');
for (const [svc, info] of servicePorts) {
  const rowRe = new RegExp('^\\| ' + svc + '\\s+\\| (\\d{4}) ', 'm');
  const row = rowRe.exec(localDevMd);
  if (!row) {
    fail('docs/local-dev.md 端口表缺 ' + svc);
  } else if (row[1] !== info.port) {
    fail('docs/local-dev.md 中 ' + svc + ' 端口 ' + row[1] + '，dev.sh 是 ' + info.port);
  }
}

// ── 2. Python 覆盖率阈值 ──────────────────────────────────────────────────
const covLine = /覆盖率阈值:Python \d+ 个项目[^\n]*/.exec(agentsMd);
if (!covLine) {
  fail('AGENTS.md 找不到「覆盖率阈值:Python N 个项目…」清单');
} else {
  const documented = new Map();
  for (const m of covLine[0].matchAll(/([a-z][a-z0-9-]+) (\d+)(?= \/|\)|;)/g)) {
    documented.set(m[1], m[2]);
  }
  const pyProjects = listDirs('services')
    .map((d) => 'services/' + d)
    .concat(listDirs('packages').map((d) => 'packages/' + d))
    .filter((d) => exists(path.join(d, 'pyproject.toml')));
  for (const dir of pyProjects) {
    // 文档里服务名习惯省略 -service 后缀（connector 91），两种写法都接受
    const dirName = path.basename(dir);
    const name = documented.has(dirName) ? dirName : dirName.replace(/-service$/, '');
    const cfg = read(path.join(dir, 'pyproject.toml'));
    const m = /--cov-fail-under=(\d+)/.exec(cfg);
    if (!m) {
      fail(dir + '/pyproject.toml 没有 --cov-fail-under（AGENTS.md 声称 11 个项目都有）');
      continue;
    }
    if (!documented.has(name)) {
      fail('AGENTS.md 覆盖率清单缺 ' + name + '（pyproject 阈值 ' + m[1] + '）');
    } else if (documented.get(name) !== m[1]) {
      fail('AGENTS.md 覆盖率 ' + name + '=' + documented.get(name) + '，pyproject 是 ' + m[1]);
    }
  }
}

// ── 3. CI 规模 ────────────────────────────────────────────────────────────
const ciYml = read('.github/workflows/ci.yml');
const jobsBlock = ciYml.slice(ciYml.indexOf('\njobs:'));
const jobNames = [...jobsBlock.matchAll(/^  ([a-z0-9-]+):$/gm)].map((m) => m[1]);
const documentedJobs = /共 (\d+) job/.exec(agentsMd);
if (!documentedJobs) {
  fail('AGENTS.md 找不到「共 N job」的 CI 规模描述');
} else if (Number(documentedJobs[1]) !== jobNames.length) {
  fail('AGENTS.md 说 CI 共 ' + documentedJobs[1] + ' job，ci.yml 实际 ' + jobNames.length + ' 个');
}

function matrixCount(startMarker, endMarker, what) {
  const start = ciYml.indexOf(startMarker);
  const end = ciYml.indexOf(endMarker, start + 1);
  if (start < 0 || end < 0) {
    fail('ci.yml 找不到 ' + startMarker + ' ~ ' + endMarker + ' 区段（' + what + '）');
    return null;
  }
  return { seg: ciYml.slice(start, end), end };
}

const lint = matrixCount('python-lint:', 'python-test:', 'python-lint matrix');
if (lint) {
  const m = /project:\s*\n\s*\[([\s\S]*?)\]/.exec(lint.seg);
  const n = m ? m[1].split(',').map((s) => s.trim()).filter(Boolean).length : -1;
  const doc = /python-lint×(\d+)/.exec(agentsMd);
  if (n < 0) fail('ci.yml 的 python-lint matrix 解析失败');
  else if (doc && Number(doc[1]) !== n) fail('AGENTS.md 说 python-lint×' + doc[1] + '，ci.yml 实际 ' + n);
  if (process.env.DOC_DRIFT_VERBOSE) console.log('python-lint matrix =', n);
}

const test = matrixCount('python-test:', 'lock-check:', 'python-test matrix');
if (test) {
  const m = /project:\s*\n\s*\[([\s\S]*?)\]/.exec(test.seg);
  const n = m ? m[1].split(',').map((s) => s.trim()).filter(Boolean).length : -1;
  const doc = /python-test×(\d+)/.exec(agentsMd);
  if (n < 0) fail('ci.yml 的 python-test matrix 解析失败');
  else if (doc && Number(doc[1]) !== n) fail('AGENTS.md 说 python-test×' + doc[1] + '，ci.yml 实际 ' + n);
}

const alembic = matrixCount('alembic-check:', 'docker-build:', 'alembic-check matrix');
if (alembic) {
  const n = (alembic.seg.match(/- service:/g) || []).length;
  const doc = /alembic-check×(\d+)/.exec(agentsMd);
  if (doc && Number(doc[1]) !== n) fail('AGENTS.md 说 alembic-check×' + doc[1] + '，ci.yml 实际 ' + n);
}

// ── 4. 前端业务域数量 ─────────────────────────────────────────────────────
const featureDirs = listDirs('apps/web/src/features');
const agentsDomain = /\((\d+) 个域/.exec(agentsMd);
if (!agentsDomain) fail('AGENTS.md 找不到「(N 个域」描述');
else if (Number(agentsDomain[1]) !== featureDirs.length) {
  fail('AGENTS.md 说 ' + agentsDomain[1] + ' 个业务域，apps/web/src/features 实际 ' + featureDirs.length + ' 个');
}
const webReadme = read('apps/web/README.md');
const webDomain = /业务域（(\d+) 个）/.exec(webReadme);
if (!webDomain) fail('apps/web/README.md 找不到「业务域（N 个）」描述');
else if (Number(webDomain[1]) !== featureDirs.length) {
  fail('apps/web/README.md 说 ' + webDomain[1] + ' 个业务域，实际 ' + featureDirs.length + ' 个');
}

// ── 5. 版本字段数量 ───────────────────────────────────────────────────────
const versionsSh = read('scripts/check-versions.sh');
function arrayLen(name) {
  const start = versionsSh.indexOf(name + '=(');
  const end = versionsSh.indexOf('\n)', start);
  if (start < 0 || end < 0) return -1;
  return versionsSh
    .slice(start, end)
    .split('\n')
    .filter((line) => /^\s+[^\s#]/.test(line)).length;
}
const jsCount = arrayLen('JS_FILES');
const pyCount = arrayLen('PY_FILES');
const totalVersions = jsCount + pyCount;
const docVersions = /(\d+) 处版本字段/.exec(agentsMd);
if (!docVersions) fail('AGENTS.md 找不到「N 处版本字段」描述');
else if (Number(docVersions[1]) !== totalVersions) {
  fail('AGENTS.md 说 ' + docVersions[1] + ' 处版本字段，check-versions.sh 实际 ' + totalVersions + ' 处（JS ' + jsCount + ' + Python ' + pyCount + '）');
}
const shVersions = /断言: 1\) (\d+) 处版本字段/.exec(versionsSh);
if (shVersions && Number(shVersions[1]) !== totalVersions) {
  fail('check-versions.sh 头部说 ' + shVersions[1] + ' 处版本字段，数组实际 ' + totalVersions + ' 处');
}

// ── 6. 已删除路径：现行文档不得再引用 ─────────────────────────────────────
const liveDocs = ['README.md', 'AGENTS.md'];
function walkDocs(dir, out) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === 'archive') continue;
      walkDocs(p, out);
    } else if (entry.name.endsWith('.md')) {
      out.push(p);
    }
  }
  return out;
}
const docsFiles = walkDocs('docs', []);
const readmeFiles = [];
for (const base of ['apps', 'packages', 'services', 'deploy']) {
  for (const dir of listDirs(base)) {
    const p = path.join(base, dir, 'README.md');
    if (exists(p)) readmeFiles.push(p);
  }
}
// 时点快照（归档/旧 gap/重构 RFC·复盘·基线）只记录当时状态，不做现状校验
const snapshotDocs = [
  /(^|\/)archive\//,
  /^docs\/gap-2026-10\//,
  /^docs\/refactoring\/(baseline\.md|rfc\/|retro\/|verification\/|roadmap\.md)/,
];
function isSnapshot(file) {
  return snapshotDocs.some((re) => re.test(file.replace(/\\/g, '/')));
}

const forbidden = [
  { pattern: /packages\/console-ui/, why: 'packages/console-ui 已删除（W6 收敛进 apps/web/src/components/console 与 packages/design）' },
];
for (const file of liveDocs.concat(docsFiles, readmeFiles)) {
  if (isSnapshot(file)) continue; // 时点快照：记录当时路径，不做现状校验
  const text = read(file);
  for (const item of forbidden) {
    if (item.pattern.test(text)) {
      const lines = text
        .split('\n')
        .map((line, i) => [i + 1, line])
        .filter(([, line]) => item.pattern.test(line))
        .map(([n]) => n);
      fail(file + ':' + lines.join(',') + ' 仍引用已删除路径：' + item.why);
    }
  }
}

// ── 7. 工程文档里的仓库路径必须存在 ───────────────────────────────────────
const engineeringDocs = new Set(['README.md', 'AGENTS.md', 'docs/README.md', 'docs/architecture.md', 'docs/local-dev.md', 'docs/security.md', 'docs/observability.md', 'docs/release.md']);
for (const f of readmeFiles) engineeringDocs.add(f);
for (const f of walkDocs('deploy', [])) engineeringDocs.add(f);

const serviceDirs = new Set(listDirs('services'));
const extFallbacks = {
  '.ts': ['.tsx', '.js'],
  '.tsx': ['.ts', '.js'],
  '.js': ['.mjs', '.cjs', '.ts'],
  '.mjs': ['.js', '.cjs'],
};
const pathRe = /(?:^|[\s\x60(（\["'])((?:apps|packages|tools|deploy)\/[A-Za-z0-9_./-]+|services\/[a-z][a-z0-9-]*\/[A-Za-z0-9_./-]+)/g;
for (const file of engineeringDocs) {
  const text = read(file);
  for (const m of text.matchAll(pathRe)) {
    let p = m[1].replace(/[.,;:)\]}"']+$/, '');
    if (!p || /[*<>{}]/.test(p)) continue;
    const base = path.basename(p);
    if (base.startsWith('.env') || base.endsWith('.override.yml')) continue; // 运行期生成/可选的本地文件
    if (p.startsWith('services/')) {
      const svc = p.split('/')[1];
      if (!serviceDirs.has(svc)) continue; // 模块相对路径（如 services/run.py），不是仓库路径
    }
    if (p.startsWith('tools/')) {
      const tool = p.split('/')[1];
      if (!listDirs('tools').includes(tool)) continue; // MCP 方法名（tools/list）不是仓库路径
    }
    if (exists(p)) continue;
    const parsed = path.parse(p);
    const swapped = (extFallbacks[parsed.ext] || []).map((ext) => path.join(parsed.dir, parsed.name + ext));
    if (swapped.some((candidate) => exists(candidate))) continue;
    fail(file + ' 引用的路径不存在：' + p);
  }
}

// ── 汇总 ──────────────────────────────────────────────────────────────────
if (problems.length === 0) {
  console.log('[PASS] doc drift (' + servicePorts.size + ' 服务端口 / ' + featureDirs.length + ' 业务域 / ' + totalVersions + ' 版本字段)');
  process.exit(0);
}
for (const p of problems) console.error('[FAIL] ' + p);
console.error('== doc drift: ' + problems.length + ' 处不一致 ==');
process.exit(1);
