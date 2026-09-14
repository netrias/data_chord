import { execFileSync } from 'node:child_process';
import { test, expect } from '@playwright/test';

import { e2eEnv } from './runtime-env.mjs';

import {
  clickHarmonize,
  fileFixture,
  parseDownloadedCsv,
  parseDownloadedCsvTable,
  uploadAndAnalyzeReal,
} from './utils.mjs';

// These real jobs share the local server's single harmonization slot.
test.describe.configure({ mode: 'default' });

test('real workflow: missing cells stay in the download but not in review or checked counts', async ({ page }) => {
  // Given: empty and space-only cells share a column with three present values.
  const fileId = await uploadAndAnalyzeReal(page, fileFixture('missing-values.csv'));

  // When: the real job completes and the reviewer opens Stage 4.
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled({ timeout: 15_000 });
  const jobId = new URL(page.url()).searchParams.get('job_id');
  expect(jobId).toBeTruthy();
  const jobResponse = await page.request.get(`/stage-3/jobs/${jobId}?file_id=${fileId}`);
  expect(jobResponse.ok()).toBeTruthy();
  const job = await jobResponse.json();

  // Then: only present terms were checked; the dataset still has seven rows.
  expect(job.manifest_summary.total_terms).toBe(3);
  expect(job.manifest_summary.source_row_count).toBe(7);
  await expect(page.locator('#stageThreeCheckedCount')).toHaveText('3');
  await expect(page.locator('#stageThreeRowCount')).toContainText('7 rows');
  const rowsResponse = page.waitForResponse(
    (response) => response.url().endsWith('/stage-4/rows') && response.ok(),
  );
  await page.click('#reviewButton');
  await waitForReviewRows(page);
  const review = await (await rowsResponse).json();
  expect(review.totalOriginalRows).toBe(7);
  expect(review.columns.flatMap((column) => column.transformations.map((item) => ({
    original: item.originalValue, indices: item.rowIndices,
  })))).toEqual([
    { original: 'breast ca', indices: [1] },
    { original: 'Diabetes', indices: [4] },
    { original: 'adamantinoma', indices: [5] },
  ]);
  await expect(page.locator('.row-cell .original-context-value')).toHaveText([
    'breast ca', 'adamantinoma',
  ]);

  // When: a client tries to replace the original space-only cell.
  const invalidEdit = await page.request.post('/stage-4/overrides', {
    headers: { 'If-None-Match': '*' },
    data: {
      file_id: fileId,
      overrides: { '3': { col_0000: { original_value: '   ', human_value: 'Diabetes' } } },
      review_state: {},
    },
  });
  // Then: the server rejects the edit instead of changing missing data.
  expect(invalidEdit.status()).toBe(400);

  // When: row mode rebuilds the review and the browser reloads it.
  await page.click('#settingsButton');
  const filterSaved = page.waitForResponse((response) => (
    response.url().endsWith('/stage-4/overrides') && response.request().method() === 'POST'
  ));
  await page.check('#showUnchangedValues');
  expect((await filterSaved).ok()).toBeTruthy();
  await expect(page.locator('.row-cell')).toHaveCount(3);
  const viewSaved = page.waitForResponse((response) => (
    response.url().endsWith('/stage-4/overrides') && response.request().method() === 'POST'
  ));
  await page.selectOption('#reviewModeSelect', 'row');
  expect((await viewSaved).ok()).toBeTruthy();
  await page.click('#settingsCloseButton');
  await page.reload();
  await waitForReviewRows(page);
  // Then: no missing-value cards appear in the rebuilt review.
  await expect(page.locator('.row-cell .original-context-value')).toHaveText([
    'breast ca', 'Diabetes', 'adamantinoma',
  ]);

  // When: the reviewer continues and downloads the real output.
  const summaryResponse = page.waitForResponse(
    (response) => response.url().endsWith('/stage-5/summary') && response.ok(),
  );
  await page.click('#stageFiveButton');
  await page.waitForURL(/\/stage-5/);
  await page.click('#conformanceProceedButton');
  const summary = await (await summaryResponse).json();
  const downloadResponse = await page.request.post('/stage-5/download', { data: { file_id: fileId } });
  expect(downloadResponse.ok()).toBeTruthy();

  // Then: result terms exclude missing cells, while export keeps every source position.
  expect(summary.column_summaries.map((column) => ({
    terms: column.distinct_terms, rows: column.total_rows,
  }))).toEqual([{ terms: 3, rows: 7 }]);
  expect(summary.term_mappings.map((item) => item.original_value).sort()).toEqual(
    ['Diabetes', 'adamantinoma', 'breast ca'],
  );
  const rows = await parseDownloadedCsv(downloadResponse);
  expect(rows.map((row) => row.diagnosis)).toEqual([
    'Breast Cancer', '', '   ', 'Diabetes', 'adamantinoma', '', '  ',
  ]);
});

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

