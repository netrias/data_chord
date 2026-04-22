import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import {
  fileFixture,
  getFileIdFromUrl,
  uploadAndAnalyze,
  clickHarmonize,
  injectCdeOptions,
  mockDataModels,
  mockAnalyzeHarmonizationMix,
  mockAnalyzeNoMappings,
  mockHarmonizeSuccess,
  mockHarmonizeFailure,
  seedHarmonization,
  parseDownloadedCsv,
} from './utils.mjs';

const waitForReviewRows = async (page) => {
  await page.waitForFunction(() => {
    const selectors = ['.column-mode-grid', '.row-mode-wrapper', '.review-empty'];
    return selectors.some((selector) => {
      const el = document.querySelector(selector);
      if (!el) return false;
      const style = window.getComputedStyle(el);
      return style.visibility !== 'hidden' && style.display !== 'none' && el.getClientRects().length > 0;
    });
  });
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

test('multiple columns with changes show as separate tabs', async ({ page }) => {
  await mockHarmonizeSuccess(page);

  // Given: a CSV with changes in multiple columns
  const fileId = await uploadAndAnalyze(page, fileFixture('multi-column.csv'));
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  // Seed changes in col_a and col_b (different columns)
  seedHarmonization(fileId, {
    0: { col_a: 'Changed_A', col_b: 'Changed_B' },
    1: { col_a: 'Changed_A2', col_b: 'Changed_B2' },
  });

  // When: review page is loaded
  await page.goto(`/stage-4?file_id=${fileId}`);
  await waitForReviewRows(page);

  // Then: both column pills are visible
  const columnPills = page.locator('.batch-progress-item.column-pill');
  await expect(columnPills).toHaveCount(2);

  // And: each pill shows the column label
  const pillTexts = await columnPills.allTextContents();
  expect(pillTexts.some((text) => text.toLowerCase().includes('col_a') || text.toLowerCase().includes('col a'))).toBe(true);
  expect(pillTexts.some((text) => text.toLowerCase().includes('col_b') || text.toLowerCase().includes('col b'))).toBe(true);
});

test('clicking different column tabs shows different transformations', async ({ page }) => {
  await mockHarmonizeSuccess(page);

  // Given: a CSV with changes in multiple columns
  const fileId = await uploadAndAnalyze(page, fileFixture('multi-column.csv'));
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileId, {
    0: { col_a: 'UniqueA', col_b: 'UniqueB' },
  });

  await page.goto(`/stage-4?file_id=${fileId}`);
  await waitForReviewRows(page);

  // When: the first column tab is active
  const columnPills = page.locator('.batch-progress-item.column-pill');
  const firstPill = columnPills.first();
  await firstPill.click();
  await waitForReviewRows(page);

  // Then: transformations for that column are visible
  const firstColumnOriginalValues = await page.locator('.original-context-value').allTextContents();

  // When: clicking the second column tab
  const secondPill = columnPills.nth(1);
  await secondPill.click();
  await waitForReviewRows(page);

  // Then: transformations update to show different column's values
  const secondColumnOriginalValues = await page.locator('.original-context-value').allTextContents();

  // Verify the values changed (different column data displayed)
  expect(firstColumnOriginalValues).not.toEqual(secondColumnOriginalValues);
});

test('row mode shows all changed cells for each row', async ({ page }) => {
  await mockHarmonizeSuccess(page);

  // Given: a CSV with changes in multiple columns for the same row
  const fileId = await uploadAndAnalyze(page, fileFixture('multi-column.csv'));
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  // Row 0 has changes in both col_a and col_b
  seedHarmonization(fileId, {
    0: { col_a: 'Changed_A', col_b: 'Changed_B' },
  });

  await page.goto(`/stage-4?file_id=${fileId}`);
  await waitForReviewRows(page);

  // When: switching to row mode
  await page.click('#settingsButton');
  await page.selectOption('#reviewModeSelect', 'row');
  await page.click('#settingsCloseButton');
  await waitForReviewRows(page);

  // Then: row mode shows a row entry
  const rowEntries = page.locator('.row-mode-row');
  await expect(rowEntries.first()).toBeVisible();

  // And: the row contains cells for both changed columns
  const firstRow = rowEntries.first();
  const cellsInRow = firstRow.locator('.row-cell');
  const cellCount = await cellsInRow.count();

  // Should have at least 2 cells (one for each changed column)
  expect(cellCount).toBeGreaterThanOrEqual(2);
});

