import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import AdmZip from 'adm-zip';

import {
  clickHarmonize,
  downloadedZipEntries,
  fileFixture,
  getFileIdFromUrl,
  injectCdeOptions,
  mockDataModels,
  mockHarmonizeFailure,
  mockHarmonizeSuccess,
  parseDownloadedCsv,
  parseDownloadedCsvTable,
  seedHarmonization,
  uploadAndAnalyze,
} from '../../e2e/utils.mjs';

const requirementFixture = (name) => path.resolve('tests/requirements/e2e/fixtures', name);

const requirementJsonFixture = (name) => JSON.parse(fs.readFileSync(requirementFixture(name), 'utf-8'));

const requirementMessage = (requirementIds, message) => `[${requirementIds}] ${message}`;

const expectRequirement = (actual, requirementIds, message) => {
  return expect(actual, requirementMessage(requirementIds, message));
};

const expectPollRequirement = (callback, requirementIds, message) => {
  return expect.poll(callback, { message: requirementMessage(requirementIds, message) });
};

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

const reviewState = (reviewMode = 'column') => ({
  review_mode: reviewMode,
  sort_mode: 'original',
  scroll_mode: false,
  show_case_only_changes: false,
  show_unchanged_values: false,
  column_mode: { current_unit: 1, completed_units: [], flagged_units: [], batch_size: 5 },
  row_mode: { current_unit: 1, completed_units: [], flagged_units: [], batch_size: 5 },
});

const downloadResponse = async (page, fileId) => {
  const response = await page.request.post('/stage-5/download', { data: { file_id: fileId } });
  expectRequirement(response.ok(), 'R-053', 'Stage 5 download returns a successful export bundle').toBeTruthy();
  return response;
};

const downloadCsvRows = async (page, fileId) => parseDownloadedCsv(await downloadResponse(page, fileId));

const downloadCsvTable = async (page, fileId) => parseDownloadedCsvTable(await downloadResponse(page, fileId));

const zipCsvContent = async (page, fileId) => {
  const response = await downloadResponse(page, fileId);
  const zip = new AdmZip(Buffer.from(await response.body()));
  const entry = zip.getEntries().find((item) => item.entryName.endsWith('.csv'));
  if (!entry) {
    throw new Error('No CSV entry found in download.');
  }
  return entry.getData().toString('utf-8');
};

const uploadAndAnalyzeWithPayload = async (page, filePath, payloadForFile) => {
  await mockDataModels(page);
  await page.route('**/stage-1/analyze', async (route) => {
    const payload = route.request().postDataJSON?.() ?? {};
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payloadForFile(payload.file_id ?? '')),
    });
  });
  await page.goto('/stage-1');
  await page.setInputFiles('#fileInput', filePath);
  await page.locator('#analyzeButton').waitFor({ state: 'visible' });
  await page.waitForFunction(() => !document.querySelector('#analyzeButton')?.disabled);
  await page.click('#analyzeButton');
  await page.locator('.data-model-confirm-btn').click();
  await page.waitForURL(/\/stage-2/);
  return getFileIdFromUrl(page);
};

const analyzePayload = (name, fileId) => ({
  ...requirementJsonFixture(name),
  file_id: fileId,
});

const threeColumnAnalyzePayload = (fileId) => analyzePayload('three-column-analysis.json', fileId);

const duplicateAnalyzePayload = (fileId) => analyzePayload('duplicate-analysis.json', fileId);

const singleDiagnosisAnalyzePayload = (fileId) => analyzePayload('single-diagnosis-analysis.json', fileId);

const seedPvs = (fileId, columnId, columnName, cdeKey, values) => {
  execFileSync('uv', [
    'run',
    'python',
    path.resolve('tests/e2e/support/seed_pvs.py'),
    '--file-id',
    fileId,
    '--column-id',
    String(columnId),
    '--column-name',
    columnName,
    '--cde-key',
    cdeKey,
    '--values',
    ...values,
  ], { stdio: 'inherit' });
};