test('real workflow: zero-term success keeps file context and downloads missing cells', async ({ page }) => {
  // Given: an unmapped identifier retains row positions and diagnosis has only missing cells.
  const fileId = await uploadAndAnalyzeReal(page, fileFixture('missing-only.csv'));
  // When: the real job completes with no source terms to process.
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled({ timeout: 15_000 });
  // Then: Stage 3 shows successful neutral completion, not an approved-value claim.
  await expect(page.locator('#stageThreeHeadline')).toHaveText('Harmonization complete');
  await expect(page.locator('#stageThreeResultMessage')).toHaveText(
    'No unique values were checked against an approved list.',
  );
  await expect(page.locator('#stageThreeCheckedCount')).toHaveText('0');
  await expect(page.locator('#stageThreeRowCount')).toContainText('3 rows');
  await expect(page.locator('#stageThreeSourceFile')).toHaveText('missing-only.csv');
  await expect(page.locator('#stageThreeLegend li')).toHaveCount(0);

  // When: the reviewer opens the real review and summary screens.
  const reviewResponse = page.waitForResponse((response) => (
    response.url().endsWith('/stage-4/rows') && response.ok()
  ));
  await page.click('#reviewButton');
  await waitForReviewRows(page);
  // Then: Stage 4 exposes no missing-source items, but preserves the source row count.
  expect(await (await reviewResponse).json()).toEqual({
    columns: [], columnPVs: {}, totalOriginalRows: 3, reviewState: null,
  });
  await expect(page.locator('.row-cell')).toHaveCount(0);
  await expect(page.locator('.review-empty')).toBeVisible();
  const summaryResponse = page.waitForResponse((response) => (
    response.url().endsWith('/stage-5/summary') && response.ok()
  ));
  await page.click('#stageFiveButton');
  await page.waitForURL(/\/stage-5/);
  const summary = await (await summaryResponse).json();
  expect({ columns: summary.column_summaries, terms: summary.term_mappings,
    warnings: summary.non_conformant_items }).toEqual({ columns: [], terms: [], warnings: [] });

  // When: the reviewer downloads the completed zero-term job.
  const downloadResponse = await page.request.post('/stage-5/download', { data: { file_id: fileId } });
  expect(downloadResponse.ok()).toBeTruthy();
  // Then: the exported rows and whitespace cells are unchanged.
  expect(await parseDownloadedCsv(downloadResponse)).toEqual([
    { record_id: 'RID-1', diagnosis: '' },
    { record_id: 'RID-2', diagnosis: '   ' },
    { record_id: 'RID-3', diagnosis: '  ' },
  ]);
});

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

