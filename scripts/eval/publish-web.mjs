#!/usr/bin/env node
/**
 * 把 evalkit 的 JSON 报告发布成评测页可消费的静态快照。
 *
 * evalkit 本身保持零网络、只落 reports/eval/*.json;网页只读
 * /eval/index.json 与 /eval/runs/<id>.json。本脚本是两者之间的发布步骤:
 * - 取最近 N 次运行,生成运行索引(套件摘要、失败用例、git 元数据);
 * - 每份报告裁掉超长 prompt(默认 2000 字符)与过长事件列表,避免把
 *   MB 级输入直接塞进前端包;
 * - 不修改原始报告,原始文件仍由 CI 归档。
 *
 * 用法:
 *   node scripts/eval/publish-web.mjs
 *   node scripts/eval/publish-web.mjs --limit 3 --trigger ci
 *   node scripts/eval/publish-web.mjs --reports reports/eval --out apps/web/public/eval
 */
import { execFileSync } from 'node:child_process';
import {
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { basename, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const args = parseArgs(process.argv.slice(2));
const reportsDir = resolve(repoRoot, args.reports ?? 'reports/eval');
const outDir = resolve(repoRoot, args.out ?? 'apps/web/public/eval');
const limit = clampNumber(Number(args.limit ?? 5), 1, 50, 5);
const promptLimit = clampNumber(
  Number(args['prompt-limit'] ?? 2000),
  200,
  20000,
  2000,
);

function parseArgs(argv) {
  const parsed = {};
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token.startsWith('--')) continue;
    const pieces = token.slice(2).split('=');
    const key = pieces[0];
    const inline = pieces.length > 1 ? pieces.slice(1).join('=') : undefined;
    if (inline !== undefined) {
      parsed[key] = inline;
      continue;
    }
    const next = argv[index + 1];
    if (next && !next.startsWith('--')) {
      parsed[key] = next;
      index += 1;
    } else {
      parsed[key] = true;
    }
  }
  return parsed;
}

function clampNumber(value, min, max, fallback) {
  if (!Number.isFinite(value)) return fallback;
  return Math.min(max, Math.max(min, Math.trunc(value)));
}

function gitOutput(gitArgs) {
  try {
    return execFileSync('git', gitArgs, {
      cwd: repoRoot,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim();
  } catch {
    return '';
  }
}

function trimText(value, max) {
  if (typeof value !== 'string' || value.length <= max) return value;
  return value.slice(0, max) + '\n…(已截断,原始长度 ' + value.length + ' 字符)';
}

/** 只裁展示层大字段;报告结构的其余部分原样保留。 */
function trimReport(report) {
  for (const suite of report.suites ?? []) {
    for (const testCase of suite.cases ?? []) {
      const fields = testCase.fields;
      if (!fields || typeof fields !== 'object') continue;
      for (const key of Object.keys(fields)) {
        if (key.startsWith('prompt_') || key.startsWith('reply_')) {
          fields[key] = trimText(fields[key], promptLimit);
        }
      }
      if (Array.isArray(testCase.events) && testCase.events.length > 50) {
        testCase.events = testCase.events.slice(0, 50);
      }
    }
  }
  return report;
}

function toRunEntry(report, file) {
  const suites = (report.suites ?? []).map((suite) => ({
    name: suite.name,
    dataset: suite.dataset ?? null,
    dataset_version: suite.dataset_version ?? null,
    totals: suite.totals ?? {
      cases: 0,
      passed: 0,
      failed: 0,
      pass_rate: 0,
      score: 0,
      tokens: 0,
      duration_ms: 0,
    },
    drift_count: 0,
  }));

  const failedCases = [];
  for (const suite of report.suites ?? []) {
    for (const testCase of suite.cases ?? []) {
      if (testCase.ok) continue;
      const failures = Array.isArray(testCase.failures)
        ? testCase.failures.filter(Boolean)
        : [];
      failedCases.push({
        suite: suite.name,
        case_id: testCase.id,
        failures: failures.length
          ? failures
          : [testCase.error ?? '未提供失败原因'],
      });
    }
  }

  const datasetVersions = Object.fromEntries(
    suites.map((suite) => [suite.name, suite.dataset_version]),
  );

  return {
    id: basename(file, '.json'),
    created_at: report.generated_at,
    trigger: args.trigger ?? process.env.EVAL_TRIGGER ?? 'local',
    git_sha:
      args.sha ??
      process.env.GITHUB_SHA?.slice(0, 7) ??
      gitOutput(['rev-parse', '--short', 'HEAD']),
    branch:
      args.branch ??
      process.env.GITHUB_REF_NAME ??
      gitOutput(['rev-parse', '--abbrev-ref', 'HEAD']),
    pr: process.env.EVAL_PR ? Number(process.env.EVAL_PR) : null,
    model: args.model ?? process.env.EVAL_MODEL ?? 'scripted',
    environment: args.environment ?? process.env.EVAL_ENV ?? 'local',
    report_version: report.version ?? 1,
    totals: report.totals,
    drift_count: 0,
    suites,
    failed_cases: failedCases,
    dataset_versions: datasetVersions,
  };
}

if (!existsSync(reportsDir)) {
  console.error('找不到报告目录:' + reportsDir);
  process.exit(1);
}

const files = readdirSync(reportsDir)
  .filter((name) => name.endsWith('.json'))
  .sort()
  .reverse()
  .slice(0, limit);

if (files.length === 0) {
  console.error(
    reportsDir + ' 下没有 JSON 报告,先运行 bash scripts/eval/platform.sh run',
  );
  process.exit(1);
}

const runsDir = join(outDir, 'runs');
rmSync(runsDir, { force: true, recursive: true });
mkdirSync(runsDir, { recursive: true });

const runs = [];
for (const file of files) {
  const report = JSON.parse(readFileSync(join(reportsDir, file), 'utf8'));
  if (!Array.isArray(report.suites)) {
    console.warn('跳过 ' + file + ':不是 evalkit 报告');
    continue;
  }
  const run = toRunEntry(report, file);
  writeFileSync(
    join(runsDir, run.id + '.json'),
    JSON.stringify(trimReport(report), null, 2) + '\n',
  );
  runs.push(run);
}

const index = {
  version: 1,
  generated_at: new Date().toISOString(),
  runs,
};

writeFileSync(
  join(outDir, 'index.json'),
  JSON.stringify(index, null, 2) + '\n',
);

console.log(
  '已发布 ' +
    runs.length +
    ' 次运行到 ' +
    outDir +
    ': index.json + runs/*.json(最近 ' +
    (runs[0]?.id ?? '-') +
    ')',
);
