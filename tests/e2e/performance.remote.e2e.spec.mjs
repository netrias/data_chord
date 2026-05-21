import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { expect, test } from '@playwright/test';

test.skip(process.env.RUN_REMOTE_PERF !== 'true', 'Set RUN_REMOTE_PERF=true to run remote performance checks.');

const DEFAULT_ROWS = 20;
const SAMPLE_FIXTURE = path.resolve('tests/fixtures/sample.csv');
const REMOTE_TIMEOUT_MS = 10 * 60 * 1000;

test.setTimeout(REMOTE_TIMEOUT_MS);

const _numberFromEnv = (name, fallback) => {
  const value = Number(process.env[name]);
  return Number.isInteger(value) && value > 0 ? value : fallback;
};

const _now = () => Date.now();

const _duration = (start) => _now() - start;

const _installRemoteTiming = async (page) => {
  await page.addInitScript(() => {
    const STORE_KEY = '__dataChordPerf';
    const store = window[STORE_KEY] ?? { marks: [], measures: [] };
    window[STORE_KEY] = store;
    store.getReport = () => ({
      marks: [...store.marks],
      measures: [...store.measures],
    });

    if (window.__dataChordFetchTimingInstalled) {
      return;
    }
    window.__dataChordFetchTimingInstalled = true;
    const originalFetch = window.fetch.bind(window);

    const pathnameFor = (input) => {
      const rawUrl = typeof input === 'string' ? input : input?.url;
      if (!rawUrl) return null;
      try {
        return new URL(rawUrl, window.location.origin).pathname;
      } catch {
        return null;
      }
    };

    window.fetch = async (...args) => {
      const path = pathnameFor(args[0]);
      const start = performance.now();
      const response = await originalFetch(...args);
      const end = performance.now();
      if (path) {
        store.measures.push({
          name: `fetch:${path}`,
          start: `fetch:${path}:start`,
          end: `fetch:${path}:response`,
          duration_ms: end - start,
          detail: { status: response.status },
        });
      }
      return response;
    };
  });
};

const _readSampleRows = () => {
  const content = fs.readFileSync(SAMPLE_FIXTURE, 'utf8').trim();
  const [headerLine, ...rows] = content.split(/\r?\n/);
  return { headerLine, rows };
};

const _createRemotePerfCsv = (rowCount) => {
  const { headerLine, rows } = _readSampleRows();
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'data-chord-remote-perf-'));
  const csvPath = path.join(tmpDir, `remote-perf-${rowCount}.csv`);
  const body = Array.from({ length: rowCount }, (_, index) => rows[index % rows.length]);
  fs.writeFileSync(csvPath, `${[headerLine, ...body].join('\n')}\n`);
  return csvPath;
};

const _selectRequestedDataModel = async (page) => {
  const dataModelKey = process.env.PERF_DATA_MODEL_KEY;
  const versionNumber = process.env.PERF_VERSION_NUMBER;

  if (dataModelKey) {
    await page.selectOption('#dataModelSelect', dataModelKey);
  }

  if (versionNumber) {
    await page.click('#versionDropdownTrigger');
    await page.locator(`.data-model-dropdown-item[data-value="${versionNumber}"]`).click();
  }
};

const _uploadAndAnalyzeRemote = async (page, csvPath) => {
  await _installRemoteTiming(page);
  await page.addInitScript(() => {
    sessionStorage.clear();
  });

  await page.goto('/stage-1');
  if (page.url().includes('.auth.') || page.url().includes('/oauth2/authorize')) {
    throw new Error('Remote performance journey reached Cognito. Connect to the company VPN or pass a VPN-accessible staging URL.');
  }

  const uploadStart = _now();
  await page.setInputFiles('#fileInput', csvPath);
  await page.locator('#analyzeButton').waitFor({ state: 'visible' });
  await page.waitForFunction(() => !document.querySelector('#analyzeButton')?.disabled);
  const uploadToReadyMs = _duration(uploadStart);

  await page.click('#analyzeButton');
  await page.locator('.data-model-dialog').waitFor({ state: 'visible' });
  await _selectRequestedDataModel(page);

  const analyzeStart = _now();
  await page.locator('.data-model-confirm-btn').click();
  await page.waitForURL(/\/stage-2/, { timeout: REMOTE_TIMEOUT_MS });
  await expect(page.locator('#mappingRows .mapping-row').first()).toBeVisible({ timeout: REMOTE_TIMEOUT_MS });
  const analyzeToMappingUsableMs = _duration(analyzeStart);

  return { uploadToReadyMs, analyzeToMappingUsableMs };
};

const _runHarmonizationRemote = async (page) => {
  const harmonizeStart = _now();
  await page.locator('#harmonizeButton').waitFor({ state: 'visible' });
  await page.waitForFunction(() => !document.querySelector('#harmonizeButton')?.disabled);
  await page.click('#harmonizeButton');
  await page.waitForURL(/\/stage-3/, { timeout: REMOTE_TIMEOUT_MS });
  await expect(page.locator('#reviewButton')).toBeEnabled({ timeout: REMOTE_TIMEOUT_MS });
  return _duration(harmonizeStart);
};

const _advanceThroughPvWarningIfNeeded = async (page) => {
  const proceedButton = page.locator('[data-action="proceed"]');
  try {
    await proceedButton.waitFor({ state: 'visible', timeout: 2000 });
    await proceedButton.click();
  } catch {
    // No PV warning appeared. Continue waiting for Stage 5 navigation.
  }
};

