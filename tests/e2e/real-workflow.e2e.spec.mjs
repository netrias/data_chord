import { test, expect } from '@playwright/test';

import {
  clickHarmonize,
  fileFixture,
  parseDownloadedCsv,
  uploadAndAnalyzeReal,
} from './utils.mjs';

const waitForReviewRows = async (page) => {
  await page.waitForFunction(() => {
    const selectors = ['.column-mode-grid', '.row-mode-wrapper', '.review-empty'];
    return selectors.some((selector) => {
      const element = document.querySelector(selector);
      if (!element) return false;
      const style = window.getComputedStyle(element);
      return style.visibility !== 'hidden'
        && style.display !== 'none'
        && element.getClientRects().length > 0;
    });
  });
};

test('real workflow: upload, map, harmonize, review, summarize, and download', async ({ page }) => {
  // Given: A small input whose mapping and harmonization are deterministic.
  const fileId = await uploadAndAnalyzeReal(page, fileFixture('real-workflow.csv'));

  // When: The browser completes mapping and the real Stage 3 job.
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled({ timeout: 15_000 });
  await page.click('#reviewButton');
  await page.waitForURL(/\/stage-4/);
  await waitForReviewRows(page);

  const reviewedCell = page.locator('.row-cell').filter({
    has: page.locator('.original-context-value', { hasText: 'breast ca' }),
  });
  await expect(reviewedCell).toHaveCount(1);
  await expect(reviewedCell).toContainText('Breast Cancer');

  const saveResponse = page.waitForResponse(
    (response) => response.request().method() === 'POST'
      && response.url().endsWith('/stage-4/overrides'),
  );
  await reviewedCell.locator('.pv-combobox-link').click();
  await page.locator('.pv-selection-option[data-value="Carcinoma NOS"]').click();
  expect((await saveResponse).ok()).toBeTruthy();

  await page.click('#stageFiveButton');
  await page.waitForURL(/\/stage-5/);
  await expect(page.locator('.quality-certificate')).toBeVisible();
  await expect(page.locator('[data-impact-metric="manual_values"]')).toContainText('1');

  const downloadResponse = await page.request.post('/stage-5/download', {
    data: { file_id: fileId },
  });
  expect(downloadResponse.ok()).toBeTruthy();
  const rows = await parseDownloadedCsv(downloadResponse);

  // Then: The exported data contains both the AI result and the reviewer decision.
  expect(rows[0].diagnosis).toBe('Carcinoma NOS');
  expect(rows[1].diagnosis).toBe('Diabetes');

  // The API assertion above owns ZIP content. This assertion proves that the
  // visible download control is connected to that route as a browser download.
  const downloadPromise = page.waitForEvent('download');
  await page.click('#downloadResults');
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.zip$/);
});
