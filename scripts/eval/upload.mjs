#!/usr/bin/env node
/**
 * 把最近一次 evalkit JSON 报告上报到 eval-service(M1 服务端索引)。
 *
 * evalkit 保持零网络:报告仍落 reports/eval/*.json,本脚本是 CI 与服务之间的
 * 唯一网络步骤。除报告外还带上 evals/baselines/<suite>.json,让服务端在入库时
 * 计算基线漂移并回填到报告快照;这样网页不必自己比较基线。
 *
 * 用法:
 *   EVAL_INTERNAL_SECRET=... node scripts/eval/upload.mjs
 *   node scripts/eval/upload.mjs --optional        # 服务未启动时跳过(本地/预览)
 *   node scripts/eval/upload.mjs --report reports/eval/xxx.json --url http://127.0.0.1:8009
 */
import { execFileSync } from 'node:child_process';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { basename, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const args = parseArgs(process.argv.slice(2));
const reportsDir = resolve(repoRoot, args.reports ?? 'reports/eval');
const baselinesDir = resolve(repoRoot, args.baselines ?? 'evals/baselines');
const url = String(args.url ?? process.env.EVAL_SERVICE_URL ?? 'http://127.0.0.1:8009');
const secret = String(process.env.EVAL_INTERNAL_SECRET ?? '');
const optional = args.optional === true;

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

function latestReport() {
  if (args.report) return resolve(repoRoot, String(args.report));
  if (!existsSync(reportsDir)) return null;
  const files = readdirSync(reportsDir)
    .filter((name) => /^\d{8}T\d{6}Z\.json$/.test(name))
    .sort();
  return files.length ? join(reportsDir, files[files.length - 1]) : null;
}

function loadBaselines(suites) {
  const baselines = {};
  for (const suite of suites) {
    const path = join(baselinesDir, `${suite.name}.json`);
    if (!existsSync(path)) continue;
    baselines[suite.name] = JSON.parse(readFileSync(path, 'utf8'));
  }
  return baselines;
}

function prNumber() {
  const explicit = process.env.EVAL_PR ?? process.env.GITHUB_PR_NUMBER;
  if (explicit) return Number(explicit) || null;
  const match = /^refs\/pull\/(\d+)\//.exec(process.env.GITHUB_REF ?? '');
  return match ? Number(match[1]) : null;
}

async function main() {
  const path = latestReport();
  if (!path || !existsSync(path)) {
    const message = '没有找到 reports/eval/*.json,先运行 bash scripts/eval/platform.sh run';
    if (optional) {
      console.log(`[eval-upload] 跳过:${message}`);
      return 0;
    }
    console.error(`[eval-upload] ${message}`);
    return 1;
  }
  if (!secret) {
    const message = '缺少 EVAL_INTERNAL_SECRET,拒绝匿名上报';
    if (optional) {
      console.log(`[eval-upload] 跳过:${message}`);
      return 0;
    }
    console.error(`[eval-upload] ${message}`);
    return 1;
  }

  const report = JSON.parse(readFileSync(path, 'utf8'));
  const suites = Array.isArray(report.suites) ? report.suites : [];
  const externalId =
    process.env.EVAL_EXTERNAL_ID ??
    (process.env.GITHUB_RUN_ID
      ? `${process.env.GITHUB_RUN_ID}-${process.env.GITHUB_RUN_ATTEMPT ?? '1'}`
      : basename(path, '.json'));

  const envelope = {
    external_id: externalId,
    trigger: process.env.EVAL_TRIGGER ?? 'ci',
    git_sha: process.env.GITHUB_SHA ?? gitOutput(['rev-parse', 'HEAD']),
    branch:
      process.env.GITHUB_REF_NAME ?? gitOutput(['branch', '--show-current']),
    pr: prNumber(),
    model: process.env.EVAL_MODEL ?? 'scripted',
    environment: process.env.EVAL_ENV ?? 'ci',
    report,
    baselines: loadBaselines(suites),
  };

  let response;
  try {
    response = await fetch(`${url.replace(/\/$/, '')}/v1/runs`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-internal-secret': secret,
      },
      body: JSON.stringify(envelope),
    });
  } catch (error) {
    const message = `连接 eval-service 失败(${url}):${error.message}`;
    if (optional) {
      console.log(`[eval-upload] 跳过:${message}`);
      return 0;
    }
    console.error(`[eval-upload] ${message}`);
    return 1;
  }

  const body = await response.text();
  if (!response.ok) {
    const message = `上报失败 HTTP ${response.status}:${body.slice(0, 500)}`;
    if (optional && response.status >= 500) {
      console.log(`[eval-upload] 跳过:${message}`);
      return 0;
    }
    console.error(`[eval-upload] ${message}`);
    return 1;
  }

  const run = JSON.parse(body);
  console.log(
    `[eval-upload] run ${run.id} 已入库:套件 ${suites.length},漂移 ${run.drift_count ?? 0},失败 ${run.totals?.failed ?? 0}`,
  );
  return 0;
}

process.exit(await main());
