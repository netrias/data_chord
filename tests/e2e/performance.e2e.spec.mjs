import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { expect, test } from '@playwright/test';

import {
  collectNavigationRuns,
  printPerformanceSummary,
  savePerformanceReport,
} from './performance-journey.mjs';
import {
  buildPerformanceReport,
  positiveIntegerFromEnv,
} from './performance-report.mjs';
import {
  clickHarmonize,
  mockHarmonizeSuccess,
  seedHarmonization,
  uploadAndAnalyze,
} from './utils.mjs';

test.skip(process.env.RUN_PERF !== 'true', 'Set RUN_PERF=true to run the performance journey.');

const DEFAULT_ROWS = 80;
const DEFAULT_COLUMNS = 8;
const DEFAULT_COLD_RUNS = 3;
const DEFAULT_WARM_RUNS = 10;
const PERFORMANCE_TIMEOUT_MS = 5 * 60 * 1000;

test.setTimeout(PERFORMANCE_TIMEOUT_MS);

const _createPerfCsv = (rowCount, columnCount) => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'data-chord-perf-'));
  const csvPath = path.join(tmpDir, `perf-${rowCount}x${columnCount}.csv`);
  const headers = Array.from({ length: columnCount }, (_, index) => `perf_col_${index + 1}`);
  const lines = [headers.join(',')];

  // Synthetic values make the mocked harmonization deterministic while still
  // scaling server and browser work with both rows and columns.
  for (let row = 0; row < rowCount; row += 1) {
    lines.push(headers.map((header) => `${header}_raw_${row + 1}`).join(','));
  }

  fs.writeFileSync(csvPath, `${lines.join('\n')}\n`);
  return { csvPath, headers };
};

const _changesFor = (rowCount, headers) => {
  const changes = {};
  for (let row = 0; row < rowCount; row += 1) {
    changes[row] = Object.fromEntries(
      headers.map((header, index) => [
        `col_${String(index).padStart(4, '0')}`,
        `${header}_harmonized_${row + 1}`,
      ]),
    );
  }
  return changes;
};

test('performance journey: repeated Stage 4 and Stage 5 click-to-render timings', async ({
  page,
  baseURL,
}, testInfo) => {
  const rowCount = positiveIntegerFromEnv(process.env, 'PERF_ROWS', DEFAULT_ROWS);
  const columnCount = positiveIntegerFromEnv(process.env, 'PERF_COLUMNS', DEFAULT_COLUMNS);
  const coldRuns = positiveIntegerFromEnv(process.env, 'PERF_COLD_RUNS', DEFAULT_COLD_RUNS);
  const warmRuns = positiveIntegerFromEnv(process.env, 'PERF_WARM_RUNS', DEFAULT_WARM_RUNS);
  const { csvPath, headers } = _createPerfCsv(rowCount, columnCount);
  await mockHarmonizeSuccess(page);

  // Given: one deterministic workflow has completed analysis and harmonization.
  const fileId = await uploadAndAnalyze(page, csvPath);
  await clickHarmonize(page);
  seedHarmonization(fileId, _changesFor(rowCount, headers));
  await expect(page.locator('#reviewButton')).toBeEnabled();
  const stageThreeUrl = new URL(page.url());
  stageThreeUrl.searchParams.set('job_id', `e2e-job-${fileId}`);

  // When: cold browser contexts and the warm browser session repeat both navigations.
  const runs = await collectNavigationRuns({
    page,
    baseURL,
    stageThreeUrl: stageThreeUrl.toString(),
    coldRuns,
    warmRuns,
    timeout: PERFORMANCE_TIMEOUT_MS,
  });

  // Then: every sample reaches the post-paint marker and a stable JSON report is saved.
  expect(runs).toHaveLength(coldRuns + warmRuns);
  for (const run of runs) {
    expect(run.stage4.button_to_usable_ms).toBeGreaterThan(0);
    expect(run.stage5.button_to_usable_ms).toBeGreaterThan(0);
    for (const duration of [...Object.values(run.stage4), ...Object.values(run.stage5)]) {
      expect(Number.isFinite(duration)).toBe(true);
    }
  }
  const mappingCount = Math.max(...runs.map((run) => run.mapping_count ?? 0));
  const report = buildPerformanceReport({
    environment: {
      target: 'local',
      stage: 'local',
      base_url: baseURL,
      commit: process.env.GITHUB_SHA ?? null,
    },
    dataset: {
      rows: rowCount,
      columns: columnCount,
      mappings: mappingCount,
      source: 'synthetic',
    },
    runs,
  });
  await savePerformanceReport({
    testInfo,
    report,
    explicitPath: process.env.PERF_REPORT_PATH,
  });
  printPerformanceSummary(report);
});
