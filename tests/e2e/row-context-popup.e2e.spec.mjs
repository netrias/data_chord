import fs from 'node:fs';
import path from 'node:path';
import { expect, test } from '@playwright/test';

import {
  clickHarmonize,
  mockHarmonizeSuccess,
  seedHarmonization,
  uploadAndAnalyze,
} from './utils.mjs';

const _waitForReviewRows = async (page) => {
  await page.waitForFunction(() => document.querySelector('.column-mode-grid') !== null);
};

const _openReviewWithRepeatedRows = async (page, testInfo, rowCount = 100) => {
  await mockHarmonizeSuccess(page);
  const csvPath = testInfo.outputPath('row-context.csv');
  fs.mkdirSync(path.dirname(csvPath), { recursive: true });
  const rows = Array.from({ length: rowCount }, (_, index) => `RID-${index + 1},Foo`);
  fs.writeFileSync(csvPath, `record_id,col_a\n${rows.join('\n')}\n`);
  const fileId = await uploadAndAnalyze(page, csvPath);
  await clickHarmonize(page);
  seedHarmonization(fileId, Object.fromEntries(
    Array.from({ length: rowCount }, (_, index) => [index, { col_0001: 'Suggested' }]),
  ));
  await page.goto(`/stage-4?file_id=${fileId}`);
  await _waitForReviewRows(page);
};

test('row context popup renders through the local virtual table', async ({ page }, testInfo) => {
  // Given: Stage 4 contains one changed value repeated across 100 source rows.
  await _openReviewWithRepeatedRows(page, testInfo);

  // When: the reviewer opens the source-row context.
  await page.locator('.entry-row-label').click();

  // Then: the real popup shows the source rows through the bounded local table.
  const dialog = page.locator('.row-context-dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('[data-virtual-row="0"]')).toContainText('RID-1');
  await expect(dialog.locator('.row-context-table')).toHaveAttribute('aria-rowcount', '101');
  expect(await dialog.locator('[data-virtual-row]').count()).toBeLessThan(50);
});

test('row context popup reaches the last source row through production scrolling', async ({ page }, testInfo) => {
  // Given: the real 100-row popup is open with production markup and CSS.
  await _openReviewWithRepeatedRows(page, testInfo);
  await page.locator('.entry-row-label').click();
  const dialog = page.locator('.row-context-dialog');
  await expect(dialog).toBeVisible();

  // When: the reviewer scrolls the popup to the end.
  await dialog.locator('.row-context-table-wrapper').evaluate(async (scrollElement) => {
    scrollElement.scrollTop = scrollElement.scrollHeight;
    scrollElement.dispatchEvent(new Event('scroll'));
    await new Promise((resolve) => requestAnimationFrame(resolve));
  });

  // Then: the last source row renders and the DOM window remains bounded.
  await expect(dialog.locator('[data-virtual-row="99"]')).toContainText('RID-100');
  expect(await dialog.locator('[data-virtual-row]').count()).toBeLessThan(50);
});

test('closing a loading row context popup aborts its request', async ({ page }, testInfo) => {
  // Given: the source-row request is delayed while the loading popup is open.
  await _openReviewWithRepeatedRows(page, testInfo);
  let requestFailed = false;
  page.on('requestfailed', (request) => {
    if (request.url().endsWith('/stage-4/row-context')) requestFailed = true;
  });
  await page.route('**/stage-4/row-context', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 500));
    try {
      await route.continue();
    } catch {
      // The browser can close the aborted request before Playwright resumes it.
    }
  });
  await page.locator('.entry-row-label').click();
  const dialog = page.locator('.row-context-dialog');
  await expect(dialog.locator('.row-context-loading')).toBeVisible();

  // When: the reviewer closes the popup before row loading finishes.
  await dialog.locator('.row-context-close-btn').click();

  // Then: the request is aborted and the closed popup does not return.
  await expect(dialog).toHaveCount(0);
  await expect.poll(() => requestFailed).toBe(true);
});
