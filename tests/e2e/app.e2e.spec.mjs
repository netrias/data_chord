import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import {
  fileFixture,
  getFileIdFromUrl,
  uploadAndAnalyze,
  clickHarmonize,
  mockDataModels,
  mockHarmonizeSuccess,
  mockHarmonizeFailure,
  seedHarmonization,
  parseDownloadedCsv,
} from './utils.mjs';

const waitForReviewRows = async (page) => {
  const grid = page.locator('.column-mode-grid');
  const empty = page.locator('.review-empty');
  await Promise.race([
    grid.waitFor({ state: 'visible' }),
    empty.waitFor({ state: 'visible' }),
  ]);
};

const downloadCsvRows = async (page, fileId) => {
  const response = await page.request.post('/stage-5/download', { data: { file_id: fileId } });
  expect(response.ok()).toBeTruthy();
  return parseDownloadedCsv(response);
};

test('happy path flow: upload → analyze → harmonize → review → summary → download', async ({ page }) => {
  await mockHarmonizeSuccess(page);

  // Given: a CSV file is uploaded and analyzed
  const fileId = await uploadAndAnalyze(page, fileFixture('basic.csv'));
  const preOverrides = await page.request.get(`/stage-4/overrides/${fileId}`);
  expect(await preOverrides.json()).toBeNull();

  // When: the user proceeds to harmonize
  await clickHarmonize(page);

  // Then: harmonize completes and review can continue
  await expect(page.locator('#reviewButton')).toBeEnabled();

  seedHarmonization(fileId, { 0: { col_a: 'Baz' } });

  await page.click('#reviewButton');
  await page.waitForURL(/\/stage-4/);
  await waitForReviewRows(page);

  await page.click('#stageFiveButton');
  await page.waitForURL(/\/stage-5/);
  await page.locator('#summaryGrid').waitFor({ state: 'visible' });

  const rows = await downloadCsvRows(page, fileId);
  expect(rows[0].col_a).toBe('Baz');
});

test('override propagation applies to all instances in a column', async ({ page }) => {
  await mockHarmonizeSuccess(page);

  // Given: a CSV with repeated terms in a column
  const fileId = await uploadAndAnalyze(page, fileFixture('basic.csv'));
  const preOverrides = await page.request.get(`/stage-4/overrides/${fileId}`);
  expect(await preOverrides.json()).toBeNull();
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileId, { 0: { col_a: 'Suggested' }, 1: { col_a: 'Suggested' } });

  // When: the user overrides the term once
  await page.goto(`/stage-4?file_id=${fileId}`);
  await waitForReviewRows(page);
  const card = page.locator('.column-mode-grid .row-cell', {
    has: page.locator('.original-context-value', { hasText: 'Foo' }),
  }).first();
  await card.locator('.target-value-input').fill('Baz');
  await page.waitForResponse((response) => response.url().includes('/stage-4/overrides') && response.ok());

  // Then: download applies override to all matching rows in that column
  const rows = await downloadCsvRows(page, fileId);
  expect(rows[0].col_a).toBe('Baz');
  expect(rows[1].col_a).toBe('Baz');
  expect(rows[2].col_a).toBe('Bar');
});

test('whitespace-significant terms remain distinct', async ({ page }) => {
  await mockHarmonizeSuccess(page);

  // Given: a CSV where whitespace creates distinct terms
  const fileId = await uploadAndAnalyze(page, fileFixture('whitespace.csv'));
  const preOverrides = await page.request.get(`/stage-4/overrides/${fileId}`);
  expect(await preOverrides.json()).toBeNull();
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileId, {
    0: { col_a: 'Suggested' },
    1: { col_a: 'Suggested' },
    2: { col_a: 'Suggested' },
  });

  // When: the user overrides the whitespace-padded term in row mode
  await page.goto(`/stage-4?file_id=${fileId}`);
  await waitForReviewRows(page);
  await page.click('#settingsButton');
  await page.selectOption('#reviewModeSelect', 'row');
  await page.click('#settingsCloseButton');
  const row = page.locator('.row-mode-row').first();
  await row.locator('.target-value-input').fill('Quux');
  await page.waitForResponse((response) => response.url().includes('/stage-4/overrides') && response.ok());

  // Then: only the whitespace-padded term is overridden
  const rows = await downloadCsvRows(page, fileId);
  expect(rows[0].col_a).toBe('Quux');
  expect(rows[1].col_a).toBe('Quux');
  expect(rows[2].col_a).toBe('Suggested');
});

test('BOM headers do not break overrides', async ({ page }) => {
  await mockHarmonizeSuccess(page);

  // Given: a BOM-prefixed CSV
  const fileId = await uploadAndAnalyze(page, fileFixture('bom.csv'));
  const preOverrides = await page.request.get(`/stage-4/overrides/${fileId}`);
  expect(await preOverrides.json()).toBeNull();
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileId, { 0: { col_a: 'Suggested' }, 1: { col_a: 'Suggested' } });

  // When: an override is applied
  await page.goto(`/stage-4?file_id=${fileId}`);
  await waitForReviewRows(page);
  const card = page.locator('.column-mode-grid .row-cell', {
    has: page.locator('.original-context-value', { hasText: 'Foo' }),
  }).first();
  await card.locator('.target-value-input').fill('Bar');
  await page.waitForResponse((response) => response.url().includes('/stage-4/overrides') && response.ok());

  // Then: download reflects overrides for all rows
  const rows = await downloadCsvRows(page, fileId);
  expect(rows[0].col_a).toBe('Bar');
  expect(rows[1].col_a).toBe('Bar');
});