test('[R-004 R-005 R-008 R-009 R-027 R-043 R-044 R-045 R-049 R-051 R-052 R-053 R-054 R-055] browser workflow reaches export with persisted review decisions', async ({ page }) => {
  // Given: a CSV upload has not created any review overrides yet.
  await mockHarmonizeSuccess(page);
  const fileId = await uploadAndAnalyze(page, fileFixture('basic.csv'));
  const preOverrides = await page.request.get(`/stage-4/overrides/${fileId}`);
  expectRequirement(await preOverrides.json(), 'R-045', 'review overrides are absent before the user edits a value').toBeNull();

  // When: the user harmonizes, reviews a changed value, saves an override, and opens the summary.
  await clickHarmonize(page);
  await expectRequirement(page.locator('#reviewButton'), 'R-004', 'workflow reaches the review stage after harmonization').toBeEnabled();
  seedHarmonization(fileId, { 0: { col_a: 'Suggested' } });
  await page.goto(`/stage-4?file_id=${fileId}`);
  await waitForReviewRows(page);
  const card = page.locator('.column-mode-grid .row-cell', {
    has: page.locator('.original-context-value', { hasText: 'Foo' }),
  }).first();
  await expectRequirement(card.locator('.target-value-input'), 'R-044', 'target input has not already been manually overridden').not.toHaveValue('Reviewed');
  await expectRequirement(card.locator('.original-context-value'), 'R-043', 'review shows the original value beside the target value').toContainText('Foo');
  await card.locator('.target-value-input').fill('Reviewed');
  await page.waitForResponse((response) => response.url().includes('/stage-4/overrides') && response.ok());
  await page.reload();
  await waitForReviewRows(page);
  await expectRequirement(card.locator('.target-value-input'), 'R-045', 'manual review override persists after page reload').toHaveValue('Reviewed');
  await page.goto(`/stage-5?file_id=${fileId}`);
  await page.locator('#changesTableBody tr').first().waitFor({ state: 'visible' });
  await page.click('#changesTableBody tr.clickable-row');

  // Then: Stage 5 shows transformation history and export applies the saved override.
  const dialog = page.locator('.history-dialog');
  await expectRequirement(dialog.locator('.history-step[data-source="original"]'), 'R-051', 'history includes the original value step').toBeVisible();
  await expectRequirement(dialog.locator('.history-step[data-source="ai"]'), 'R-049 R-051', 'history includes the AI harmonization step').toBeVisible();
  await expectRequirement(dialog.locator('.history-step[data-source="user"]'), 'R-052', 'history includes the manual override step').toBeVisible();
  const rows = await downloadCsvRows(page, fileId);
  expectRequirement(rows[0].col_a, 'R-055', 'export applies the saved review override to the harmonized CSV').toBe('Reviewed');
  const entries = await downloadedZipEntries(await downloadResponse(page, fileId));
  expectRequirement(entries.some((entry) => entry.endsWith('_manifest.json')), 'R-054', 'export bundle includes a human-readable manifest artifact').toBeTruthy();
});

test('[R-015 R-016 R-017 R-018 R-019] Stage 2 resolves accepted, overridden, and unmapped columns by position', async ({ page }) => {
  // Given: Stage 2 has three positional columns with AI suggestions and no manual selections.
  await injectCdeOptions(page, [
    { cde_id: 101, cde_key: 'primary_diagnosis', label: 'primary_diagnosis', description: 'Primary diagnosis' },
    { cde_id: 102, cde_key: 'status_code', label: 'status_code', description: 'Status code' },
    { cde_id: 103, cde_key: 'clinical_note', label: 'clinical_note', description: 'Clinical note' },
    { cde_id: 104, cde_key: 'vital_status', label: 'vital_status', description: 'Vital status' },
  ]);
  let harmonizePayload = null;
  await page.route('**/stage-3/harmonize', async (route) => {
    harmonizePayload = route.request().postDataJSON();
    const fileId = harmonizePayload.file_id;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        job_id: 'requirements-mapping',
        status: 'succeeded',
        detail: 'Harmonization completed.',
        next_stage_url: `/stage-4?file_id=${fileId}&job_id=requirements-mapping&status=succeeded`,
        job_id_available: true,
        manifest_summary: null,
      }),
    });
  });
  await uploadAndAnalyzeWithPayload(page, fileFixture('basic.csv'), threeColumnAnalyzePayload);
  await expectRequirement(page.locator('#harmonizeButton'), 'R-017', 'Stage 2 allows harmonization when a mapping is available').toBeEnabled();

  // When: the user keeps one AI mapping, overrides one mapping, and explicitly leaves one unmapped.
  const statusRow = page.locator('#mappingResults .mapping-row').nth(1);
  await statusRow.locator('.combobox-toggle').click();
  await statusRow.locator('.combobox-option', { hasText: 'vital_status' }).click();
  const notesRow = page.locator('#mappingResults .mapping-row').nth(2);
  await notesRow.locator('.combobox-toggle').click();
  await notesRow.locator('.combobox-option', { hasText: 'No Mapping' }).click();
  await clickHarmonize(page);

  // Then: the browser-submitted request keeps stable column IDs and gives manual choices precedence.
  await expectPollRequirement(() => harmonizePayload, 'R-017', 'Stage 2 submits a harmonization request after user mapping decisions').not.toBeNull();
  expectRequirement(harmonizePayload.mapping_decisions, 'R-015 R-016 R-017 R-018 R-019', 'mapping decisions preserve positional identity, accepted AI mapping, manual override, and explicit no-mapping').toEqual([
    expect.objectContaining({ column_id: 0, column_name: 'diagnosis', cde_name: 'primary_diagnosis', method: 'ai_recommendation' }),
    expect.objectContaining({ column_id: 1, column_name: 'status', cde_name: 'vital_status', method: 'user_override' }),
    expect.objectContaining({ column_id: 2, column_name: 'notes', cde_name: null, method: 'user_override' }),
  ]);
});

