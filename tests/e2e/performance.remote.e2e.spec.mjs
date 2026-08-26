import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { expect, test } from '@playwright/test';

import { resolvePrivateStorageState } from './performance-auth.mjs';
import {
  collectNavigationRuns,
  printPerformanceSummary,
  savePerformanceReport,
} from './performance-journey.mjs';
import {
  buildPerformanceReport,
  positiveIntegerFromEnv,
} from './performance-report.mjs';

test.skip(process.env.RUN_REMOTE_PERF !== 'true', 'Set RUN_REMOTE_PERF=true to run remote performance checks.');

const DEFAULT_ROWS = 20;
const DEFAULT_COLD_RUNS = 3;
const DEFAULT_WARM_RUNS = 10;
const SAMPLE_FIXTURE = path.resolve('tests/fixtures/sample.csv');
const AGENT_FILE_INPUT = '[data-testid="agent-file-input"]';
const REMOTE_TIMEOUT_MS = 10 * 60 * 1000;

test.setTimeout(REMOTE_TIMEOUT_MS);

const _now = () => Date.now();

const _duration = (startedAt) => _now() - startedAt;

const _readSampleRows = () => {
  const content = fs.readFileSync(SAMPLE_FIXTURE, 'utf8').trim();
  const [headerLine, ...rows] = content.split(/\r?\n/);
  return { headerLine, rows };
};

const _createRemotePerfCsv = (rowCount) => {
  const { headerLine, rows } = _readSampleRows();
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'data-chord-remote-perf-'));
  const csvPath = path.join(tmpDir, `remote-perf-${rowCount}.csv`);
  // Repeat representative fixture rows so the deployed test uses the real data-model path.
  const body = Array.from({ length: rowCount }, (_, index) => rows[index % rows.length]);
  fs.writeFileSync(csvPath, `${[headerLine, ...body].join('\n')}\n`);
  return { csvPath, columnCount: headerLine.split(',').length };
};

const _selectRequestedDataModel = async (page) => {
  const dataModelKey = process.env.PERF_DATA_MODEL_KEY;
  const versionNumber = process.env.PERF_VERSION_NUMBER;
  if (dataModelKey) await page.selectOption('#dataModelSelect', dataModelKey);
  if (versionNumber) {
    await page.click('#versionDropdownTrigger');
    await page.locator(`.data-model-dropdown-item[data-value="${versionNumber}"]`).click();
  }
};

const _uploadAndAnalyzeRemote = async (page, csvPath) => {
  await page.addInitScript(() => sessionStorage.clear());
  await page.goto('/stage-1');
  if (page.url().includes('.auth.') || page.url().includes('/oauth2/authorize')) {
    throw new Error(
      'Authentication state is not valid. Run just perf-staging-login again.',
    );
  }
  try {
    await page.locator(AGENT_FILE_INPUT).waitFor({
      state: 'attached',
      timeout: 10_000,
    });
  } catch {
    throw new Error(
      'Authentication state did not open Stage 1. Run just perf-staging-login again.',
    );
  }

  const uploadStartedAt = _now();
  await page.setInputFiles(AGENT_FILE_INPUT, csvPath);
  await page.locator('#analyzeButton').waitFor({ state: 'visible' });
  await page.waitForFunction(() => !document.querySelector('#analyzeButton')?.disabled);
  const uploadToReadyMs = _duration(uploadStartedAt);

  await page.click('#analyzeButton');
  await page.locator('.data-model-dialog').waitFor({ state: 'visible' });
  await _selectRequestedDataModel(page);
  const analyzeStartedAt = _now();
  await page.locator('.data-model-confirm-btn').click();
  await page.waitForURL(/\/stage-2/, { timeout: REMOTE_TIMEOUT_MS });
  await expect(page.locator('#mappingRows .mapping-row').first()).toBeVisible({
    timeout: REMOTE_TIMEOUT_MS,
  });
  return {
    upload_to_ready_ms: uploadToReadyMs,
    analyze_to_mapping_usable_ms: _duration(analyzeStartedAt),
  };
};

const _runHarmonizationRemote = async (page) => {
  const startedAt = _now();
  await page.locator('#harmonizeButton').waitFor({ state: 'visible' });
  await page.waitForFunction(() => !document.querySelector('#harmonizeButton')?.disabled);
  await page.click('#harmonizeButton');
  await page.waitForURL(/\/stage-3/, { timeout: REMOTE_TIMEOUT_MS });
  await expect(page.locator('#reviewButton')).toBeEnabled({ timeout: REMOTE_TIMEOUT_MS });
  return _duration(startedAt);
};

test('remote performance journey: repeated deployed click-to-render timings', async ({
  browser,
  baseURL,
}, testInfo) => {
  if (!baseURL) throw new Error('PLAYWRIGHT_BASE_URL is required for remote performance checks.');
  const storageStatePath = resolvePrivateStorageState(process.env);
  const rowCount = positiveIntegerFromEnv(process.env, 'PERF_REMOTE_ROWS', DEFAULT_ROWS);
  const coldRuns = positiveIntegerFromEnv(process.env, 'PERF_COLD_RUNS', DEFAULT_COLD_RUNS);
  const warmRuns = positiveIntegerFromEnv(process.env, 'PERF_WARM_RUNS', DEFAULT_WARM_RUNS);
  const { csvPath, columnCount } = _createRemotePerfCsv(rowCount);
  const context = await browser.newContext({ baseURL, storageState: storageStatePath });
  const page = await context.newPage();

  try {
    // Given: one real deployed workflow completes upload, analysis, and harmonization.
    const setup = await _uploadAndAnalyzeRemote(page, csvPath);
    setup.harmonize_to_review_ready_ms = await _runHarmonizationRemote(page);
    const stageThreeUrl = page.url();

    // When: cold browser contexts and the warm browser session repeat both navigations.
    const runs = await collectNavigationRuns({
      page,
      baseURL,
      stageThreeUrl,
      coldRuns,
      warmRuns,
      timeout: REMOTE_TIMEOUT_MS,
    });

    // Then: all samples reach the deployed post-paint markers and produce one JSON artifact.
    expect(runs).toHaveLength(coldRuns + warmRuns);
    for (const run of runs) {
      for (const duration of [...Object.values(run.stage4), ...Object.values(run.stage5)]) {
        expect(Number.isFinite(duration)).toBe(true);
      }
      expect(run.browser_timing.stage4.navigation.response_end_ms).toBeGreaterThan(0);
      expect(run.browser_timing.stage5.navigation.response_end_ms).toBeGreaterThan(0);
    }
    const report = buildPerformanceReport({
      environment: {
        target: process.env.PERF_TARGET ?? 'bdf',
        stage: 'staging',
        base_url: baseURL,
        commit: process.env.GITHUB_SHA ?? null,
      },
      dataset: {
        rows: rowCount,
        columns: columnCount,
        mappings: Math.max(...runs.map((run) => run.mapping_count ?? 0)),
        source: 'tests/fixtures/sample.csv',
      },
      runs,
      setup,
    });
    await savePerformanceReport({
      testInfo,
      report,
      explicitPath: process.env.PERF_REPORT_PATH,
    });
    printPerformanceSummary(report);
  } finally {
    await context.close();
  }
});