test('changing file clears previous session', async ({ page }) => {
  await mockDataModels(page);

  // Given: a file is uploaded
  const fileId1 = await uploadAndAnalyze(page, fileFixture('basic.csv'));

  // When: user clicks change file and uploads another
  await page.goto('/stage-1');
  await page.click('#changeFileButton');
  const fileId2 = await uploadAndAnalyze(page, fileFixture('basic.csv'));

  // Then: new file has different ID (fresh session)
  expect(fileId2).not.toBe(fileId1);
});

test('history dialog shows transformation summary and steps', async ({ page }) => {
  await mockHarmonizeSuccess(page);

  // Given: a file with harmonization changes
  const fileId = await uploadAndAnalyze(page, fileFixture('basic.csv'));
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileId, { 0: { col_a: 'Changed Value' } });

  // Navigate to Stage 5
  await page.goto(`/stage-5?file_id=${fileId}`);
  await page.locator('#summaryGrid').waitFor({ state: 'visible' });
  await page.locator('#changesTableBody tr').first().waitFor({ state: 'visible' });

  // When: User clicks a row in the changes table
  await page.click('#changesTableBody tr.clickable-row');
  const dialog = page.locator('.history-dialog');
  await dialog.waitFor({ state: 'visible' });

  // Then: Dialog shows title and column name
  await expect(dialog.locator('.history-dialog-title')).toContainText('Transformation History');
  await expect(dialog.locator('.history-dialog-subtitle')).toBeVisible();

  // And: Dialog shows original→final summary
  await expect(dialog.locator('.history-dialog-transform')).toBeVisible();

  // And: Each step has value, attribution, and timestamp on separate lines
  const steps = dialog.locator('.history-step');
  await expect(steps.first()).toBeVisible();
  await expect(steps.first().locator('.history-step__value-line')).toBeVisible();
  await expect(steps.first().locator('.history-step__attribution')).toBeVisible();
  await expect(steps.first().locator('.history-step__timestamp')).toBeVisible();

  // Close dialog
  await dialog.locator('button:has-text("Close")').click();
  await expect(dialog).toBeHidden();
});

test('history dialog shows PV conformance icons with tooltips', async ({ page }) => {
  await mockHarmonizeSuccess(page);

  // Given: a file with harmonization changes (some may be non-conformant)
  const fileId = await uploadAndAnalyze(page, fileFixture('basic.csv'));
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileId, { 0: { col_a: 'Changed Value' } });

  // Navigate to Stage 5
  await page.goto(`/stage-5?file_id=${fileId}`);
  await page.locator('#summaryGrid').waitFor({ state: 'visible' });
  await page.locator('#changesTableBody tr').first().waitFor({ state: 'visible' });

  // When: User opens history dialog
  await page.click('#changesTableBody tr.clickable-row');
  const dialog = page.locator('.history-dialog');
  await dialog.waitFor({ state: 'visible' });

  // Then: Steps show PV conformance icons
  const steps = dialog.locator('.history-step');
  await expect(steps.first()).toBeVisible();

  // Each step value line should have a PV icon (either conformant ✓ or warning ⚠)
  const pvIcons = dialog.locator('.history-step__pv-icon');
  await expect(pvIcons.first()).toBeVisible();

  // Icons should have tooltip on hover (data-tooltip attribute)
  const firstIcon = pvIcons.first();
  await expect(firstIcon).toHaveAttribute('data-tooltip');

  // Close dialog
  await dialog.locator('button:has-text("Close")').click();
});

test('history dialog shows correct attribution labels', async ({ page }) => {
  await mockHarmonizeSuccess(page);

  // Given: a file with AI changes
  const fileId = await uploadAndAnalyze(page, fileFixture('basic.csv'));
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileId, { 0: { col_a: 'AI Changed' } });

  // Navigate to Stage 5
  await page.goto(`/stage-5?file_id=${fileId}`);
  await page.locator('#summaryGrid').waitFor({ state: 'visible' });
  await page.locator('#changesTableBody tr').first().waitFor({ state: 'visible' });

  // When: User opens history dialog
  await page.click('#changesTableBody tr.clickable-row');
  const dialog = page.locator('.history-dialog');
  await dialog.waitFor({ state: 'visible' });

  // Then: Original step shows "Original value"
  const originalStep = dialog.locator('.history-step[data-source="original"]');
  await expect(originalStep.locator('.history-step__attribution')).toContainText('Original value');

  // And: AI step shows "Changed by Data Chord"
  const aiStep = dialog.locator('.history-step[data-source="ai"]');
  if (await aiStep.count() > 0) {
    await expect(aiStep.locator('.history-step__attribution')).toContainText('Changed by Data Chord');
  }

  // Close dialog
  await dialog.locator('button:has-text("Close")').click();
});