test('[R-020 R-057 R-058] unmapped columns pass through export unchanged while mapped columns harmonize', async ({ page }) => {
  // Given: one column is explicitly unmapped before harmonization.
  await injectCdeOptions(page, [
    { cde_id: 101, cde_key: 'primary_diagnosis', label: 'primary_diagnosis', description: 'Primary diagnosis' },
    { cde_id: 102, cde_key: 'status_code', label: 'status_code', description: 'Status code' },
    { cde_id: 103, cde_key: 'clinical_note', label: 'clinical_note', description: 'Clinical note' },
    { cde_id: 104, cde_key: 'vital_status', label: 'vital_status', description: 'Vital status' },
  ]);
  await mockHarmonizeSuccess(page);
  const fileId = await uploadAndAnalyzeWithPayload(page, fileFixture('multi-column.csv'), threeColumnAnalyzePayload);
  const notesRow = page.locator('#mappingResults .mapping-row').nth(2);
  await notesRow.locator('.combobox-toggle').click();
  await notesRow.locator('.combobox-option', { hasText: 'No Mapping' }).click();
  await expectRequirement(page.locator('#harmonizeButton'), 'R-020', 'unmapped columns do not block harmonization of mapped columns').toBeEnabled();

  // When: another column is harmonized and the user downloads the export.
  await clickHarmonize(page);
  await expectRequirement(page.locator('#reviewButton'), 'R-020', 'workflow can continue after leaving one column unmapped').toBeEnabled();
  seedHarmonization(fileId, { 0: { col_a: 'Changed_A' } });
  const rows = await downloadCsvRows(page, fileId);

  // Then: the harmonized column changes and the unmapped column value remains untouched.
  expectRequirement(rows[0].col_a, 'R-020', 'mapped column is still harmonized').toBe('Changed_A');
  expectRequirement(rows[0].col_b, 'R-057 R-058', 'unmapped and untouched column value is preserved exactly in export').toBe('Apple');
});

