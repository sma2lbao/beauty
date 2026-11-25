/**
 * 评测页数据模型:总览读运行索引,详情读单次运行快照。
 *
 * M0 数据是静态文件;后面换成 eval-service 时只替换 evalApi 的 URL,
 * 组件与 URL 过滤协议保持不变。
 */
import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router';

import { evalApi } from '@/lib/eval-api';
import type { EvalCaseWithSuite, EvalCompareStatus } from '@/lib/eval-api';
import { queryKeys } from '@/lib/query-keys';

import { buildGateRows } from './gates';

export type EvalCaseStatus = 'all' | 'passed' | 'failed';
export type EvalCaseTab = 'result' | 'trajectory' | 'io' | 'raw';
export type EvalCompareFilter = 'all' | EvalCompareStatus;

export function useEvalsModel() {
  const index = useQuery({
    queryKey: queryKeys.evals,
    queryFn: evalApi.index,
    staleTime: 60_000,
  });

  const runs = index.data?.runs ?? [];
  const latest = runs[0];
  const previous = runs[1];

  const suiteRows = (latest?.suites ?? []).map((suite) => ({
    ...suite,
    trend: runs
      .slice(0, 6)
      .reverse()
      .map(
        (run) =>
          run.suites.find((item) => item.name === suite.name)?.totals
            .pass_rate ?? null,
      ),
  }));

  return {
    index,
    runs,
    latest,
    previous,
    suiteRows,
    isLoading: index.isLoading,
    error: index.error as Error | null,
  };
}

function parseStatus(value: string | null): EvalCaseStatus {
  if (value === 'passed' || value === 'failed') return value;
  return 'all';
}

function parseTab(value: string | null): EvalCaseTab {
  if (value === 'trajectory' || value === 'io' || value === 'raw') {
    return value;
  }
  return 'result';
}

function parsePage(value: string | null): number {
  const page = Number(value);
  return Number.isInteger(page) && page > 0 ? page : 1;
}

const PAGE_SIZE = 50;

export function useEvalRunModel(runId: string) {
  const [params, setParams] = useSearchParams();
  const requestedSuite = params.get('suite');
  const status = parseStatus(params.get('status'));
  const tab = parseTab(params.get('tab'));
  const page = parsePage(params.get('page'));
  const caseId = params.get('case');

  const summary = useQuery({
    queryKey: queryKeys.evalRunSummary(runId),
    queryFn: () => evalApi.summary(runId),
    enabled: Boolean(runId),
    staleTime: 60_000,
  });

  const suites = summary.data?.suites ?? [];
  const activeSuite =
    requestedSuite ?? suites[0]?.name ?? '';

  const casesQuery = useQuery({
    queryKey: queryKeys.evalRunCases(
      runId,
      activeSuite,
      status,
      page,
    ),
    queryFn: () =>
      evalApi.cases(runId, activeSuite, {
        status: status === 'all' ? undefined : status,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      }),
    enabled: Boolean(runId && activeSuite),
    staleTime: 60_000,
  });

  const caseQuery = useQuery({
    queryKey: queryKeys.evalRunCase(runId, activeSuite, caseId ?? ''),
    queryFn: () => evalApi.case(runId, activeSuite, caseId ?? ''),
    enabled: Boolean(runId && activeSuite && caseId),
    staleTime: 60_000,
  });

  const cases = casesQuery.data?.items ?? [];
  const selectedCase =
    caseQuery.data ?? cases.find((testCase) => testCase.id === caseId);
  const total = casesQuery.data?.total ?? 0;

  function updateParams(patch: Record<string, string | null>) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(patch)) {
      if (value === null || value === '') {
        next.delete(key);
      } else {
        next.set(key, value);
      }
    }
    setParams(next, { replace: true });
  }

  function selectSuite(suite: string) {
    updateParams({ suite, case: null, tab: null, page: null });
  }

  function selectStatus(nextStatus: EvalCaseStatus) {
    updateParams({
      status: nextStatus === 'all' ? null : nextStatus,
      case: null,
      tab: null,
      page: null,
    });
  }

  function selectPage(nextPage: number) {
    updateParams({
      page: nextPage > 1 ? String(nextPage) : null,
      case: null,
      tab: null,
    });
  }

  function selectCase(testCase: EvalCaseWithSuite) {
    updateParams({ suite: testCase.suite, case: testCase.id, tab: 'result' });
  }

  function closeCase() {
    updateParams({ case: null, tab: null });
  }

  function selectTab(nextTab: EvalCaseTab) {
    updateParams({ tab: nextTab });
  }

  return {
    run: summary,
    summary,
    suite: activeSuite,
    suites,
    cases,
    casesQuery,
    caseQuery,
    selectedCase,
    total,
    page,
    pageSize: PAGE_SIZE,
    status,
    tab,
    selectSuite,
    selectStatus,
    selectCase,
    selectPage,
    closeCase,
    selectTab,
  };
}