test('classic dataset: confirmed mappings, full text, restore, and exact export', async ({ page }) => {
  test.setTimeout(60_000);
  // Given: the reusable CSV has 20 identified cases and 11 position-stable columns.
  // Python's standard reader is independent of the JavaScript download reader.
  const records = JSON.parse(execFileSync('uv', ['run', 'python', '-c',
    'import csv,json,sys; print(json.dumps(list(csv.reader(open(sys.argv[1],newline="",encoding="utf-8")))))',
    fileFixture('classic-workflow.csv')], { encoding: 'utf8', env: e2eEnv }));
  const [headers, ...sourceRows] = records;
  expect(headers).toEqual(['case_id', 'diagnosis', 'disease_type', 'primary_site', 'morphology',
    'site_of_origin', 'sample_anatomic_site', 'sample_type', 'gender', 'race', 'ethnicity']);
  expect(sourceRows.map((row) => row[0])).toEqual(
    Array.from({ length: 20 }, (_, i) => `C${String(i + 1).padStart(2, '0')}`),
  );
  expect(sourceRows.every((row) => row.length === 11)).toBe(true);
  const presentValues = sourceRows.filter((row) => !['C05', 'C06', 'C07', 'C08', 'C09', 'C20'].includes(row[0]))
    .map((row) => row[1]);
  const distinctSources = [...new Set(presentValues)];
  expect(distinctSources).toHaveLength(13);
  const expectedRows = sourceRows.map((row) => (
    row[1] === 'breast ca' ? [row[0], 'Breast Cancer', ...row.slice(2)] : row
  ));
  const fileId = await uploadAndAnalyzeReal(page, fileFixture('classic-workflow.csv'));
  const mappings = Object.fromEntries(headers.map((_, i) => [
    `col_${String(i).padStart(4, '0')}`, i === 1 ? 'primary_diagnosis' : null,
  ]));

  // When: the real mapping endpoint saves every column choice, then the page reloads.
  const saved = await page.request.post('/stage-2/choices', {
    data: { file_id: fileId, manual_overrides: mappings, column_renames: {} },
  });
  expect(saved.ok()).toBe(true);
  await page.reload();
  await expect(page.locator('#mappingRows .mapping-row').first()).toBeVisible();
  const analysisResponse = await page.request.get(`/stage-1/analysis/${fileId}`);
  expect(analysisResponse.ok()).toBe(true);
  const analysis = await analysisResponse.json();

  // Then: persisted choices prove that only diagnosis is mapped; all other cells pass through.
  expect(analysis.manual_overrides).toEqual(mappings);
  expect(analysis.total_rows).toBe(20);

  // When: a real no-cost job runs through the normal Stage 3 controls.
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled({ timeout: 15_000 });
  // Then: missing sources and repeated terms do not inflate unique checked counts.
  await expect(page.locator('#stageThreeCheckedCount')).toHaveText('13');
  await expect(page.locator('#stageThreeRowCount')).toContainText('20 rows');
  const reviewReady = page.waitForResponse((response) => response.url().endsWith('/stage-4/rows') && response.ok());
  await page.click('#reviewButton');
  const review = await (await reviewReady).json();
  expect(review.totalOriginalRows).toBe(20);
  expect(review.columns.map((column) => column.columnKey)).toEqual(['col_0001']);
  expect(review.columns.flatMap((column) => column.transformations.map((item) => item.originalValue)).sort())
    .toEqual([...distinctSources].sort());
  await expect(page.locator('.row-cell')).toHaveCount(12);
  await page.click('#settingsButton');
  await page.check('#showUnchangedValues');
  await page.click('#settingsCloseButton');
  await expect(page.locator('.row-cell')).toHaveCount(13);
  expect((await page.locator('.pv-combobox-link').allTextContents()).sort())
    .toEqual([...new Set(expectedRows.filter((row) => presentValues.includes(row[1]) || row[1] === 'Breast Cancer')
      .map((row) => row[1]))].sort());

  // When: long values render at a normal laptop width and the card opens value editing.
  await page.setViewportSize({ width: 1044, height: 921 });
  await assertCompleteCardValues(page, sourceRows);


  const original = page.locator('.row-cell').filter({ hasText: 'adamantinoma' });
  await expect(original.locator('.pv-warning-icon')).toBeVisible();
  const nextSave = () => page.waitForResponse((response) => response.url().endsWith('/stage-4/overrides')
    && response.request().method() === 'POST');
  // When: an approved edit is saved and loaded again.
  const editSaved = nextSave();
  await original.locator('.pv-combobox-link').click();
  await page.locator('.pv-selection-option[data-value="Carcinoma NOS"]').click();
  expect((await editSaved).ok()).toBe(true);
  await page.reload();
  // Then: the saved edit has approval, not the original warning.
  await expect(original.locator('.pv-combobox-link')).toHaveText('Carcinoma NOS');
  await expect(original.locator('.pv-conformant-icon')).toBeVisible();
  await expect(original.locator('.pv-warning-icon')).toBeHidden();

  // When: restore is saved, row mode is selected, and review is loaded again.
  const restored = nextSave();
  await original.getByRole('button', { name: 'Restore original value' }).click();
  const restoreResponse = await restored;
  expect(restoreResponse.ok()).toBe(true);
  expect(restoreResponse.request().postDataJSON().overrides).toEqual({});
  await expect(original.locator('.pv-warning-icon')).toBeVisible();
  await page.click('#settingsButton');
  await page.selectOption('#reviewModeSelect', 'row');
  const modeSaved = nextSave();
  await page.selectOption('#batchSizeSelect', '15');
  expect((await modeSaved).ok()).toBe(true);
  await page.click('#settingsCloseButton');
  await page.reload();
  // Then: every present source row appears, including repeated rows, but no missing row appears.
  await expect(page.locator('.row-cell')).toHaveCount(14);
  expect(await page.locator('.pv-combobox-link').allTextContents())
    .toEqual(expectedRows.filter((row) => !['C05', 'C06', 'C07', 'C08', 'C09', 'C20'].includes(row[0]))
      .map((row) => row[1]));
  await expect(original.locator('.pv-warning-icon')).toBeVisible();
  await expect(original.locator('.pv-combobox-link')).toHaveText('adamantinoma');
  await assertCompleteCardValues(page, sourceRows);

  // When: the reviewer continues and downloads the actual completed output.
  await page.click('#stageFiveButton');
  await page.waitForURL(/\/stage-5/);
  await expect(page.locator('#conformanceWarningDialog')).toContainText('11 values');
  await page.click('#conformanceProceedButton');
  await expect(page.locator('[data-impact-metric="manual_values"]')).toContainText('0');
  const response = await page.request.post('/stage-5/download', { data: { file_id: fileId } });
  expect(response.ok()).toBe(true);
  // Then: the whole export has the expected columns and cells, including exact line breaks and whitespace.
  expect(await parseDownloadedCsvTable(response)).toEqual({ headers, rows: expectedRows });
});

async function assertCompleteCardValues(page, sourceRows) {
  for (const id of ['C12', 'C13', 'C17']) {
    const value = sourceRows.find((row) => row[0] === id)[1];
    const card = page.locator('.row-cell').filter({ has: page.locator('.pv-combobox-link', { hasText: value }) });
    const link = card.locator('.pv-combobox-link');
    // Then: the active value is complete and the control does not clip its content.
    expect(await link.textContent()).toBe(value);
    expect(await link.evaluate((element) => ({
      clipsWidth: element.scrollWidth > element.clientWidth + 1,
      clipsHeight: element.scrollHeight > element.clientHeight + 1,
    }))).toEqual({ clipsWidth: false, clipsHeight: false });
    await card.locator('.card-body').click({ position: { x: 4, y: 4 } });
    await expect(page.locator('#pv-modal-title')).toBeVisible();
    expect(await page.locator('.pv-selection-current').textContent()).toBe(value);
    await page.locator('.pv-selection-close-btn').click();
  }
}