test('[R-012 R-013 R-014 R-046 R-047 R-050 R-056 R-059] duplicate headers and short rows stay positional through review and export', async ({ page }) => {
  // Given: a CSV has duplicate headers and a row missing the trailing cell.
  await mockHarmonizeSuccess(page);
  const fileId = await uploadAndAnalyzeWithPayload(page, requirementFixture('duplicate-short.csv'), duplicateAnalyzePayload);
  await clickHarmonize(page);
  await expectRequirement(page.locator('#reviewButton'), 'R-013', 'duplicate-header workflow reaches review after harmonization').toBeEnabled();
  seedHarmonization(fileId, {
    0: { 0: 'AI_LEFT', 1: 'AI_RIGHT', 2: 'AI_TRAILING' },
    1: { 0: 'AI_LEFT_2', 1: 'AI_RIGHT_2' },
  });
  const rowContext = await page.request.post('/stage-4/row-context', {
    data: { file_id: fileId, row_indices: [0] },
  });
  expectRequirement(rowContext.ok(), 'R-047', 'row context request succeeds for duplicate-header rows').toBeTruthy();
  const contextBody = await rowContext.json();
  expectRequirement(contextBody.headers, 'R-012 R-013 R-047', 'row context preserves duplicate headers positionally').toEqual(['dup', 'dup', 'trailing']);
  expectRequirement(contextBody.rows[0], 'R-014 R-047', 'row context pads short rows without dropping positional cells').toEqual(['left', 'right', '']);

  // When: saved review overrides target each duplicate column and the padded trailing column by column ID.
  const saveResponse = await page.request.post('/stage-4/overrides', {
    data: {
      file_id: fileId,
      overrides: {
        1: {
          0: { ai_value: 'AI_LEFT', human_value: 'OVERRIDE_LEFT', original_value: 'left' },
          1: { ai_value: 'AI_RIGHT', human_value: 'OVERRIDE_RIGHT', original_value: 'right' },
          2: { ai_value: 'AI_TRAILING', human_value: 'OVERRIDE_TRAILING', original_value: '' },
        },
      },
      review_state: reviewState(),
    },
  });
  expectRequirement(saveResponse.ok(), 'R-046', 'review overrides save successfully by positional column ID').toBeTruthy();
  await page.goto(`/stage-5?file_id=${fileId}`);
  await page.locator('#changesTableBody tr').first().waitFor({ state: 'visible' });
  const table = await downloadCsvTable(page, fileId);
  const csvContent = await zipCsvContent(page, fileId);

  // Then: duplicate columns stay distinct, short rows are padded, and CSV output uses the project line ending.
  expectRequirement(table.headers, 'R-056', 'export preserves duplicate header names exactly').toEqual(['dup', 'dup', 'trailing']);
  expectRequirement(table.rows[0], 'R-046 R-059', 'export applies positional overrides and pads trailing short-row cells').toEqual(['OVERRIDE_LEFT', 'OVERRIDE_RIGHT', 'OVERRIDE_TRAILING']);
  expectRequirement(csvContent, 'R-060', 'exported CSV contains the project line terminator').toContain('\n');
  expectRequirement(csvContent, 'R-060', 'exported CSV does not switch to CRLF line endings').not.toContain('\r\n');
  const summaryRows = await page.locator('#changesTableBody tr.clickable-row').count();
  expectRequirement(summaryRows, 'R-050', 'summary keeps duplicate-named columns as distinct mappings').toBeGreaterThanOrEqual(2);
});

test('[R-036 R-048] review renders whitespace markers and restores review mode after reload', async ({ page }) => {
  // Given: review has whitespace-significant values and starts in column mode.
  await mockHarmonizeSuccess(page);
  const fileId = await uploadAndAnalyze(page, fileFixture('whitespace.csv'));
  await clickHarmonize(page);
  await expectRequirement(page.locator('#reviewButton'), 'R-036', 'whitespace fixture reaches review').toBeEnabled();
  seedHarmonization(fileId, { 0: { col_a: 'Suggested' } });
  await page.goto(`/stage-4?file_id=${fileId}`);
  await waitForReviewRows(page);
  await expectRequirement(page.locator('.row-mode-row'), 'R-048', 'review starts outside row mode before the user changes mode').toHaveCount(0);

  // When: the user switches to row mode and reloads the review page.
  await page.click('#settingsButton');
  await page.selectOption('#reviewModeSelect', 'row');
  await page.click('#settingsCloseButton');
  await waitForReviewRows(page);
  await expectRequirement(page.locator('.ws-marker').first(), 'R-036', 'review renders visible whitespace markers for meaningful whitespace').toBeVisible();
  const firstRow = page.locator('.row-mode-row').first();
  await firstRow.locator('.target-value-input').fill('Whitespace Reviewed');
  await page.waitForResponse((response) => response.url().includes('/stage-4/overrides') && response.ok());
  await page.reload();
  await waitForReviewRows(page);

  // Then: row mode and whitespace markers are still visible.
  await expectRequirement(page.locator('.row-mode-row').first(), 'R-048', 'review mode is restored after reload').toBeVisible();
  await expectRequirement(page.locator('.ws-marker').first(), 'R-036', 'visible whitespace markers remain after reload').toBeVisible();
});

