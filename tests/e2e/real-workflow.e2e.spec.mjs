import { test, expect } from '@playwright/test';

import {
  clickHarmonize,
  fileFixture,
  parseDownloadedCsv,
  uploadAndAnalyzeReal,
} from './utils.mjs';

// These real jobs share the local server's single harmonization slot.
test.describe.configure({ mode: 'default' });

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

test('real workflow: restoring an original value keeps its warning and clears the saved edit', async ({ page }) => {
  // Given: A real job keeps an unapproved source because no recommendation exists.
  const fileId = await uploadAndAnalyzeReal(page, fileFixture('restore-original.csv'));
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled({ timeout: 15_000 });
  await page.click('#reviewButton');
  await page.waitForURL(/\/stage-4/);
  await waitForReviewRows(page);
  const card = page.locator('.row-cell').filter({
    has: page.locator('.original-context-value', { hasText: 'adamantinoma' }),
  });
  const expectOriginal = async () => {
    await expect(card.locator('.pv-combobox-link')).toHaveText('adamantinoma');
    await expect(card.locator('.pv-warning-icon')).toBeVisible();
    await expect(card.locator('.pv-conformant-icon')).toBeHidden();
    await expect(card.locator('.card-result-note')).toHaveText(
      'No match found. Source value kept. Choose an approved value.',
    );
    await expect(card.getByRole('button', { name: 'Restore original value' })).toBeDisabled();
  };
  const nextSave = () => page.waitForResponse(
    (response) => response.request().method() === 'POST'
      && response.url().endsWith('/stage-4/overrides'),
  );
  await expectOriginal();

  // When: The reviewer saves an approved edit and reloads the persisted review.
  const editSaved = nextSave();
  await card.locator('.pv-combobox-link').click();
  await page.locator('.pv-selection-option[data-value="Carcinoma NOS"]').click();
  expect((await editSaved).ok()).toBeTruthy();
  await page.reload();
  await waitForReviewRows(page);

  // Then: The saved edit is displayed as approved and can be restored.
  await expect(card.locator('.pv-combobox-link')).toHaveText('Carcinoma NOS');
  await expect(card.locator('.pv-warning-icon')).toBeHidden();
  await expect(card.locator('.pv-conformant-icon')).toBeVisible();
  await expect(card.getByRole('button', { name: 'Restore original value' })).toBeEnabled();

  // When: The reviewer restores the original value from this loaded review.
  const restoredSave = nextSave();
  await card.getByRole('button', { name: 'Restore original value' }).click();
  const restoredResponse = await restoredSave;

  // Then: The real API saves no overrides and the warning returns immediately.
  expect(restoredResponse.ok()).toBeTruthy();
  expect(restoredResponse.request().postDataJSON().overrides).toEqual({});
  await expectOriginal();

  // When: A view change rebuilds cards from the same loaded rows, without a refetch.
  await page.click('#settingsButton');
  const modeSaved = nextSave();
  await page.selectOption('#reviewModeSelect', 'row');
  expect((await modeSaved).ok()).toBeTruthy();
  await page.click('#settingsCloseButton');

  // Then: The discarded server edit does not return during the rebuild or reload.
  await expectOriginal();
  await page.reload();
  await waitForReviewRows(page);
  await expectOriginal();

  // When: The reviewer continues with the unapproved original and exports the result.
  await page.click('#stageFiveButton');
  await page.waitForURL(/\/stage-5/);
  await expect(page.locator('#conformanceWarningDialog')).toBeVisible();
  await expect(page.locator('#conformanceWarningDialog')).toContainText('adamantinoma');
  await page.click('#conformanceProceedButton');
  await expect(page.locator('[data-impact-metric="manual_values"]')).toContainText('0');
  const downloadResponse = await page.request.post('/stage-5/download', {
    data: { file_id: fileId },
  });
  expect(downloadResponse.ok()).toBeTruthy();

  // Then: Export preserves the restored source, the AI result, and the unchanged value.
  const rows = await parseDownloadedCsv(downloadResponse);
  expect(rows.map((row) => row.diagnosis)).toEqual(['Breast Cancer', 'adamantinoma', 'Diabetes']);
});