test('Stage 2 blocks Harmonize when no column has an effective CDE mapping', async ({ page }) => {
  // Given: Stage 2 loaded with a payload that has no effective CDE mappings
  await mockDataModels(page);
  await mockAnalyzeNoMappings(page);
  await page.goto('/stage-1');
  await page.setInputFiles('#fileInput', fileFixture('basic.csv'));
  await page.locator('#analyzeButton').waitFor({ state: 'visible' });
  await page.waitForFunction(() => !document.querySelector('#analyzeButton')?.disabled);
  await page.click('#analyzeButton');
  const confirmButton = page.locator('.data-model-confirm-btn');
  await confirmButton.waitFor({ state: 'visible' });
  await confirmButton.click();
  await page.waitForURL(/\/stage-2/);

  // Baseline negative assertions — must hold before the hover action under test:
  //   - button is disabled (no mappings configured yet)
  //   - tooltip is not yet visible (requires hover on wrapper to appear)
  await expect(page.locator('#harmonizeButton')).toBeDisabled();
  await expect(page.locator('.harmonize-action-tooltip')).toBeHidden();

  // When: hover the wrapper (pointer-events: none on disabled button means wrapper receives hover)
  await page.locator('.progress-tracker-action').hover();

  // Then: tooltip becomes visible with the correct message
  await expect(page.locator('.harmonize-action-tooltip')).toBeVisible();
  await expect(page.locator('.harmonize-action-tooltip')).toContainText(
    'Select a CDE mapping for at least one column before harmonizing.'
  );
});

test('Stage 2 shows pass-through badge on no_permissible_values rows and none on harmonizable rows', async ({ page }) => {
  // Given: analyze mock with one harmonizable column (diagnosis) and one no_permissible_values column (middle_name)
  await mockDataModels(page);
  await mockAnalyzeHarmonizationMix(page);
  await page.goto('/stage-1');

  // Baseline negative assertion — Stage 1 does not render harmonization badges
  await expect(page.locator('.harmonization-badge')).toHaveCount(0);

  await page.setInputFiles('#fileInput', fileFixture('basic.csv'));
  await page.locator('#analyzeButton').waitFor({ state: 'visible' });
  await page.waitForFunction(() => !document.querySelector('#analyzeButton')?.disabled);
  await page.click('#analyzeButton');
  const confirmButton = page.locator('.data-model-confirm-btn');
  await confirmButton.waitFor({ state: 'visible' });
  await confirmButton.click();
  await page.waitForURL(/\/stage-2/);

  // Wait for the mapping rows to render
  const rows = page.locator('#mappingResults .mapping-row');
  await rows.first().waitFor({ state: 'visible' });
  await expect(rows).toHaveCount(2);

  // Then: diagnosis row has no badge; middle_name row shows the pass-through dash badge
  const diagnosisRow = rows.filter({ hasText: 'diagnosis' }).first();
  const middleNameRow = rows.filter({ hasText: 'middle_name' }).first();

  await expect(diagnosisRow.locator('.harmonization-badge')).toHaveCount(0);
  const middleNameBadge = middleNameRow.locator('.harmonization-badge--pass-through');
  await expect(middleNameBadge).toBeVisible();
  await expect(middleNameBadge).toHaveText('⊘');
  // Accessible name for AT users is carried on the icon; the visible tooltip sibling
  // is rendered via a custom element so it can appear immediately on hover.
  const expectedTooltip = 'This target CDE has no permissible values. This means we cannot harmonize your data against a set of known good values so no transformation will be applied to this data.';
  await expect(middleNameBadge).toHaveAttribute('aria-label', expectedTooltip);
  const tooltipNode = middleNameRow.locator('.mapping-td-badge .icon-tooltip');
  await expect(tooltipNode).toHaveText(expectedTooltip);
});

