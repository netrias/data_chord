import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { expect, test } from '@playwright/test';

test.skip(process.env.RUN_REMOTE_PERF !== 'true', 'Set RUN_REMOTE_PERF=true to run remote performance checks.');

const DEFAULT_ROWS = 20;
const SAMPLE_FIXTURE = path.resolve('tests/fixtures/sample.csv');
const AGENT_FILE_INPUT = '[data-testid="agent-file-input"]';
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

    // Remote pages may be served by an older build without the full timing
    // helper, so wrap fetch at test time to keep network timings comparable.
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
  // Repeat real fixture rows instead of synthetic columns so remote timings
  // exercise the deployed Data Model Store path with representative values.
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
  await page.setInputFiles(AGENT_FILE_INPUT, csvPath);
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
  await _waitForMeasure(page, 'stage5.download_to_usable');
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

const _waitForMeasure = async (page, name) => {
  await page.waitForFunction(
    (measureName) => window.__dataChordPerf?.measures?.some((measure) => measure.name === measureName),
    name,
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

const _subtractDurations = (total, ...parts) => {
  if (total === null || parts.some((part) => part === null)) {
    return null;
  }
  return Math.max(0, total - parts.reduce((sum, part) => sum + part, 0));
};

const _appendLoadBreakdown = (lines, { total, network, parse, render }) => {
  lines.push(
    _reportLine('page usable', total),
    _reportLine('network request', network),
    _reportLine('client after network', _subtractDurations(total, network)),
    _reportLine('JSON parse', parse),
    _reportLine('render DOM work', render),
    _reportLine('other client time', _subtractDurations(total, network, parse, render)),
  );
};

const _appendDownloadBreakdown = (lines, { total, network, blob }) => {
  lines.push(
    _reportLine('download usable', total),
    _reportLine('network request', network),
    _reportLine('client after network', _subtractDurations(total, network)),
    _reportLine('blob read', blob),
    _reportLine('other client time', _subtractDurations(total, network, blob)),
  );
};

const _printRemoteReport = ({ baseURL, rowCount, journey, stage4, stage5 }) => {
  const stage4RowsRequest = _latestDurationAny(stage4, ['stage4.rows.request', 'fetch:/stage-4/rows']);
  const stage4RowsParse = _latestDuration(stage4, 'stage4.rows.parse');
  const stage4RenderDom = _latestDuration(stage4, 'stage4.render.dom');
  const stage4Usable = _latestDuration(stage4, 'stage4.init_to_usable');
  const stage5SummaryRequest = _latestDurationAny(stage5, ['stage5.summary.request', 'fetch:/stage-5/summary']);
  const stage5SummaryParse = _latestDuration(stage5, 'stage5.summary.parse');
  const stage5RenderDom = _latestDuration(stage5, 'stage5.summary.render.dom');
  const stage5Usable = _latestDuration(stage5, 'stage5.init_to_usable');
  const downloadRequest = _latestDurationAny(stage5, ['stage5.download.request', 'fetch:/stage-5/download']);
  const downloadBlob = _latestDuration(stage5, 'stage5.download.blob');
  const downloadUsable = _latestDuration(stage5, 'stage5.download_to_usable');

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
    'Stage 4 in-page load:',
  ];
  _appendLoadBreakdown(lines, {
    total: stage4Usable,
    network: stage4RowsRequest,
    parse: stage4RowsParse,
    render: stage4RenderDom,
  });
  lines.push('Stage 5 in-page load:');
  _appendLoadBreakdown(lines, {
    total: stage5Usable,
    network: stage5SummaryRequest,
    parse: stage5SummaryParse,
    render: stage5RenderDom,
  });
  lines.push('Stage 5 download:');
  _appendDownloadBreakdown(lines, {
    total: downloadUsable,
    network: downloadRequest,
    blob: downloadBlob,
  });
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