const _openStageFourRemote = async (page) => {
  const stageFourStart = _now();
  await page.click('#reviewButton');
  await page.waitForURL(/\/stage-4/, { timeout: REMOTE_TIMEOUT_MS });
  await _waitForStageFourUsable(page);
  return _duration(stageFourStart);
};

const _openStageFiveRemote = async (page) => {
  const stageFiveStart = _now();
  await page.click('#stageFiveButton');
  await _advanceThroughPvWarningIfNeeded(page);
  await page.waitForURL(/\/stage-5/, { timeout: REMOTE_TIMEOUT_MS });
  await _waitForStageFiveUsable(page);
  return _duration(stageFiveStart);
};

const _downloadRemote = async (page) => {
  const downloadPromise = page.waitForEvent('download', { timeout: REMOTE_TIMEOUT_MS });
  const downloadStart = _now();
  await page.click('#downloadResults');
  await downloadPromise;
  await _waitForDownloadUsable(page);
  return _duration(downloadStart);
};

const _waitForStageFourUsable = async (page) => {
  await page.locator('#reviewTable .row-cell, #reviewTable .review-empty').first().waitFor({
    state: 'visible',
    timeout: REMOTE_TIMEOUT_MS,
  });
};

const _waitForStageFiveUsable = async (page) => {
  await page.locator('#summaryGrid').waitFor({ state: 'visible', timeout: REMOTE_TIMEOUT_MS });
};

const _waitForDownloadUsable = async (page) => {
  await page.waitForFunction(
    () => !document.querySelector('#downloadResults')?.disabled,
    null,
    { timeout: REMOTE_TIMEOUT_MS },
  );
};

const _readReport = async (page) => {
  return page.evaluate(() => window.__dataChordPerf?.getReport?.() ?? { marks: [], measures: [] });
};

const _latestDuration = (report, name) => {
  const matches = report.measures.filter((measure) => measure.name === name);
  const latest = matches.at(-1);
  return latest ? Math.round(latest.duration_ms) : null;
};

const _latestDurationAny = (report, names) => {
  for (const name of names) {
    const duration = _latestDuration(report, name);
    if (duration !== null) {
      return duration;
    }
  }
  return null;
};

const _reportLine = (label, duration) => {
  const rendered = duration === null ? 'missing' : `${duration}ms`;
  return `  ${label}: ${rendered}`;
};

const _printRemoteReport = ({ baseURL, rowCount, journey, stage4, stage5 }) => {
  const lines = [
    '',
    `Remote performance journey (${baseURL}, ${rowCount} rows)`,
    'Whole-operation timings:',
    _reportLine('upload to analyze-ready', journey.uploadToReadyMs),
    _reportLine('analyze to mapping usable', journey.analyzeToMappingUsableMs),
    _reportLine('harmonize to review-ready', journey.harmonizeToReviewReadyMs),
    _reportLine('Stage 4 navigation to usable', journey.stageFourNavigationMs),
    _reportLine('Stage 5 navigation to usable', journey.stageFiveNavigationMs),
    _reportLine('download click to usable', journey.downloadMs),
    'Stage 4 browser breakdown:',
    _reportLine('rows request', _latestDurationAny(stage4, ['stage4.rows.request', 'fetch:/stage-4/rows'])),
    _reportLine('rows JSON parse', _latestDuration(stage4, 'stage4.rows.parse')),
    _reportLine('first render DOM work', _latestDuration(stage4, 'stage4.render.dom')),
    _reportLine('page usable', _latestDuration(stage4, 'stage4.init_to_usable')),
    'Stage 5 browser breakdown:',
    _reportLine('summary request', _latestDurationAny(stage5, ['stage5.summary.request', 'fetch:/stage-5/summary'])),
    _reportLine('summary JSON parse', _latestDuration(stage5, 'stage5.summary.parse')),
    _reportLine('summary render DOM work', _latestDuration(stage5, 'stage5.summary.render.dom')),
    _reportLine('page usable', _latestDuration(stage5, 'stage5.init_to_usable')),
    _reportLine('download request', _latestDurationAny(stage5, ['stage5.download.request', 'fetch:/stage-5/download'])),
    _reportLine('download blob read', _latestDuration(stage5, 'stage5.download.blob')),
    _reportLine('download usable', _latestDuration(stage5, 'stage5.download_to_usable')),
  ];
  console.log(lines.join('\n'));
};

test('remote performance journey: deployed Stage 4 and Stage 5 user timings', async ({ page, baseURL }) => {
  const rowCount = _numberFromEnv('PERF_REMOTE_ROWS', DEFAULT_ROWS);
  const csvPath = _createRemotePerfCsv(rowCount);

  // Given: the deployed app is reachable and no prior browser session should affect the run.
  const journey = await _uploadAndAnalyzeRemote(page, csvPath);

  // When: the user runs harmonization and opens Stage 4.
  journey.harmonizeToReviewReadyMs = await _runHarmonizationRemote(page);
  journey.stageFourNavigationMs = await _openStageFourRemote(page);
  const stage4Report = await _readReport(page);

  // Then: Stage 4 is usable, and Stage 5 plus download timings can be captured.
  await _waitForStageFourUsable(page);
  journey.stageFiveNavigationMs = await _openStageFiveRemote(page);
  await _waitForStageFiveUsable(page);
  journey.downloadMs = await _downloadRemote(page);
  const stage5Report = await _readReport(page);

  _printRemoteReport({ baseURL, rowCount, journey, stage4: stage4Report, stage5: stage5Report });
});