test('no-change flow shows empty review state and zero summary', async ({ page }) => {
  await mockHarmonizeSuccess(page);

  // Given: a CSV with no harmonization changes
  const fileId = await uploadAndAnalyze(page, fileFixture('no-change.csv'));
  const preOverrides = await page.request.get(`/stage-4/overrides/${fileId}`);
  expect(await preOverrides.json()).toBeNull();
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileId, {});

  // When: review is opened
  await page.goto(`/stage-4?file_id=${fileId}`);
  await expect(page.locator('.review-empty')).toBeVisible();

  // Then: summary shows no changes
  await page.goto(`/stage-5?file_id=${fileId}`);
  await expect(page.locator('#summaryGrid .summary-empty')).toBeVisible();
});

test('autosave persists overrides across reloads', async ({ page }) => {
  await mockHarmonizeSuccess(page);

  // Given: a CSV with changes and review loaded
  const fileId = await uploadAndAnalyze(page, fileFixture('basic.csv'));
  const preOverrides = await page.request.get(`/stage-4/overrides/${fileId}`);
  expect(await preOverrides.json()).toBeNull();
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileId, { 0: { col_a: 'Suggested' } });

  await page.goto(`/stage-4?file_id=${fileId}`);
  await waitForReviewRows(page);
  const card = page.locator('.column-mode-grid .row-cell', {
    has: page.locator('.original-context-value', { hasText: 'Foo' }),
  }).first();
  await card.locator('.target-value-input').fill('Persisted');
  await page.waitForResponse((response) => response.url().includes('/stage-4/overrides') && response.ok());

  // When: the page is reloaded
  await page.reload();
  await waitForReviewRows(page);

  // Then: the override value is restored
  await expect(card.locator('.target-value-input')).toHaveValue('Persisted');
});

test('error handling: wrong file type and oversize upload', async ({ page }) => {
  await mockDataModels(page);

  // Given: a non-CSV file is uploaded
  await page.goto('/stage-1');
  await expect(page.locator('#statusMessage')).toBeEmpty();
  await page.setInputFiles('#fileInput', fileFixture('not-csv.json'));

  // Then: upload error is shown
  await expect(page.locator('#statusMessage')).toContainText(/Only CSV|Unsupported|Upload failed/i);

  // Given: an oversized CSV in a temp directory
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'data-chord-e2e-'));
  const oversizedPath = path.join(tmpDir, 'oversized.csv');
  try {
    const largeContent = 'col_a\n' + 'x'.repeat(26 * 1024 * 1024);
    fs.writeFileSync(oversizedPath, largeContent);

    // When: oversized file is uploaded
    await page.setInputFiles('#fileInput', oversizedPath);

    // Then: size error is shown
    await expect(page.locator('#statusMessage')).toContainText(/exceeds|too large|Upload failed/i);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test('error handling: harmonize failure and missing manifest', async ({ page }) => {
  await mockHarmonizeFailure(page);

  // Given: a CSV is uploaded and analyzed
  const fileId = await uploadAndAnalyze(page, fileFixture('basic.csv'));
  const preOverrides = await page.request.get(`/stage-4/overrides/${fileId}`);
  expect(await preOverrides.json()).toBeNull();

  // When: harmonize fails
  await clickHarmonize(page);
  await expect(page.locator('#stageThreeError')).toBeVisible();

  // Then: review shows missing manifest warning
  await page.goto(`/stage-4?file_id=${fileId}`);
  await expect(page.locator('.review-empty')).toBeVisible();

  await page.goto(`/stage-5?file_id=${fileId}`);
  await expect(page.locator('#summaryGrid .summary-empty')).toBeVisible();
});

test('multi-file isolation: overrides on one file do not affect another', async ({ page, context }) => {
  await mockHarmonizeSuccess(page);

  // Given: two files uploaded and harmonized
  const fileA = await uploadAndAnalyze(page, fileFixture('basic.csv'));
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileA, { 0: { col_a: 'Suggested' } });

  const pageB = await context.newPage();
  await mockHarmonizeSuccess(pageB);
  const fileB = await uploadAndAnalyze(pageB, fileFixture('basic.csv'));
  await clickHarmonize(pageB);
  await expect(pageB.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileB, { 0: { col_a: 'Suggested' } });

  // When: an override is applied to file A
  await page.goto(`/stage-4?file_id=${fileA}`);
  await waitForReviewRows(page);
  const card = page.locator('.column-mode-grid .row-cell', {
    has: page.locator('.original-context-value', { hasText: 'Foo' }),
  }).first();
  await card.locator('.target-value-input').fill('OnlyA');
  await page.waitForResponse((response) => response.url().includes('/stage-4/overrides') && response.ok());

  // Then: file B download remains unchanged
  const beforeOverride = await downloadCsvRows(pageB, fileB);
  expect(beforeOverride[0].col_a).toBe('Suggested');
  const rows = await downloadCsvRows(pageB, fileB);
  expect(rows[0].col_a).toBe('Suggested');
});

test('stage navigation links go to correct stages', async ({ page }) => {
  await mockHarmonizeSuccess(page);

  // Given: review page is open
  const fileId = await uploadAndAnalyze(page, fileFixture('basic.csv'));
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileId, {});
  await page.goto(`/stage-4?file_id=${fileId}`);

  // When: user clicks the upload step in the tracker
  await page.click('.progress-track .step[data-stage="upload"]');

  // Then: navigates to Stage 1
  await page.waitForURL(/\/stage-1/);
});
