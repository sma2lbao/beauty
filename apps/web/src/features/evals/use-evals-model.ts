/**
 * 评测页数据模型:总览读运行索引,详情读单次运行快照。
 *
 * M0 数据是静态文件;后面换成 eval-service 时只替换 evalApi 的 URL,
 * 组件与 URL 过滤协议保持不变。
 */
import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router';

import { evalApi, findCase, flattenCases } from '@/lib/eval-api';
import type { EvalCaseWithSuite } from '@/lib/eval-api';
import { queryKeys } from '@/lib/query-keys';

export type EvalCaseStatus = 'all' | 'passed' | 'failed';
export type EvalCaseTab = 'result' | 'trajectory' | 'io' | 'raw';

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

export function useEvalRunModel(runId: string) {
  const run = useQuery({
    queryKey: queryKeys.evalRun(runId),
    queryFn: () => evalApi.run(runId),
    enabled: Boolean(runId),
    staleTime: 60_000,
  });
  const [params, setParams] = useSearchParams();

  const selectedCase = findCase(run.data, params.get('case'));
  const requestedSuite = params.get('suite');
  const activeSuite =
    requestedSuite ?? selectedCase?.suite ?? run.data?.suites[0]?.name ?? '';
  const status = parseStatus(params.get('status'));
  const tab = parseTab(params.get('tab'));

  const suites = run.data?.suites ?? [];
  const suiteCases = flattenCases(run.data).filter(
    (testCase) => testCase.suite === activeSuite,
  );
  const cases = suiteCases.filter((testCase) => {
    if (status === 'passed') return testCase.ok;
    if (status === 'failed') return !testCase.ok;
    return true;
  });

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
    updateParams({ suite, case: null, tab: null });
  }

  function selectStatus(nextStatus: EvalCaseStatus) {
    updateParams({ status: nextStatus === 'all' ? null : nextStatus });
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
    run,
    suite: activeSuite,
    suites,
    cases,
    suiteCases,
    selectedCase,
    status,
    tab,
    selectSuite,
    selectStatus,
    selectCase,
    closeCase,
    selectTab,
  };
}