test('[R-041] summary marks values outside permissible values as non-conformant', async ({ page }) => {
  // Given: a harmonized workflow has a value that is not in the persisted PV set.
  await mockHarmonizeSuccess(page);
  const fileId = await uploadAndAnalyzeWithPayload(page, requirementFixture('case.csv'), singleDiagnosisAnalyzePayload);

  // When: Stage 5 summary loads after PV data is seeded with only the canonical case.
  await clickHarmonize(page);
  await expectRequirement(page.locator('#reviewButton'), 'R-041', 'case-mismatch workflow reaches review before summary').toBeEnabled();
  seedHarmonization(fileId, {});
  seedPvs(fileId, 0, 'diagnosis', 'primary_diagnosis', ['Lung Cancer']);
  await page.goto(`/stage-5?file_id=${fileId}`);

  // Then: the summary shows the value as non-conformant.
  await expectRequirement(page.locator('.non-conformant-banner'), 'R-041', 'summary displays a non-conformant warning for exact PV mismatch').toBeVisible();
  await page.locator('button', { hasText: 'All' }).click();
  await expectRequirement(page.locator('#changesTableBody tr.non-conformant'), 'R-041', 'term mapping row is marked non-conformant when no value matches the PV set').toHaveCount(1);
});

test('[R-006 R-029 R-040 R-067 R-068 R-069] browser surfaces recoverable workflow errors without crashing', async ({ page }) => {
  // Given: the browser is on Stage 1 with no upload error visible.
  await mockDataModels(page);
  await page.goto('/stage-1');
  await expectRequirement(page.locator('#statusMessage'), 'R-006', 'Stage 1 starts with no recovery error shown').toBeEmpty();

  // When: the user uploads an unsupported file and later hits missing workflow state.
  await page.setInputFiles('#fileInput', fileFixture('not-csv.json'));
  await expectRequirement(page.locator('#statusMessage'), 'R-006 R-069', 'unsupported upload shows a plain-language recovery message').toContainText(/Only CSV|Unsupported|Upload failed/i);
  await mockHarmonizeFailure(page);
  const fileId = await uploadAndAnalyze(page, fileFixture('basic.csv'));
  await clickHarmonize(page);
  await expectRequirement(page.locator('#stageThreeError'), 'R-029 R-069', 'harmonization client failure is shown as a controlled user-facing error').toBeVisible();
  await page.goto(`/stage-4?file_id=${fileId}`);
  await expectRequirement(page.locator('.review-empty'), 'R-040 R-068', 'missing manifest opens a recoverable review empty state').toBeVisible();
  await page.goto('/stage-4?file_id=00000000');

  // Then: the user sees recoverable empty/error states and not a server traceback.
  await expectRequirement(page.locator('body'), 'R-069', 'recoverable error UI does not show a Python traceback').not.toContainText('Traceback');
  const missingUpload = await page.request.post('/stage-4/rows', { data: { file_id: '00000000' } });
  expectRequirement(missingUpload.status(), 'R-067', 'missing uploaded file returns a not-found response').toBe(404);
  expectRequirement((await missingUpload.json()).detail, 'R-067 R-069', 'missing uploaded file response tells the user what happened').toMatch(/not found|rerun/i);
});

test('[R-007 R-077 R-082 R-083 R-084] Stage 5 navigation can return to upload and start a fresh workflow', async ({ page }) => {
  // Given: a user has reached Stage 5 for one uploaded file.
  await mockHarmonizeSuccess(page);
  const firstFileId = await uploadAndAnalyze(page, fileFixture('basic.csv'));
  await clickHarmonize(page);
  await expectRequirement(page.locator('#reviewButton'), 'R-077', 'workflow reaches a later stage before backward navigation').toBeEnabled();
  seedHarmonization(firstFileId, {});
  await page.goto(`/stage-5?file_id=${firstFileId}`);
  await page.locator('#summaryGrid').waitFor({ state: 'visible' });

  // When: the user navigates back to upload and starts a new workflow.
  await page.click('.progress-track .step[data-stage="upload"]');
  await page.waitForURL(/\/stage-1/);
  await page.click('#changeFileButton');
  const secondFileId = await uploadAndAnalyze(page, fileFixture('basic.csv'));

  // Then: the new workflow gets a different file ID and does not reuse the previous file state.
  expectRequirement(secondFileId, 'R-007 R-083', 'starting a new workflow creates a fresh file identity').not.toBe(firstFileId);
  await expectRequirement(page, 'R-082 R-084', 'new workflow navigation returns to the new upload context').toHaveURL(new RegExp(`file_id=${secondFileId}`));
});