test('Stage 2 pass-through badge disappears when user overrides to a harmonizable CDE', async ({ page }) => {
  // Given: analyze mock with a pass-through column (middle_name) and a harmonizable CDE available for override
  // Inject a harmonizable CDE option so the combobox has something to pick beyond "No Mapping"
  await injectCdeOptions(page, [
    { cde_id: 99, cde_key: 'primary_diagnosis', label: 'primary_diagnosis', description: 'Primary diagnosis' },
  ]);
  await mockDataModels(page);
  await mockAnalyzeHarmonizationMix(page);
  await page.goto('/stage-1');

  // Baseline negative assertion — pass-through badge not yet present on Stage 1
  await expect(page.locator('.harmonization-badge--pass-through')).toHaveCount(0);

  await page.setInputFiles('#fileInput', fileFixture('basic.csv'));
  await page.locator('#analyzeButton').waitFor({ state: 'visible' });
  await page.waitForFunction(() => !document.querySelector('#analyzeButton')?.disabled);
  await page.click('#analyzeButton');
  const confirmButton = page.locator('.data-model-confirm-btn');
  await confirmButton.waitFor({ state: 'visible' });
  await confirmButton.click();
  await page.waitForURL(/\/stage-2/);

  // Wait for the mapping rows to render
  const rows = page.locator('#mappingResults .mapping-row');
  await rows.first().waitFor({ state: 'visible' });
  const middleNameRow = rows.filter({ hasText: 'middle_name' }).first();

  // Pre-condition: the pass-through badge is visible before the override
  await expect(middleNameRow.locator('.harmonization-badge--pass-through')).toBeVisible();

  // When: user opens the override combobox on the middle_name row and picks the injected harmonizable CDE
  const toggleBtn = middleNameRow.locator('.combobox-toggle');
  await toggleBtn.click();
  const dropdown = middleNameRow.locator('.combobox-dropdown');
  await dropdown.waitFor({ state: 'visible' });
  // Pick the first non-muted, non-empty option (the injected harmonizable CDE)
  const realCdeOption = dropdown.locator('.combobox-option:not(.combobox-option--muted):not(.combobox-option--empty)').first();
  await realCdeOption.click();

  // Then: the pass-through badge is gone — this column is now overridden to a harmonizable CDE
  await expect(middleNameRow.locator('.harmonization-badge--pass-through')).toHaveCount(0);
});


test('Stage 2 enables Harmonize after the user selects a CDE for one column', async ({ page }) => {
  // Given: Stage 2 loaded with no effective CDE mappings but real CDE options available for override
  await injectCdeOptions(page);
  await mockDataModels(page);
  await mockAnalyzeNoMappings(page);
  await page.goto('/stage-1');
  await page.setInputFiles('#fileInput', fileFixture('basic.csv'));
  await page.locator('#analyzeButton').waitFor({ state: 'visible' });
  await page.waitForFunction(() => !document.querySelector('#analyzeButton')?.disabled);
  await page.click('#analyzeButton');
  const confirmButton = page.locator('.data-model-confirm-btn');
  await confirmButton.waitFor({ state: 'visible' });
  await confirmButton.click();
  await page.waitForURL(/\/stage-2/);

  // Baseline negative assertion — button is disabled before the user selects a CDE
  await expect(page.locator('#harmonizeButton')).toBeDisabled();

  // When: open the first mapping row's combobox and pick the first real CDE (not "No Mapping")
  const firstRow = page.locator('#mappingResults .mapping-row').first();
  await firstRow.waitFor({ state: 'visible' });
  const toggleBtn = firstRow.locator('.combobox-toggle');
  await toggleBtn.click();

  // Pick the first option that is not "No Mapping" from the open dropdown
  const dropdown = firstRow.locator('.combobox-dropdown');
  await dropdown.waitFor({ state: 'visible' });
  const realCdeOption = dropdown.locator('.combobox-option:not(.combobox-option--muted):not(.combobox-option--empty)').first();
  await realCdeOption.click();

  // Then: button becomes enabled and the blocked state is cleared
  await expect(page.locator('#harmonizeButton')).toBeEnabled();
  await expect(page.locator('.progress-tracker-action')).not.toHaveClass(/is-blocked/);

  // And: tooltip stays hidden on hover (gate is lifted)
  await page.locator('.progress-tracker-action').hover();
  await expect(page.locator('.harmonize-action-tooltip')).toBeHidden();
});
