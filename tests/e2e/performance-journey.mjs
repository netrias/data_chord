import fs from 'node:fs';
import path from 'node:path';

import { captureBrowserTiming } from './performance-browser-timing.mjs';

const _now = () => Date.now();

const _duration = (startedAt) => _now() - startedAt;

const _waitForMeasure = async (page, name, timeout) => {
  await page.waitForFunction(
    (measureName) => window.__dataChordPerf?.measures?.some(
      (measure) => measure.name === measureName,
    ),
    name,
    { timeout },
  );
};

const _readTimingReport = async (page) => page.evaluate(
  () => window.__dataChordPerf?.getReport?.() ?? { marks: [], measures: [] },
);

const _latestDuration = (report, name) => {
  const latest = report.measures.filter((measure) => measure.name === name).at(-1);
  return latest ? Math.round(latest.duration_ms) : null;
};

const _latestMarkDetail = (report, name, key) => {
  const latest = report.marks.filter((mark) => mark.name === name).at(-1);
  const value = latest?.detail?.[key];
  return Number.isInteger(value) && value >= 0 ? value : null;
};

const _stageFourTiming = (report, buttonToUsableMs) => ({
  button_to_usable_ms: buttonToUsableMs,
  rows_request_ms: _latestDuration(report, 'stage4.rows.request'),
  parse_ms: _latestDuration(report, 'stage4.rows.parse'),
  render_ms: _latestDuration(report, 'stage4.render.dom'),
  post_paint_ms: _latestDuration(report, 'stage4.render_to_usable'),
  init_to_usable_ms: _latestDuration(report, 'stage4.init_to_usable'),
});

const _stageFiveTiming = (report, buttonToUsableMs) => ({
  button_to_usable_ms: buttonToUsableMs,
  summary_request_ms: _latestDuration(report, 'stage5.summary.request'),
  parse_ms: _latestDuration(report, 'stage5.summary.parse'),
  render_ms: _latestDuration(report, 'stage5.summary.render.dom'),
  post_paint_ms: _latestDuration(report, 'stage5.summary.render_to_usable'),
  init_to_usable_ms: _latestDuration(report, 'stage5.init_to_usable'),
});

export const runNavigationSample = async ({
  page,
  stageThreeUrl,
  id,
  kind,
  timeout,
}) => {
  await page.goto(stageThreeUrl);
  await page.locator('#reviewButton').waitFor({ state: 'visible', timeout });
  await page.waitForFunction(
    () => !document.querySelector('#reviewButton')?.disabled,
    null,
    { timeout },
  );

  const stageFourStartedAt = _now();
  await page.click('#reviewButton');
  await page.waitForURL(/\/stage-4/, { timeout });
  await _waitForMeasure(page, 'stage4.init_to_usable', timeout);
  const stageFourButtonToUsableMs = _duration(stageFourStartedAt);
  const stageFourReport = await _readTimingReport(page);
  await page.locator('#reviewTable .row-cell, #reviewTable .review-empty').first().waitFor({
    state: 'visible',
    timeout,
  });
  const stageFourBrowserTiming = await captureBrowserTiming(page);

  const stageFiveStartedAt = _now();
  await page.click('#stageFiveButton');
  await page.waitForURL(/\/stage-5/, { timeout });
  await _waitForMeasure(page, 'stage5.init_to_usable', timeout);
  const stageFiveButtonToUsableMs = _duration(stageFiveStartedAt);
  const stageFiveReport = await _readTimingReport(page);
  await page.locator('#summaryGrid .change-impact').waitFor({ state: 'visible', timeout });
  const proceedButton = page.locator('#conformanceProceedButton');
  const warningShown = await proceedButton.isVisible();
  if (warningShown) await proceedButton.click();
  const stageFiveBrowserTiming = await captureBrowserTiming(page);

  return {
    id,
    kind,
    warning_shown: warningShown,
    mapping_count: _latestMarkDetail(
      stageFiveReport,
      'stage5.summary.fetch.parsed',
      'term_mapping_count',
    ),
    stage4: _stageFourTiming(stageFourReport, stageFourButtonToUsableMs),
    stage5: _stageFiveTiming(stageFiveReport, stageFiveButtonToUsableMs),
    browser_timing: {
      stage4: stageFourBrowserTiming,
      stage5: stageFiveBrowserTiming,
    },
  };
};

export const collectNavigationRuns = async ({
  page,
  baseURL,
  stageThreeUrl,
  coldRuns,
  warmRuns,
  timeout,
}) => {
  const browser = page.context().browser();
  if (!browser) throw new Error('The performance journey requires a browser context.');
  const storageState = await page.context().storageState();
  const runs = [];

  for (let index = 0; index < coldRuns; index += 1) {
    const coldContext = await browser.newContext({ baseURL, storageState });
    try {
      const coldPage = await coldContext.newPage();
      runs.push(await runNavigationSample({
        page: coldPage,
        stageThreeUrl,
        id: `cold-${index + 1}`,
        kind: 'cold',
        timeout,
      }));
    } finally {
      await coldContext.close();
    }
  }

  await runNavigationSample({
    page,
    stageThreeUrl,
    id: 'warm-up',
    kind: 'warm-up',
    timeout,
  });
  for (let index = 0; index < warmRuns; index += 1) {
    runs.push(await runNavigationSample({
      page,
      stageThreeUrl,
      id: `warm-${index + 1}`,
      kind: 'warm',
      timeout,
    }));
  }
  return runs;
};

export const savePerformanceReport = async ({ testInfo, report, explicitPath }) => {
  const body = `${JSON.stringify(report, null, 2)}\n`;
  const artifactPath = testInfo.outputPath('performance-report.json');
  fs.writeFileSync(artifactPath, body);
  await testInfo.attach('performance-report', {
    path: artifactPath,
    contentType: 'application/json',
  });

  if (explicitPath) {
    const requestedPath = path.resolve(explicitPath);
    fs.mkdirSync(path.dirname(requestedPath), { recursive: true });
    fs.writeFileSync(requestedPath, body);
  }
  return artifactPath;
};

export const printPerformanceSummary = (report) => {
  const cold = report.summary.cold;
  const warm = report.summary.warm;
  console.log([
    '',
    `Performance report: ${report.environment.base_url}`,
    `Cold Stage 4 median/p95: ${cold.stage4_button_to_usable_ms.median}ms / ${cold.stage4_button_to_usable_ms.p95}ms`,
    `Cold Stage 5 median/p95: ${cold.stage5_button_to_usable_ms.median}ms / ${cold.stage5_button_to_usable_ms.p95}ms`,
    `Warm Stage 4 median/p95: ${warm.stage4_button_to_usable_ms.median}ms / ${warm.stage4_button_to_usable_ms.p95}ms`,
    `Warm Stage 5 median/p95: ${warm.stage5_button_to_usable_ms.median}ms / ${warm.stage5_button_to_usable_ms.p95}ms`,
  ].join('\n'));
};