test('[R-078] navigating back to Stage 2 restores saved mapping state', async ({ page }) => {
  // Given: a user changed a Stage 2 mapping and continued to review.
  await injectCdeOptions(page, [
    { cde_id: 101, cde_key: 'primary_diagnosis', label: 'primary_diagnosis', description: 'Primary diagnosis' },
    { cde_id: 104, cde_key: 'vital_status', label: 'vital_status', description: 'Vital status' },
  ]);
  await mockHarmonizeSuccess(page);
  const fileId = await uploadAndAnalyzeWithPayload(page, fileFixture('basic.csv'), threeColumnAnalyzePayload);
  const statusRow = page.locator('#mappingResults .mapping-row').nth(1);
  await statusRow.locator('.combobox-toggle').click();
  await statusRow.locator('.combobox-option', { hasText: 'vital_status' }).click();
  await clickHarmonize(page);
  await expectRequirement(page.locator('#reviewButton'), 'R-078', 'workflow reaches review after changing Stage 2 state').toBeEnabled();
  seedHarmonization(fileId, {});

  // When: the user navigates back to Stage 2.
  await page.goto(`/stage-2?file_id=${fileId}`);

  // Then: the manually selected CDE is still shown instead of a blank or reset mapping.
  const restoredStatusRow = page.locator('#mappingResults .mapping-row').nth(1);
  await expectRequirement(restoredStatusRow, 'R-078', 'back navigation restores saved Stage 2 mapping state').toContainText('vital_status');
});

test('[R-079] forward controls distinguish navigation from workflow actions', async ({ page }) => {
  // Given: Stage 2 is loaded before harmonization has run.
  await mockHarmonizeSuccess(page);
  await uploadAndAnalyze(page, fileFixture('basic.csv'));

  // When: the user inspects the forward control.
  const action = page.locator('.progress-tracker-action');
  await expectRequirement(action, 'R-079', 'Stage 2 forward action control is visible').toBeVisible();

  // Then: the UI clearly indicates this control will run harmonization rather than merely navigate forward.
  await expectRequirement(action, 'R-079', 'Stage 2 forward action copy distinguishes harmonization from passive navigation').toContainText(/run|harmonize|start/i);
});

test.fail('[R-080 R-081] changing Stage 2 mapping after harmonization blocks stale review and export', async ({ page }) => {
  // Given: a workflow has already been harmonized.
  await injectCdeOptions(page, [
    { cde_id: 101, cde_key: 'primary_diagnosis', label: 'primary_diagnosis', description: 'Primary diagnosis' },
    { cde_id: 104, cde_key: 'vital_status', label: 'vital_status', description: 'Vital status' },
  ]);
  await mockHarmonizeSuccess(page);
  const fileId = await uploadAndAnalyzeWithPayload(page, fileFixture('basic.csv'), threeColumnAnalyzePayload);
  await clickHarmonize(page);
  await expectRequirement(page.locator('#reviewButton'), 'R-080 R-081', 'workflow reaches review before upstream mapping changes').toBeEnabled();
  seedHarmonization(fileId, { 0: { col_a: 'Suggested' } });

  // When: the user changes an upstream mapping after harmonization.
  await page.goto(`/stage-2?file_id=${fileId}`);
  const statusRow = page.locator('#mappingResults .mapping-row').nth(1);
  await statusRow.locator('.combobox-toggle').click();
  await statusRow.locator('.combobox-option', { hasText: 'vital_status' }).click();
  await page.goto(`/stage-4?file_id=${fileId}`);

  // Then: stale downstream review is blocked until harmonization runs again.
  await expectRequirement(page.locator('body'), 'R-080 R-081', 'changed mappings block stale review until harmonization runs again').toContainText(/rerun|reharmonize|out of date|stale/i);
});
