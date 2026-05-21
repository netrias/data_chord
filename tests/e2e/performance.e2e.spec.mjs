import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { expect, test } from '@playwright/test';
import {
  clickHarmonize,
  mockHarmonizeSuccess,
  seedHarmonization,
  uploadAndAnalyze,
} from './utils.mjs';

test.skip(process.env.RUN_PERF !== 'true', 'Set RUN_PERF=true to run the performance journey.');

const DEFAULT_ROWS = 80;
const DEFAULT_COLUMNS = 8;

const _numberFromEnv = (name, fallback) => {
  const value = Number(process.env[name]);
  return Number.isInteger(value) && value > 0 ? value : fallback;
};

const _createPerfCsv = (rowCount, columnCount) => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'data-chord-perf-'));
  const csvPath = path.join(tmpDir, `perf-${rowCount}x${columnCount}.csv`);
  const headers = Array.from({ length: columnCount }, (_, index) => `perf_col_${index + 1}`);
  const lines = [headers.join(',')];

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
      headers.map((header) => [header, `${header}_harmonized_${row + 1}`]),
    );
  }
  return changes;
};

const _waitForMeasure = async (page, name) => {
  await page.waitForFunction(
    (measureName) => window.__dataChordPerf?.measures?.some((measure) => measure.name === measureName),
    name,
  );
};

const _waitForNewMeasure = async (page, name, previousCount) => {
  await page.waitForFunction(
    ({ measureName, count }) => {
      const measures = window.__dataChordPerf?.measures ?? [];
      return measures.filter((measure) => measure.name === measureName).length > count;
    },
    { measureName: name, count: previousCount },
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

const _measureCount = (report, name) => {
  return report.measures.filter((measure) => measure.name === name).length;
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

const _printReport = ({ rowCount, columnCount, stage4, stage5 }) => {
  const stage4RowsRequest = _latestDuration(stage4, 'stage4.rows.request');
  const stage4RowsParse = _latestDuration(stage4, 'stage4.rows.parse');
  const stage4RenderDom = _latestDuration(stage4, 'stage4.render.dom');
  const stage4Usable = _latestDuration(stage4, 'stage4.init_to_usable');
  const stage5SummaryRequest = _latestDuration(stage5, 'stage5.summary.request');
  const stage5SummaryParse = _latestDuration(stage5, 'stage5.summary.parse');
  const stage5RenderDom = _latestDuration(stage5, 'stage5.summary.render.dom');
  const stage5Usable = _latestDuration(stage5, 'stage5.init_to_usable');
  const downloadRequest = _latestDuration(stage5, 'stage5.download.request');
  const downloadBlob = _latestDuration(stage5, 'stage5.download.blob');
  const downloadUsable = _latestDuration(stage5, 'stage5.download_to_usable');

  const lines = [
    '',
    `Performance journey (${rowCount} rows x ${columnCount} columns)`,
    'Stage 4 in-page load:',
  ];
  _appendLoadBreakdown(lines, {
    total: stage4Usable,
    network: stage4RowsRequest,
    parse: stage4RowsParse,
    render: stage4RenderDom,
  });
  lines.push(
    _reportLine('next batch usable', _latestDuration(stage4, 'stage4.batch_change_to_usable')),
    'Stage 5 in-page load:',
  );
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

test('performance journey: Stage 4 and Stage 5 user-perceived timings', async ({ page }) => {
  const rowCount = _numberFromEnv('PERF_ROWS', DEFAULT_ROWS);
  const columnCount = _numberFromEnv('PERF_COLUMNS', DEFAULT_COLUMNS);
  const { csvPath, headers } = _createPerfCsv(rowCount, columnCount);
  await mockHarmonizeSuccess(page);

  // Given: a larger uploaded file has completed the mocked analysis and harmonization handoff.
  const fileId = await uploadAndAnalyze(page, csvPath);
  await clickHarmonize(page);
  seedHarmonization(fileId, _changesFor(rowCount, headers));
  await expect(page.locator('#reviewButton')).toBeEnabled();

  // When: the user opens Stage 4.
  await page.click('#reviewButton');
  await page.waitForURL(/\/stage-4/);
  await _waitForMeasure(page, 'stage4.init_to_usable');
  let stage4Report = await _readReport(page);
  const previousBatchMeasureCount = _measureCount(stage4Report, 'stage4.batch_change_to_usable');

  // Then: the first review batch is visible and the next-batch interaction is timed.
  await expect(page.locator('#reviewTable .row-cell').first()).toBeVisible();
  await page.click('#nextBatchButton');
  await _waitForNewMeasure(page, 'stage4.batch_change_to_usable', previousBatchMeasureCount);
  stage4Report = await _readReport(page);

  // When: the user advances to Stage 5.
  await page.click('#stageFiveButton');
  await page.waitForURL(/\/stage-5/);
  await _waitForMeasure(page, 'stage5.init_to_usable');
  let stage5Report = await _readReport(page);

  // Then: summary UI is usable and the download operation is timed through the button.
  await expect(page.locator('#summaryGrid')).toBeVisible();
  const downloadPromise = page.waitForEvent('download');
  await page.click('#downloadResults');
  await downloadPromise;
  await _waitForMeasure(page, 'stage5.download_to_usable');
  stage5Report = await _readReport(page);

  _printReport({ rowCount, columnCount, stage4: stage4Report, stage5: stage5Report });
});