/** 数据集只读目录:URL ?dataset= 是选中项唯一真源。 */
export function useEvalDatasetsModel() {
  const list = useQuery({
    queryKey: queryKeys.evalDatasets,
    queryFn: evalApi.datasets,
    staleTime: 60_000,
  });
  const [params, setParams] = useSearchParams();

  const items = list.data?.items ?? [];
  const requested = params.get('dataset');
  const known = items.some((item) => item.name === requested);
  const selectedName = (known ? requested : items[0]?.name) ?? '';

  const detail = useQuery({
    queryKey: queryKeys.evalDataset(selectedName),
    queryFn: () => evalApi.dataset(selectedName),
    enabled: Boolean(selectedName),
    staleTime: 60_000,
  });

  function selectDataset(name: string) {
    const next = new URLSearchParams(params);
    next.set('dataset', name);
    setParams(next, { replace: true });
  }

  return {
    list,
    items,
    selectedName,
    detail,
    selectDataset,
    isLoading: list.isLoading,
    error: list.error as Error | null,
  };
}

/** 门禁治理:已批准基线 × 最新运行的数据集版本与用例数。 */
export function useEvalGatesModel() {
  const baselines = useQuery({
    queryKey: queryKeys.evalBaselines,
    queryFn: evalApi.baselines,
    staleTime: 60_000,
  });
  const index = useQuery({
    queryKey: queryKeys.evals,
    queryFn: evalApi.index,
    staleTime: 60_000,
  });

  const latest = index.data?.runs[0];
  const rows = buildGateRows(baselines.data?.items ?? [], latest);

  return {
    baselines,
    index,
    latest,
    rows,
    isLoading: baselines.isLoading || index.isLoading,
    error:
      (baselines.error as Error | null) ??
      (index.error as Error | null) ??
      null,
  };
}

function parseCompareStatus(value: string | null): EvalCompareFilter {
  if (
    value === 'regressed' ||
    value === 'fixed' ||
    value === 'added' ||
    value === 'removed' ||
    value === 'unchanged'
  ) {
    return value;
  }
  return 'all';
}

/**
 * 跨运行对比模型:默认 candidate=最新、base=次新;单次运行时两边相同。
 * URL 是真源(?base=&candidate=&status=),深链与前进/后退都可复现。
 */
export function useEvalCompareModel() {
  const index = useQuery({
    queryKey: queryKeys.evals,
    queryFn: evalApi.index,
    staleTime: 60_000,
  });
  const [params, setParams] = useSearchParams();

  const runs = index.data?.runs ?? [];
  const latest = runs[0];
  const previous = runs[1];
  const candidateId = params.get('candidate') ?? latest?.id ?? '';
  const baseId = params.get('base') ?? previous?.id ?? latest?.id ?? '';
  const status = parseCompareStatus(params.get('status'));

  const compare = useQuery({
    queryKey: queryKeys.evalCompare(baseId, candidateId),
    queryFn: () => evalApi.compare(baseId, candidateId),
    enabled: Boolean(baseId && candidateId),
    staleTime: 60_000,
  });

  const cases = (compare.data?.cases ?? []).filter(
    (item) => status === 'all' || item.status === status,
  );

  function updateParams(patch: Record<string, string | null>) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(patch)) {
      if (value === null || value === '') {
        next.delete(key);
      } else {
        next.set(key, value);
      }
    }
    setParams(next, { replace: true });
  }

  return {
    index,
    runs,
    latest,
    previous,
    baseId,
    candidateId,
    status,
    compare,
    cases,
    selectBase: (id: string) => updateParams({ base: id }),
    selectCandidate: (id: string) => updateParams({ candidate: id }),
    selectStatus: (nextStatus: EvalCompareFilter) =>
      updateParams({ status: nextStatus === 'all' ? null : nextStatus }),
  };
}
