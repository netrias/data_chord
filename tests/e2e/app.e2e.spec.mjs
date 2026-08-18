import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  AGENT_FILE_INPUT,
  fileFixture,
  getFileIdFromUrl,
  uploadAndAnalyze,
  uploadAndAnalyzeSheet,
  clickHarmonize,
  mockColumnDetail,
  mockAnalyze,
  mockDataModels,
  mockDataModelsWithVersionCount,
  mockHarmonizeSuccess,
  mockHarmonizeFailure,
  seedHarmonization,
  parseDownloadedCsv,
  parseDownloadedCsvTable,
  parseDownloadedTabular,
  createWorkbookFixture,
  parseDownloadedWorkbook,
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

const downloadTsvRows = async (page, fileId) => {
  const response = await page.request.post('/stage-5/download', { data: { file_id: fileId } });
  expect(response.ok()).toBeTruthy();
  return parseDownloadedTabular(response, '.tsv', '\t');
};

const downloadWorkbookRows = async (page, fileId, sheetName) => {
  const response = await page.request.post('/stage-5/download', { data: { file_id: fileId } });
  expect(response.ok()).toBeTruthy();
  return parseDownloadedWorkbook(response, sheetName);
};

const _stage2Column = (key, header = key, overrides = {}) => ({
  column_name: header,
  column_key: key,
  source_index: Number.parseInt(key.slice(4), 10),
  header,
  inferred_type: 'text',
  has_non_empty_values: true,
  confidence_bucket: 'high',
  confidence_score: 0.9,
  ...overrides,
});

const _stage2Cde = (key, type) => ({
  cde_id: key.length,
  cde_key: key,
  label: key,
  description: `${key} description`,
  cde_type: type,
});

const _stage2HarnessHtml = (cdeCatalog) => `
<!DOCTYPE html>
<html lang="en">
  <head><meta charset="utf-8" /><title>Stage 2 Harness</title></head>
  <body>
    <nav class="progress-tracker">
      <ol>
        <li class="step" data-stage="upload" data-url="/stage-1"></li>
        <li class="step" data-stage="mapping" data-url="/stage-2"></li>
        <li class="step" data-stage="harmonize" data-url="/stage-3"></li>
      </ol>
      <button id="harmonizeButton" disabled><span class="btn-3d-front">Harmonize →</span></button>
      <div id="stepInstruction"><p class="step-instruction-text"></p><span class="step-instruction-tooltip"></span></div>
    </nav>
    <main>
      <aside class="filter-sidebar hidden" id="filterSidebar"></aside>
      <div id="sourceFilter"></div>
      <input id="colSearch" />
      <div class="mapping-list-head">
        <button id="columnSortBtn" type="button"><span>Your column</span><span class="mapping-list-head-sort-arrow"></span></button>
        <div></div>
        <button id="targetSortBtn" type="button"><span>Target common data element</span><span class="mapping-list-head-sort-arrow"></span></button>
        <button id="valueFitSortBtn" type="button"><span>Value fit</span><span class="mapping-list-head-sort-arrow"></span></button>
        <div></div>
      </div>
      <div id="mappingRows"></div>
      <div id="mappingEmptyState" class="hidden"></div>
      <p id="harmonizeError" class="hidden" role="alert"></p>
    </main>
    <div class="takeover hidden" id="takeover">
      <div class="takeover-backdrop" data-action="close-takeover"></div>
      <div class="takeover-card" id="takeoverCard"></div>
    </div>
    <script>
      window.stageTwoConfig = {
        analyzeEndpoint: "/stage-1/analyze",
        columnDetailBase: "/stage-2/column-detail",
        dataModelKey: "gc",
        externalVersionNumber: "11.0.4",
        cdeCatalog: ${JSON.stringify(cdeCatalog)},
        noMappingLabel: "No Mapping",
        stageThreeUrl: "/stage-3",
        harmonizeEndpoint: "/stage-3/harmonize"
      };
    </script>
    <script type="module" src="/assets/stage-2/stage_2_mappings.js"></script>
  </body>
</html>
`;

test('no-recommendation card warns when its displayed source value is not permissible', async ({ page }) => {
  const fileId = '0123456789abcdef0123456789abcdef';
  let savedOverrides = null;
  await page.route('**/stage-4/rows', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        columns: [{
          columnKey: 'col_0000',
          columnLabel: 'diagnosis',
          targetCdeKey: 'primary_diagnosis',
          targetCdeLabel: 'primary_diagnosis',
          sourceColumnIndex: 0,
          termCount: 2,
          termsWithChanges: 0,
          transformations: [
            {
              originalValue: 'adamantinoma',
              harmonizedValue: null,
              matchFidelity: 'none',
              isChanged: false,
              recommendationType: 'no_recommendation',
              isPVConformant: false,
              pvSetAvailable: true,
              topSuggestions: [],
              rowIndices: [8692],
              manualOverride: null,
            },
            {
              originalValue: 'Lung Cancer',
              harmonizedValue: null,
              matchFidelity: 'none',
              isChanged: false,
              recommendationType: 'no_recommendation',
              isPVConformant: true,
              pvSetAvailable: true,
              topSuggestions: [],
              rowIndices: [8693],
              manualOverride: null,
            },
          ],
        }],
        columnPVs: { col_0000: ['Carcinoma NOS', 'Lung Cancer'] },
        totalOriginalRows: 10000,
      }),
    });
  });
  await page.route('**/stage-4/overrides/*', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: 'null' });
  });
  await page.route('**/stage-4/overrides', async (route) => {
    savedOverrides = route.request().postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { ETag: '"test-version"' },
      body: JSON.stringify({ file_id: fileId, updated_at: '2026-08-14T00:00:00Z' }),
    });
  });

  // Given: Stage 4 receives no recommendation and the source is outside the target PV set
  await page.goto(`/stage-4?file_id=${fileId}`);

  // When: the browser renders the review card
  await waitForReviewRows(page);
  const cards = page.locator('.row-cell.no-recommendation');
  const rejectedCard = cards.filter({ has: page.locator('.original-context-value', { hasText: 'adamantinoma' }) });
  const permittedCard = cards.filter({ has: page.locator('.original-context-value', { hasText: 'Lung Cancer' }) });

  // Then: each displayed source has one clear conformance state and no question marker
  await expect(cards).toHaveCount(2);
  await expect(rejectedCard.locator('.pv-combobox-link')).toHaveText('adamantinoma');
  await expect(rejectedCard.locator('.pv-warning-icon')).toBeVisible();
  await expect(rejectedCard.locator('.pv-conformant-icon')).toBeHidden();
  await expect(rejectedCard.locator('.card-header-row')).not.toHaveClass(/pv-conformant/);
  await expect(permittedCard.locator('.pv-warning-icon')).toBeHidden();
  await expect(permittedCard.locator('.pv-conformant-icon')).toBeVisible();
  await expect(permittedCard.locator('.card-header-row')).toHaveClass(/pv-conformant/);
  for (const card of await cards.all()) {
    const markerContent = await card.evaluate((element) => getComputedStyle(element, '::after').content);
    expect(markerContent).toBe('none');
  }

  // When: the reviewer replaces the rejected source with a permitted value
  const saveResponse = page.waitForResponse(
    (response) => response.request().method() === 'POST' && response.url().endsWith('/stage-4/overrides'),
  );
  await rejectedCard.locator('.pv-combobox-link').click();
  await page.locator('.pv-selection-option[data-value="Carcinoma NOS"]').click();
  await saveResponse;

  // Then: the card becomes conformant, stays a no-recommendation card, and saves the override
  await expect(rejectedCard.locator('.pv-warning-icon')).toBeHidden();
  await expect(rejectedCard.locator('.pv-conformant-icon')).toBeVisible();
  await expect(rejectedCard.locator('.card-header-row')).toHaveClass(/pv-conformant/);
  await expect(rejectedCard).toHaveClass(/no-recommendation/);
  expect(savedOverrides.overrides['8692'].col_0000.human_value).toBe('Carcinoma NOS');
});

test('happy path flow: upload → analyze → harmonize → review → summary → download', async ({ page }) => {
  await mockHarmonizeSuccess(page);

  // Given: a CSV file is uploaded and analyzed
  const fileId = await uploadAndAnalyze(page, fileFixture('basic.csv'));

  // When: the user proceeds to harmonize
  await clickHarmonize(page);

  // Then: harmonize completes and review can continue
  await expect(page.locator('#reviewButton')).toBeEnabled();

  seedHarmonization(fileId, {
    0: { col_0001: 'Baz' },
    1: { col_0001: 'Baz' },
  });

  // The completed Stage 3 view uses the durable production-shaped summary.
  const durableJobId = `e2e-job-${fileId}`;
  await page.goto(`/stage-3?file_id=${fileId}&job_id=${durableJobId}`);
  const stageThreeTable = page.locator('[data-column-outcome-table] .column-outcome-table');
  await expect(stageThreeTable).toBeVisible();
  await expect(stageThreeTable.getByRole('columnheader')).toHaveText([
    'Column',
    'Unique values harmonized',
    'Rows affected',
    'Status',
  ]);
  const stageThreeOutcome = stageThreeTable.locator('tbody tr').filter({ hasText: 'col_a' });
  await expect(stageThreeOutcome).toContainText('0 of 2');
  await expect(stageThreeOutcome).toContainText('2 of 3');
  await expect(page.locator('#stageThreeDial')).toHaveAttribute(
    'aria-label',
    'No values were checked against an approved list.',
  );
  await expect(page.locator('#stageThreeHeadline')).toHaveText('Harmonization complete');
  await expect(page.getByText('No values were checked against an approved list.')).toBeVisible();
  await expect(page.getByText('Confidence', { exact: true })).toHaveCount(0);

  await page.click('#reviewButton');
  await page.waitForURL(/\/stage-4/);
  await waitForReviewRows(page);

  await page.click('#stageFiveButton');
  await page.waitForURL(/\/stage-5/);
  await expect(page.locator('.quality-certificate')).toBeVisible();
  await expect(page.locator('[data-impact-metric="total_values"]')).toContainText('2');
  await expect(page.locator('[data-impact-metric="unique_values"]')).toContainText('1');
  await expect(page.locator('[data-impact-metric="manual_values"]')).toContainText('0');
  await expect(page.locator('.provenance-line')).toHaveCount(0);

  const rows = await downloadCsvRows(page, fileId);
  expect(rows[0].col_a).toBe('Baz');

  // And: a real UI download reveals the Stage 5 start-over action.
  await expect(page.locator('#uploadNavAction')).toHaveClass(/hidden/);
  const downloadPromise = page.waitForEvent('download');
  await page.click('#downloadResults');
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.zip$/);
  await expect(page.locator('#uploadNavAction')).not.toHaveClass(/hidden/);
});

test('completed Stage 3 shows the durable dial, action table, and remaining columns', async ({ page }) => {
  const fileId = '0123456789abcdef0123456789abcdef';
  const jobId = 'durable-stage-three-design';
  const summary = {
    total_terms: 7,
    changed_terms: 3,
    non_conformant_terms: 2,
    source_file_name: 'partner_submission.csv',
    reference_model_label: 'GDC',
    reference_model_version: '3.0',
    match_fidelity_counts: [],
    column_breakdowns: [
      {
        column_name: 'rewritten',
        label: 'rewritten',
        column_key: 'col_0000',
        source_column_index: 0,
        review_status: 'clear',
        total_rows: 20,
        changed_rows: 10,
        unchanged_rows: 10,
        unique_terms: 2,
        unique_terms_changed: 1,
        successfully_harmonized_terms: 1,
        unique_terms_unchanged: 1,
        non_conformant_terms: 0,
        match_fidelity_counts_changed: [],
      },
      {
        column_name: 'unchanged_unresolved',
        label: 'unchanged_unresolved',
        column_key: 'col_0001',
        source_column_index: 1,
        review_status: 'needs_attention',
        total_rows: 20,
        changed_rows: 0,
        unchanged_rows: 20,
        unique_terms: 2,
        unique_terms_changed: 0,
        successfully_harmonized_terms: 0,
        unique_terms_unchanged: 2,
        non_conformant_terms: 1,
        match_fidelity_counts_changed: [],
      },
      {
        column_name: 'failed_rewrite',
        label: 'failed_rewrite',
        column_key: 'col_0002',
        source_column_index: 2,
        review_status: 'needs_attention',
        total_rows: 20,
        changed_rows: 4,
        unchanged_rows: 16,
        unique_terms: 1,
        unique_terms_changed: 1,
        successfully_harmonized_terms: 0,
        unique_terms_unchanged: 0,
        non_conformant_terms: 1,
        match_fidelity_counts_changed: [],
      },
      {
        column_name: 'already_matched',
        label: 'already_matched',
        column_key: 'col_0003',
        source_column_index: 3,
        review_status: 'clear',
        total_rows: 20,
        changed_rows: 0,
        unchanged_rows: 20,
        unique_terms: 1,
        unique_terms_changed: 0,
        successfully_harmonized_terms: 0,
        unique_terms_unchanged: 1,
        non_conformant_terms: 0,
        match_fidelity_counts_changed: [],
      },
      {
        column_name: 'not_checked',
        label: 'not_checked',
        column_key: 'col_0004',
        source_column_index: 4,
        review_status: 'not_checked',
        total_rows: 20,
        changed_rows: 0,
        unchanged_rows: 20,
        unique_terms: 2,
        unique_terms_changed: 0,
        successfully_harmonized_terms: 0,
        unique_terms_unchanged: 2,
        non_conformant_terms: 0,
        match_fidelity_counts_changed: [],
      },
    ],
  };
  await page.route('**/stage-3/jobs/*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        job_id: jobId,
        status: 'succeeded',
        detail: 'Harmonization completed.',
        next_stage_url: `/stage-4?file_id=${fileId}&job_id=${jobId}&status=succeeded`,
        job_id_available: true,
        manifest_summary: summary,
      }),
    });
  });

  // Given: only a durable completed-job URL is available in a fresh browser session.
  await page.goto('/stage-1');
  await page.evaluate(() => sessionStorage.clear());

  // When: Stage 3 reloads the completed result from the job API.
  await page.goto(`/stage-3?file_id=${fileId}&job_id=${jobId}`);

  // Then: the result uses the durable display context and exact value partition.
  await expect(page.locator('#stageThreeHeadline')).toContainText('successfully harmonized 1 value');
  await expect(page.getByText('partner_submission.csv', { exact: true })).toBeVisible();
  await expect(page.getByText('Checked against GDC 3.0', { exact: true })).toBeVisible();
  await expect(page.locator('#stageThreeDial')).toHaveAttribute(
    'aria-label',
    'Of 6 checked values: 1 was successfully harmonized; 3 already matched; 2 could not be harmonized.',
  );

  const table = page.locator('[data-column-outcome-table] .column-outcome-table');
  await expect(table).toBeVisible();
  await expect(table.locator('tbody tr')).toHaveCount(3);
  await expect(table.locator('tbody tr').nth(0)).toContainText('unchanged_unresolved');
  await expect(table.locator('tbody tr')).toContainText([
    'unchanged_unresolved',
    'failed_rewrite',
    'rewritten',
  ]);
  await expect(table).not.toContainText('already_matched');
  await expect(table).not.toContainText('not_checked');

  const remaining = page.locator('[data-remaining-columns]');
  await expect(remaining).toContainText('2 columns passed through unchanged');
  await remaining.locator('summary').click();
  await expect(remaining).toContainText('already_matched');
  await expect(remaining).toContainText('not_checked');
  await expect(remaining).not.toContainText('unchanged_unresolved');
  await expect(page.locator('#reviewButton')).toBeEnabled();

  // When: the user continues to verification.
  await page.locator('#reviewButton').click();

  // Then: the existing Stage 4 navigation contract remains unchanged.
  await page.waitForURL(/\/stage-4/);
});

test('an earlier completed Stage 3 job does not invent detailed value groups', async ({ page }) => {
  const fileId = '0123456789abcdef0123456789abcdef';
  const jobId = 'legacy-stage-three-job';
  await page.route('**/stage-3/jobs/*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        job_id: jobId,
        status: 'succeeded',
        detail: 'Harmonization completed.',
        next_stage_url: `/stage-4?file_id=${fileId}&job_id=${jobId}&status=succeeded`,
        job_id_available: true,
        manifest_summary: {
          total_terms: 2,
          changed_terms: 1,
          non_conformant_terms: 0,
          match_fidelity_counts: [],
          column_breakdowns: [{
            column_name: 'legacy_column',
            label: 'legacy_column',
            column_key: 'col_0000',
            source_column_index: 0,
            review_status: 'clear',
            total_rows: 2,
            changed_rows: 1,
            unchanged_rows: 1,
            unique_terms: 2,
            unique_terms_changed: 1,
            unique_terms_unchanged: 1,
            non_conformant_terms: 0,
            match_fidelity_counts_changed: [],
          }],
        },
      }),
    });
  });

  // Given: a completed job was stored before exact Stage 3 value groups existed.
  await page.goto('/stage-1');
  await page.evaluate(() => sessionStorage.clear());

  // When: Stage 3 reloads that earlier result from its durable job URL.
  await page.goto(`/stage-3?file_id=${fileId}&job_id=${jobId}`);

  // Then: the page preserves useful results without inventing a dial partition.
  await expect(page.locator('#stageThreeHeadline')).toHaveText('Harmonization complete');
  await expect(page.locator('#stageThreeDial')).not.toBeVisible();
  await expect(page.getByText('Detailed value groups are not available for this earlier result.')).toBeVisible();
  await expect(page.locator('[data-column-outcome-table]')).toContainText('Not recorded');
  await expect(page.locator('#reviewButton')).toBeEnabled();
});

test('Stage 5 confirms start-over after successful download and clears browser workflow state', async ({ page }) => {
  /*
   * Given: the user has reached Stage 5 with workflow state in session storage
   * When:  download fails, then succeeds, then the user cancels and confirms start-over
   * Then:  the start-over action appears only after success, cancel preserves state,
   *        and confirm clears Data Chord workflow keys before returning to Stage 1.
   */
  await mockHarmonizeSuccess(page);
  let downloadAttempts = 0;
  await page.route('**/stage-5/download', async (route) => {
    downloadAttempts += 1;
    if (downloadAttempts === 1) {
      await route.fulfill({ status: 500, body: 'Download failed' });
      return;
    }
    await route.fulfill({
      status: 200,
      headers: {
        'Content-Type': 'application/zip',
        'Content-Disposition': 'attachment; filename="harmonized_data.zip"',
      },
      body: 'zip-bytes',
    });
  });

  const fileId = await uploadAndAnalyze(page, fileFixture('basic.csv'));
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileId, {});

  await page.click('#reviewButton');
  await page.waitForURL(/\/stage-4/);
  await waitForReviewRows(page);
  await page.click('#stageFiveButton');
  await page.waitForURL(/\/stage-5/);
  await page.locator('#summaryGrid').waitFor({ state: 'visible' });

  // Given: workflow state exists, while an unrelated session key should be preserved.
  await page.evaluate(() => {
    sessionStorage.setItem('unrelatedKey', 'keep-me');
  });
  const keysBeforeStartOver = await page.evaluate(() => ({
    currentFileSession: sessionStorage.getItem('currentFileSession'),
    stage2Payload: sessionStorage.getItem('stage2Payload'),
    stage3HarmonizeJob: sessionStorage.getItem('stage3HarmonizeJob'),
    maxReachedStage: sessionStorage.getItem('maxReachedStage'),
    unrelatedKey: sessionStorage.getItem('unrelatedKey'),
  }));
  expect(keysBeforeStartOver.currentFileSession).not.toBeNull();
  expect(keysBeforeStartOver.stage2Payload).not.toBeNull();
  expect(keysBeforeStartOver.stage3HarmonizeJob).not.toBeNull();
  expect(keysBeforeStartOver.maxReachedStage).not.toBeNull();
  expect(keysBeforeStartOver.unrelatedKey).toBe('keep-me');
  await expect(page.locator('#uploadNavAction')).toHaveClass(/hidden/);

  // When: the download fails.
  await page.click('#downloadResults');

  // Then: the start-over action is still hidden.
  await expect(page.locator('#downloadError')).toBeVisible();
  await expect(page.locator('#uploadNavAction')).toHaveClass(/hidden/);

  // When: the download succeeds.
  await page.click('#downloadResults');

  // Then: the start-over action appears.
  await expect(page.locator('#uploadNavAction')).not.toHaveClass(/hidden/);

  // When: the user opens the dialog and cancels.
  await page.click('#startOverButton');
  await expect(page.locator('#startOverDialog')).toBeVisible();
  await page.click('#startOverCancel');

  // Then: they stay on Stage 5 and workflow state remains.
  await expect(page.locator('#startOverDialog')).toBeHidden();
  expect(page.url()).toContain('/stage-5');
  const keysAfterCancel = await page.evaluate(() => ({
    currentFileSession: sessionStorage.getItem('currentFileSession'),
    stage2Payload: sessionStorage.getItem('stage2Payload'),
    stage3HarmonizeJob: sessionStorage.getItem('stage3HarmonizeJob'),
    maxReachedStage: sessionStorage.getItem('maxReachedStage'),
    unrelatedKey: sessionStorage.getItem('unrelatedKey'),
  }));
  expect(keysAfterCancel).toEqual(keysBeforeStartOver);

  // When: the user confirms start-over.
  await page.click('#startOverButton');
  await page.click('#startOverConfirm');
  await page.waitForURL(/\/stage-1$/);

  // Then: Stage 1 is empty and only the Data Chord workflow keys were cleared.
  await expect(page.locator('#dropzoneCopy')).not.toHaveClass(/hidden/);
  await expect(page.locator('#analyzeButton')).toBeDisabled();
  const keysAfterConfirm = await page.evaluate(() => ({
    currentFileSession: sessionStorage.getItem('currentFileSession'),
    stage2Payload: sessionStorage.getItem('stage2Payload'),
    stage3HarmonizeJob: sessionStorage.getItem('stage3HarmonizeJob'),
    maxReachedStage: sessionStorage.getItem('maxReachedStage'),
    unrelatedKey: sessionStorage.getItem('unrelatedKey'),
  }));
  expect(keysAfterConfirm).toEqual({
    currentFileSession: null,
    stage2Payload: null,
    stage3HarmonizeJob: null,
    maxReachedStage: null,
    unrelatedKey: 'keep-me',
  });
});

test('Stage 2 list opens a takeover on row click', async ({ page }) => {
  /*
   * Given: a CSV is analyzed and Stage 2 lands on the list view
   * When:  a row is clicked
   * Then:  the takeover opens with "Your column" + "Target common data element"
   *        panes; closing the takeover returns to the list.
   */
  await mockColumnDetail(page);
  await uploadAndAnalyze(page, fileFixture('basic.csv'));

  // Negative: takeover starts hidden
  await expect(page.locator('#takeover')).toHaveClass(/hidden/);
  // List rows render
  const row = page.locator('#mappingRows .mapping-row').first();
  await expect(row).toBeVisible();

  // Open takeover
  await row.click();
  await expect(page.locator('#takeover')).not.toHaveClass(/hidden/);
  await expect(page.locator('.takeover-pane--data .takeover-pane-title')).toHaveText(/your column/i);
  await expect(page.locator('.takeover-pane--target .takeover-pane-title')).toHaveText(/target common data element/i);

  // Close via the ✕ button
  await page.locator('.takeover-btn--close').click();
  await expect(page.locator('#takeover')).toHaveClass(/hidden/);
});

test('Stage 2 splits picker sections by mapping kind', async ({ page }) => {
  const payload = {
    file_id: 'abcdef0123456789abcdef0123456789',
    file_name: 'mixed.csv',
    external_version_number: '11.0.4',
    total_rows: 5,
    columns: [
      _stage2Column('col_0000', 'diagnosis'),
      _stage2Column('col_0001', 'notes'),
      _stage2Column('col_0002', 'age_value'),
      _stage2Column('col_0003', 'unknown_field'),
      _stage2Column('col_0004', 'empty_col'),
      _stage2Column('col_0005', 'low_match'),
    ],
    cde_targets: {
      col_0000: [{ target: 'dx', similarity: 0.95 }],
      col_0001: [{ target: 'notes_cde', similarity: 0.9 }],
      col_0002: [{ target: 'age_cde', similarity: 0.9 }],
      col_0004: [{ target: 'empty_dx', similarity: 0.8 }],
      col_0005: [{ target: 'low_dx', similarity: 0.8 }],
    },
    column_summaries: {
      col_0000: { value_overlap_ratio: 0.8 },
      col_0001: { value_overlap_ratio: null },
      col_0002: { value_overlap_ratio: null },
      col_0003: { value_overlap_ratio: null },
      col_0004: { value_overlap_ratio: null },
      col_0005: { value_overlap_ratio: 0.0 },
    },
    manual_overrides: {},
    manifest: { column_mappings: {} },
  };
  const cdeCatalog = [
    _stage2Cde('dx', 'pv'),
    _stage2Cde('empty_dx', 'pv'),
    _stage2Cde('low_dx', 'pv'),
    _stage2Cde('notes_cde', 'passthrough'),
    _stage2Cde('age_cde', 'passthrough'),
  ];

  await page.addInitScript((stagePayload) => {
    sessionStorage.setItem('stage2Payload', JSON.stringify(stagePayload));
    sessionStorage.setItem('maxReachedStage', 'mapping');
  }, payload);
  await page.route('**/stage-2?**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'text/html',
      body: _stage2HarnessHtml(cdeCatalog),
    });
  });
  await page.route('**/stage-2/column-detail/**', async (route) => {
    const url = new URL(route.request().url());
    const columnKey = decodeURIComponent(url.pathname.split('/').at(-1) ?? '');
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        column_key: columnKey,
        profile: {
          column_key: columnKey,
          total_rows: 5,
          distinct_values: [
            { value: 'Lung', count: 1 },
            { value: 'Breast', count: 1 },
            { value: 'Glioma', count: 1 },
            { value: 'Other', count: 1 },
            { value: 'Unknown', count: 1 },
          ],
          null_count: 0,
          total_distinct: 5,
          null_pct: 0.0,
          is_all_unique: true,
        },
        match_counts: { dx: 4, empty_dx: 0, low_dx: 0 },
        overlap_by_cde: { dx: 0.8, empty_dx: 0.0, low_dx: 0.0 },
        cde_types: { dx: 'pv', empty_dx: 'pv', low_dx: 'pv', notes_cde: 'passthrough', age_cde: 'passthrough' },
        selected_pvs: ['Breast', 'Glioma', 'Lung', 'Other'],
      }),
    });
  });

  await page.goto('/stage-2?file_id=abcdef0123456789abcdef0123456789&data_model_key=gc&external_version_number=11.0.4');

  await expect(page.locator('#mappingRows .mapping-row')).toHaveCount(6);
  await expect(page.locator('.mapping-row', { hasText: 'diagnosis' }).locator('.mapping-row-fit')).toHaveText('80%');
  await expect(page.locator('.mapping-row', { hasText: 'low_match' }).locator('.mapping-row-fit')).toHaveText('0%');
  await expect(page.locator('.mapping-row', { hasText: 'notes' }).locator('.mapping-row-fit--na')).toHaveText('N/A');
  await expect(page.locator('.mapping-row', { hasText: 'age_value' }).locator('.mapping-row-fit--na')).toHaveText('N/A');

  await page.locator('.mapping-row', { hasText: 'diagnosis' }).click();
  await page.locator('#cdePicker').click();

  await expect(page.locator('.dd-section-label', { hasText: 'Common data elements with permissible values' })).toBeVisible();
  await expect(page.locator('.dd-section-label', { hasText: 'Common data elements with no permissible values' })).toBeVisible();
  const passthroughRow = page.locator('.dd-section--rename-only .dd-opt', { hasText: 'notes_cde' });
  await expect(passthroughRow).toBeVisible();
  await expect(passthroughRow).not.toContainText('5 matches');
  // Single right-edge "Pass-through" cell replaces the prior N/A text + pill pair.
  await expect(passthroughRow).toContainText('Pass-through');
  await expect(passthroughRow.locator('.type-badge--passthrough')).toHaveCount(0);
  await expect(passthroughRow.locator('.count')).toHaveAttribute(
    'data-fast-tooltip',
    'This target common data element has no permissible values to harmonize against. Your data will be left unchanged.',
  );

  await page.locator('.dd-section--rename-only .dd-opt', { hasText: 'notes_cde' }).click();

  // After overriding diagnosis to a pass-through CDE, the fit cell shows that
  // no permissible-value comparison applies.
  await expect(page.locator('.mapping-row', { hasText: 'diagnosis' }).locator('.mapping-row-fit--na')).toHaveText('N/A');
});

test('Stage 2 settings sidebar filters rows by mapping outcome', async ({ page }) => {
  /*
   * Given: Stage 2 with four columns covering each outcome — one PV-mapped,
   *        two pass-through-mapped, and one unmapped.
   * When:  the user changes visibility from the Settings sidebar.
   * Then:  the list narrows by effective mapping outcome, and overrides update
   *        the outcome counts.
   */
  const payload = {
    file_id: 'abcdef0123456789abcdef0123456789',
    file_name: 'mixed.csv',
    external_version_number: '11.0.4',
    total_rows: 5,
    columns: [
      _stage2Column('col_0000', 'dx_col'),
      _stage2Column('col_0001', 'age_col'),
      _stage2Column('col_0002', 'notes_col'),
      _stage2Column('col_0003', 'junk_col'),
    ],
    cde_targets: {
      col_0000: [{ target: 'dx_cde', similarity: 0.95 }],
      col_0001: [{ target: 'age_cde', similarity: 0.9 }],
      col_0002: [{ target: 'notes_cde', similarity: 0.9 }],
      // junk_col deliberately omitted — exercises the No-Mapping cell.
    },
    column_summaries: {
      col_0000: { value_overlap_ratio: 0.8 },
      col_0001: { value_overlap_ratio: null },
      col_0002: { value_overlap_ratio: null },
      col_0003: { value_overlap_ratio: null },
    },
    manual_overrides: {},
    manifest: { column_mappings: {} },
  };
  const cdeCatalog = [
    _stage2Cde('dx_cde', 'pv'),
    _stage2Cde('age_cde', 'passthrough'),
    _stage2Cde('notes_cde', 'passthrough'),
  ];

  await page.addInitScript((stagePayload) => {
    sessionStorage.setItem('stage2Payload', JSON.stringify(stagePayload));
    sessionStorage.setItem('maxReachedStage', 'mapping');
  }, payload);
  await page.route('**/stage-2?**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'text/html',
      body: _stage2HarnessHtml(cdeCatalog),
    });
  });
  await page.route('**/stage-2/column-detail/**', async (route) => {
    const url = new URL(route.request().url());
    const columnKey = decodeURIComponent(url.pathname.split('/').at(-1) ?? '');
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        column_key: columnKey,
        profile: {
          column_key: columnKey,
          total_rows: 5,
          distinct_values: [{ value: 'a', count: 1 }],
          null_count: 0,
          total_distinct: 1,
          null_pct: 0.0,
          is_all_unique: true,
        },
        match_counts: { dx_cde: 0, age_cde: 0 },
        overlap_by_cde: { dx_cde: 0.0, age_cde: 0.0 },
        cde_types: { dx_cde: 'pv', age_cde: 'passthrough', notes_cde: 'passthrough' },
        selected_pvs: [],
      }),
    });
  });

  await page.goto('/stage-2?file_id=abcdef0123456789abcdef0123456789&data_model_key=gc&external_version_number=11.0.4');

  // Negative: takeover starts hidden, all four rows render, ordering matches CSV.
  await expect(page.locator('#takeover')).toHaveClass(/hidden/);
  await expect(page.locator('#mappingRows .mapping-row')).toHaveCount(4);

  // The sidebar starts closed, then shows rewrite/pass-through/unmapped counts.
  await expect(page.locator('#filterSidebar')).toHaveClass(/hidden/);
  await page.locator('#filterSidebarTrigger').click();
  await expect(page.locator('#filterSidebar')).not.toHaveClass(/hidden/);
  await expect(page.locator('.fm-check[data-outcome="rewrite"] .fm-check-count')).toHaveText('1 / 4');
  await expect(page.locator('.fm-check[data-outcome="passthrough"] .fm-check-count')).toHaveText('2 / 4');
  await expect(page.locator('.fm-check[data-outcome="unchanged"] .fm-check-count')).toHaveText('1 / 4');

  // Hiding pass-through rows leaves only the PV-mapped and unmapped rows.
  await page.locator('.fm-check[data-outcome="passthrough"]').click();
  await expect(page.locator('#mappingRows .mapping-row')).toHaveCount(2);
  await expect(page.locator('#mappingRows')).toContainText('dx_col');
  await expect(page.locator('#mappingRows')).toContainText('junk_col');
  await expect(page.locator('#mappingRows')).not.toContainText('age_col');
  await expect(page.locator('#mappingRows')).not.toContainText('notes_col');

  // Resetting restores all four rows in CSV input order.
  await page.locator('.fs-reset').click();
  const rowHeaders = page.locator('#mappingRows .mapping-row .mapping-row-col');
  await expect(rowHeaders).toHaveCount(4);
  await expect(rowHeaders.nth(0)).toContainText('dx_col');
  await expect(rowHeaders.nth(1)).toContainText('age_col');
  await expect(rowHeaders.nth(2)).toContainText('notes_col');
  await expect(rowHeaders.nth(3)).toContainText('junk_col');

  // Override dx_col (the only PV-mapped column) to the pass-through CDE.
  // The outcome counts follow the override through _effectiveCde.
  await page.locator('.mapping-row', { hasText: 'dx_col' }).click();
  await page.locator('#cdePicker').click();
  await page.locator('.dd-section--rename-only .dd-opt', { hasText: 'notes_cde' }).click();
  await page.locator('.takeover-btn--close').click();
  await expect(page.locator('#takeover')).toHaveClass(/hidden/);

  await expect(page.locator('.fm-check[data-outcome="rewrite"] .fm-check-count')).toHaveText('0 / 4');
  await expect(page.locator('.fm-check[data-outcome="passthrough"] .fm-check-count')).toHaveText('3 / 4');
});

test('Stage 2 empty-column filter uses full-column value presence', async ({ page }) => {
  /*
   * Given: one analyzed column has values and one is blank
   * When:  Stage 2 renders with the default empty-column filter
   * Then:  that column remains visible, while the truly blank column is hidden.
   */
  const payload = {
    file_id: 'abcdef0123456789abcdef0123456789',
    file_name: 'late-values.csv',
    external_version_number: '11.0.4',
    total_rows: 6,
    columns: [
      _stage2Column('col_0000', 'late_value', {
        has_non_empty_values: true,
      }),
      _stage2Column('col_0001', 'all_blank', {
        has_non_empty_values: false,
      }),
    ],
    cde_targets: {},
    column_summaries: {
      col_0000: { value_overlap_ratio: null },
      col_0001: { value_overlap_ratio: null },
    },
    manual_overrides: {},
    manifest: { column_mappings: {} },
  };

  await page.addInitScript((stagePayload) => {
    sessionStorage.setItem('stage2Payload', JSON.stringify(stagePayload));
    sessionStorage.setItem('maxReachedStage', 'mapping');
  }, payload);
  await page.route('**/stage-2?**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'text/html',
      body: _stage2HarnessHtml([]),
    });
  });

  await page.goto('/stage-2?file_id=abcdef0123456789abcdef0123456789&data_model_key=gc&external_version_number=11.0.4');

  await expect(page.locator('#mappingRows .mapping-row')).toHaveCount(1);
  await expect(page.locator('#mappingRows')).toContainText('late_value');
  await expect(page.locator('#mappingRows')).not.toContainText('all_blank');

  await page.locator('#filterSidebarTrigger').click();
  await expect(page.locator('.fm-check[data-toggle="showEmpty"] .fm-check-count')).toHaveText('1 / 2');
  await page.locator('.fm-check[data-toggle="showEmpty"]').click();
  await expect(page.locator('#mappingRows .mapping-row')).toHaveCount(2);
});

test('Stage 2 submits selected column renames for harmonization', async ({ page }) => {
  /*
   * Given: Stage 2 has a mapped column whose CDE label differs from the source header.
   * When:  the user enables rename-to-standard and continues to harmonization.
   * Then:  one request durably saves the choices and queues Stage 3, with
   *        column_renames separate from CDE overrides.
   */
  const payload = {
    file_id: 'abcdef0123456789abcdef0123456789',
    file_name: 'rename.csv',
    external_version_number: '11.0.4',
    total_rows: 1,
    columns: [_stage2Column('col_0000', 'diagnosis')],
    cde_targets: {
      col_0000: [{ target: 'primary_diagnosis', similarity: 0.95 }],
    },
    column_summaries: {
      col_0000: { value_overlap_ratio: 1.0 },
    },
    manual_overrides: {},
    manifest: {
      column_mappings: {
        col_0000: { column_name: 'diagnosis', cde_key: 'primary_diagnosis', cde_id: 101 },
      },
    },
  };
  const cdeCatalog = [{
    cde_id: 101,
    cde_key: 'primary_diagnosis',
    label: 'Primary Diagnosis',
    description: 'Diagnosis description',
    cde_type: 'pv',
  }];

  await page.addInitScript((stagePayload) => {
    sessionStorage.setItem('stage2Payload', JSON.stringify(stagePayload));
    sessionStorage.setItem('maxReachedStage', 'mapping');
  }, payload);
  await page.route('**/stage-2?**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'text/html',
      body: _stage2HarnessHtml(cdeCatalog),
    });
  });
  const choicesRequests = [];
  await page.route('**/stage-2/choices', async (route) => {
    const body = route.request().postDataJSON?.() ?? {};
    choicesRequests.push(body);
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ file_id: body.file_id }),
    });
  });
  const harmonizeRequests = [];
  await page.route('**/stage-3/harmonize', async (route) => {
    harmonizeRequests.push(route.request().postDataJSON?.() ?? {});
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        job_id: 'rename-job',
        status: 'succeeded',
        detail: 'Harmonization completed.',
        next_stage_url: '/stage-4',
        job_id_available: true,
        manifest_summary: null,
      }),
    });
  });

  await page.goto('/stage-2?file_id=abcdef0123456789abcdef0123456789&data_model_key=gc&external_version_number=11.0.4');

  // Negative: no rename has been handed off yet.
  const before = await page.evaluate(() => JSON.parse(sessionStorage.getItem('stage2Payload')).column_renames);
  expect(before).toBeUndefined();

  // Enable renaming and continue.
  await page.locator('#filterSidebarTrigger').click();
  await page.locator('.fs-rename-toggle').click();
  await page.locator('#harmonizeButton').click();
  await page.waitForURL(/\/stage-3/);

  expect(choicesRequests).toEqual([{
    file_id: 'abcdef0123456789abcdef0123456789',
    manual_overrides: {},
    column_renames: { col_0000: 'Primary Diagnosis' },
  }]);
  expect(harmonizeRequests).toHaveLength(1);
  expect(harmonizeRequests[0]).toEqual({ file_id: 'abcdef0123456789abcdef0123456789' });
  const durableJobIdentity = await page.evaluate(() => JSON.parse(sessionStorage.getItem('stage3HarmonizeJob')));
  expect(durableJobIdentity).toMatchObject({
    job_id: 'rename-job',
    file_id: 'abcdef0123456789abcdef0123456789',
  });
  expect(new URL(page.url()).searchParams.get('job_id')).toBe('rename-job');
});

test('Stage 2 picker surfaces all AI candidates as separate rows', async ({ page }) => {
  /*
   * Given: cde_targets["diagnosis"] contains two ranked AI candidates.
   * When:  the user opens the picker for that column.
   * Then:  both candidates render as suggestion rows in similarity order
   *        (top first) under the "AI suggestions"
   *        section header, and neither key appears in the lower sections
   *        for CDEs with/without permissible values. Picking a candidate
   *        updates the row's target CDE while preserving the rewrite
   *        outcome for PV CDEs.
   */
  const payload = {
    file_id: 'abcdef0123456789abcdef0123456789',
    file_name: 'multi.csv',
    external_version_number: '11.0.4',
    total_rows: 5,
    columns: [_stage2Column('col_0000', 'diagnosis'), _stage2Column('col_0001', 'notes')],
    cde_targets: {
      col_0000: [
        { target: 'dx', similarity: 0.95 },
        { target: 'dx_alt', similarity: 0.82 },
      ],
      col_0001: [{ target: 'notes_cde', similarity: 0.9 }],
    },
    column_summaries: {
      col_0000: { value_overlap_ratio: 0.8 },
      col_0001: { value_overlap_ratio: null },
    },
    manual_overrides: {},
    manifest: { column_mappings: {} },
  };
  const cdeCatalog = [
    _stage2Cde('dx', 'pv'),
    _stage2Cde('dx_alt', 'pv'),
    _stage2Cde('other_dx', 'pv'),
    _stage2Cde('notes_cde', 'passthrough'),
  ];

  await page.addInitScript((stagePayload) => {
    sessionStorage.setItem('stage2Payload', JSON.stringify(stagePayload));
    sessionStorage.setItem('maxReachedStage', 'mapping');
  }, payload);
  await page.route('**/stage-2?**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'text/html',
      body: _stage2HarnessHtml(cdeCatalog),
    });
  });
  await page.route('**/stage-2/column-detail/**', async (route) => {
    const url = new URL(route.request().url());
    const columnKey = decodeURIComponent(url.pathname.split('/').at(-1) ?? '');
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        column_key: columnKey,
        profile: {
          column_key: columnKey,
          total_rows: 5,
          distinct_values: [
            { value: 'Lung', count: 1 },
            { value: 'Breast', count: 1 },
          ],
          null_count: 0,
          total_distinct: 2,
          null_pct: 0.0,
          is_all_unique: false,
        },
        match_counts: { dx: 2, dx_alt: 1, other_dx: 0 },
        overlap_by_cde: { dx: 1.0, dx_alt: 0.5, other_dx: 0.0 },
        cde_types: { dx: 'pv', dx_alt: 'pv', other_dx: 'pv', notes_cde: 'passthrough' },
        selected_pvs: ['Lung', 'Breast'],
      }),
    });
  });

  await page.goto('/stage-2?file_id=abcdef0123456789abcdef0123456789&data_model_key=gc&external_version_number=11.0.4');

  // Open takeover for the diagnosis column, then open the picker.
  await page.locator('.mapping-row', { hasText: 'diagnosis' }).click();
  await page.locator('#cdePicker').click();

  // The suggestion section shows both candidates in overlap order. The
  // per-row badge is absent because the section header conveys the source.
  await expect(
    page.locator('.dd-section-label', { hasText: 'AI suggestions' })
  ).toBeVisible();
  const suggestionRows = page.locator('#pickerDropdown .dd-opt.ai');
  await expect(suggestionRows).toHaveCount(2);
  await expect(suggestionRows.nth(0)).toContainText('dx');
  await expect(suggestionRows.nth(1)).toContainText('dx_alt');
  await expect(suggestionRows.nth(0).locator('.ai-badge')).toHaveCount(0);

  // Each overlap candidate appears exactly once across the whole dropdown.
  await expect(page.locator('#pickerDropdown .dd-opt[data-value="dx"]')).toHaveCount(1);
  await expect(page.locator('#pickerDropdown .dd-opt[data-value="dx_alt"]')).toHaveCount(1);

  // The default state reflects the top overlap candidate.
  await expect(
    page.locator('.mapping-row', { hasText: 'diagnosis' }).locator('.mapping-row-target')
  ).toContainText('dx');
  await expect(
    page.locator('.mapping-row', { hasText: 'diagnosis' }).locator('.mapping-row-status.mapping-ico--rewrite')
  ).toBeVisible();

  // Picking the second overlap candidate keeps the row as a rewrite outcome.
  await suggestionRows.nth(1).click();
  await expect(
    page.locator('.mapping-row', { hasText: 'diagnosis' }).locator('.mapping-row-target')
  ).toContainText('dx_alt');
  await expect(
    page.locator('.mapping-row', { hasText: 'diagnosis' }).locator('.mapping-row-status.mapping-ico--rewrite')
  ).toBeVisible();
  // The picker badge still marks that the selected CDE came from AI ranking.
  await expect(page.locator('#cdePicker .ai-badge')).toBeVisible();
  await expect(page.locator('#cdePicker .ai-badge')).toHaveText('AI suggestion');

  // Picking another catalog CDE updates the target and still remains a rewrite
  // outcome because the selected CDE is PV-backed.
  await page.locator('#cdePicker').click();
  await page.locator('#pickerDropdown .dd-opt[data-value="other_dx"]').click();
  await expect(
    page.locator('.mapping-row', { hasText: 'diagnosis' }).locator('.mapping-row-target')
  ).toContainText('other_dx');
  await expect(
    page.locator('.mapping-row', { hasText: 'diagnosis' }).locator('.mapping-row-status.mapping-ico--rewrite')
  ).toBeVisible();
});

test('TSV flow preserves TSV format through download', async ({ page }) => {
  await mockHarmonizeSuccess(page);

  // Given: a TSV file is uploaded and analyzed
  const fileId = await uploadAndAnalyze(page, fileFixture('basic.tsv'), 0);

  // When: the user harmonizes and downloads the result
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileId, { 0: { col_0000: 'Baz, still one cell' } });

  // Then: the exported tabular payload is TSV and keeps comma-bearing values intact
  const rows = await downloadTsvRows(page, fileId);
  expect(rows[0].col_a).toBe('Baz, still one cell');
  expect(rows[0].col_b).toBe('value, one');
});

test('XLSX flow selects a worksheet and preserves XLSX format through download', async ({ page }) => {
  await mockHarmonizeSuccess(page);

  // Given: a workbook is uploaded and the second worksheet is selected
  const workbookPath = createWorkbookFixture();
  const fileId = await uploadAndAnalyzeSheet(page, workbookPath, 'Patients');

  // When: the user harmonizes and downloads the result
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileId, { 0: { col_0000: 'Baz, still one cell' } });

  // Then: the selected worksheet is exported as XLSX and comma-bearing values stay in one cell
  const patientRows = await downloadWorkbookRows(page, fileId, 'Patients');
  expect(patientRows[0]).toEqual(['col_a', 'col_b']);
  expect(patientRows[1]).toEqual(['Baz, still one cell', 'value, one']);
  const keptRows = await downloadWorkbookRows(page, fileId, 'Keep');
  expect(keptRows[1]).toEqual(['unchanged']);
});

test('override propagation applies to all instances in a column', async ({ page }) => {
  await mockHarmonizeSuccess(page);

  // Given: a CSV with repeated terms in a column
  const fileId = await uploadAndAnalyze(page, fileFixture('basic.csv'));
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileId, { 0: { col_0001: 'Suggested' }, 1: { col_0001: 'Suggested' } });

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

test('duplicate headers keep positional changes and overrides separate', async ({ page }, testInfo) => {
  await mockHarmonizeSuccess(page);
  const csvPath = testInfo.outputPath('duplicate-headers.csv');
  fs.mkdirSync(path.dirname(csvPath), { recursive: true });
  fs.writeFileSync(
    csvPath,
    'record_id,status,status\nRID-1,first raw,second raw\nRID-2,first other,second other\n',
  );

  // Given: two source columns have the same visible header but different positions.
  const fileId = await uploadAndAnalyze(page, csvPath);
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileId, {
    0: { col_0001: 'first suggested', col_0002: 'second suggested' },
  });

  // When: the reviewer gives each positional column a different final value.
  await page.goto(`/stage-4?file_id=${fileId}`);
  await waitForReviewRows(page);
  const columnPills = page.locator('.batch-progress-item.column-pill');
  await expect(columnPills).toHaveCount(2);
  await expect(columnPills).toContainText(['status', 'status']);

  await columnPills.nth(0).click();
  await expect(page.locator('.target-value-input')).toHaveValue('first suggested');
  const firstSave = page.waitForResponse(
    (response) => response.url().includes('/stage-4/overrides') && response.ok(),
  );
  await page.locator('.target-value-input').fill('first reviewed');
  await firstSave;

  await columnPills.nth(1).click();
  await expect(page.locator('.target-value-input')).toHaveValue('second suggested');
  const secondSave = page.waitForResponse(
    (response) => response.url().includes('/stage-4/overrides') && response.ok(),
  );
  await page.locator('.target-value-input').fill('second reviewed');
  await secondSave;

  // Then: the exported duplicate columns retain their order and distinct values.
  const response = await page.request.post('/stage-5/download', { data: { file_id: fileId } });
  expect(response.ok()).toBeTruthy();
  const table = await parseDownloadedCsvTable(response);
  expect(table.headers).toEqual(['record_id', 'status', 'status']);
  expect(table.rows[0]).toEqual(['RID-1', 'first reviewed', 'second reviewed']);
  expect(table.rows[1]).toEqual(['RID-2', 'first other', 'second other']);
});

test('an override for a repeated value reaches every matching row', async ({ page }, testInfo) => {
  await mockHarmonizeSuccess(page);
  const rowCount = 60;
  const csvPath = testInfo.outputPath('repeated-value.csv');
  fs.mkdirSync(path.dirname(csvPath), { recursive: true });
  const rows = Array.from({ length: rowCount }, (_, index) => `RID-${index + 1},Foo`);
  fs.writeFileSync(csvPath, `record_id,col_a\n${rows.join('\n')}\n`);

  // Given: one source value occurs beyond the old small-row boundary.
  const fileId = await uploadAndAnalyze(page, csvPath);
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  const changes = Object.fromEntries(
    Array.from({ length: rowCount }, (_, index) => [index, { col_0001: 'Suggested' }]),
  );
  seedHarmonization(fileId, changes);

  // When: the reviewer changes the repeated value once.
  await page.goto(`/stage-4?file_id=${fileId}`);
  await waitForReviewRows(page);
  const card = page.locator('.column-mode-grid .row-cell', {
    has: page.locator('.original-context-value', { hasText: 'Foo' }),
  });
  await expect(card.locator('.entry-row-label')).toHaveText('60 rows');
  const save = page.waitForResponse(
    (response) => response.url().includes('/stage-4/overrides') && response.ok(),
  );
  await card.locator('.target-value-input').fill('Reviewed');
  await save;

  // Then: all 60 matching cells change in the exported data.
  const downloadedRows = await downloadCsvRows(page, fileId);
  expect(downloadedRows).toHaveLength(rowCount);
  expect(downloadedRows.every((row) => row.col_a === 'Reviewed')).toBe(true);
});

test('whitespace-significant terms remain distinct', async ({ page }) => {
  await mockHarmonizeSuccess(page);

  // Given: a CSV where whitespace creates distinct terms
  const fileId = await uploadAndAnalyze(page, fileFixture('whitespace.csv'));
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileId, {
    0: { col_0001: 'Suggested' },
    1: { col_0001: 'Suggested' },
    2: { col_0001: 'Suggested' },
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
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileId, { 0: { col_0001: 'Suggested' }, 1: { col_0001: 'Suggested' } });

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

test('no-change flow shows empty review state and zero outcome metrics', async ({ page }) => {
  await mockHarmonizeSuccess(page);

  // Given: a CSV with no harmonization changes
  const fileId = await uploadAndAnalyze(page, fileFixture('no-change.csv'));
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileId, {});

  // When: review is opened
  await page.goto(`/stage-4?file_id=${fileId}`);
  await expect(page.locator('.review-empty')).toBeVisible();

  // Then: the final certificate reports an explicit zero-change aggregate
  await page.goto(`/stage-5?file_id=${fileId}`);
  await expect(page.locator('.quality-certificate')).toBeVisible();
  await expect(page.locator('[data-impact-metric="total_values"]')).toContainText('0');
  await expect(page.locator('[data-impact-metric="unique_values"]')).toContainText('0');
  await expect(page.locator('[data-impact-metric="manual_values"]')).toContainText('0');
});

test('Stage 5 aggregates change impact and keeps filters keyboard accessible', async ({ page }) => {
  const fileId = '0123456789abcdef0123456789abcdef';
  const history = [{
    value: 'Bad',
    source: 'original',
    timestamp: null,
    user_id: null,
    review_status: 'needs_attention',
  }];
  await page.route('**/stage-5/summary', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        dataset: {
          filename: 'attention.csv',
          tabular_format: 'csv',
          data_model_key: 'gc',
          external_version_number: '11.0.4',
        },
        column_summaries: [
          {
            column: 'diagnosis',
            column_key: 'col_0000',
            source_column_index: 0,
            distinct_terms: 3,
            changed_distinct_values: 2,
            total_rows: 3,
            changed_rows: 2,
            reviewer_edited_rows: 1,
            non_conformant_values: 1,
            review_status: 'needs_attention',
            ai_changes: 1,
            manual_changes: 1,
            unchanged: 1,
          },
          {
            column: 'gender',
            column_key: 'col_0001',
            source_column_index: 1,
            distinct_terms: 1,
            changed_distinct_values: 1,
            total_rows: 3,
            changed_rows: 3,
            reviewer_edited_rows: 0,
            non_conformant_values: 0,
            review_status: 'clear',
            ai_changes: 1,
            manual_changes: 0,
            unchanged: 0,
          },
        ],
        term_mappings: [
          {
            column: 'diagnosis', column_key: 'col_0000', source_column_index: 0,
            original_value: 'Bad', final_value: 'Bad', is_changed: false,
            final_value_source: 'source', review_status: 'needs_attention', row_count: 1,
            history,
          },
          {
            column: 'diagnosis', column_key: 'col_0000', source_column_index: 0,
            original_value: 'A', final_value: 'B', is_changed: true,
            final_value_source: 'data_chord', review_status: 'clear', row_count: 1,
            history: [],
          },
          {
            column: 'diagnosis', column_key: 'col_0000', source_column_index: 0,
            original_value: 'C', final_value: 'D', is_changed: true,
            final_value_source: 'reviewer', review_status: 'clear', row_count: 1,
            history: [],
          },
        ],
        non_conformant_count: 1,
      }),
    });
  });

  await page.goto(`/stage-5?file_id=${fileId}`);
  await expect(page.getByRole('heading', { level: 1, name: 'attention.csv' })).toBeVisible();
  await expect(page.locator('#datasetMetadata')).toHaveText('gc 11.0.4 · CSV');
  await expect(page.getByRole('button', { name: 'Download data' })).toBeVisible();
  await expect(page.getByRole('heading', { level: 2, name: 'Changes' })).toBeVisible();
  await expect(page.locator('[data-impact-metric="unique_values"]')).toContainText('3');
  await expect(page.locator('[data-impact-metric="unique_values"]')).toContainText('Unique values changed');
  await expect(page.locator('[data-impact-metric="total_values"]')).toContainText('5');
  await expect(page.locator('[data-impact-metric="total_values"]')).toContainText('Total values changed');
  await expect(page.locator('[data-impact-metric="manual_values"]')).toContainText('1');
  await expect(page.locator('[data-impact-metric="manual_values"]')).toContainText('Values manually changed');
  await expect(page.locator('#mappingTitle')).toHaveText('Value details · Select a row to view history');
  const attention = page.getByRole('button', { name: 'Needs attention' });
  await expect(attention).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#changesTableBody tr.needs-attention')).toContainText('Bad');
  await expect(page.locator('#changesTableBody tr')).toHaveCount(1);

  const changed = page.getByRole('button', { name: 'Changed', exact: true });
  await changed.focus();
  await changed.press('Enter');
  await expect(changed).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#changesTableBody tr[data-history-row]')).toHaveCount(2);

  const reviewer = page.getByRole('button', { name: 'Reviewer edits' });
  await reviewer.press('Enter');
  await expect(reviewer).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#changesTableBody tr')).toContainText('C');

  await page.getByRole('button', { name: 'All', exact: true }).press('Enter');
  await expect(page.locator('#changesTableBody tr[data-history-row]')).toHaveCount(3);

  const sortButton = page.getByRole('button', { name: /Source value/ });
  await sortButton.press('Enter');
  await expect(sortButton.locator('xpath=ancestor::th')).toHaveAttribute('aria-sort', 'ascending');
});

test('autosave persists overrides across reloads', async ({ page }) => {
  await mockHarmonizeSuccess(page);

  // Given: a CSV with changes and review loaded
  const fileId = await uploadAndAnalyze(page, fileFixture('basic.csv'));
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileId, { 0: { col_0001: 'Suggested' } });

  await page.goto(`/stage-4?file_id=${fileId}`);
  await waitForReviewRows(page);
  const card = page.locator('.column-mode-grid .row-cell', {
    has: page.locator('.original-context-value', { hasText: 'Foo' }),
  }).first();
  const initialSavePromise = page.waitForResponse(
    (response) => response.request().method() === 'POST'
      && response.url().includes('/stage-4/overrides')
      && response.ok(),
  );
  await card.locator('.target-value-input').fill('Persisted');
  const initialSave = await initialSavePromise;
  expect(initialSave.headers().etag).toBeTruthy();

  // When: the page is reloaded
  const loadPromise = page.waitForResponse(
    (response) => response.request().method() === 'GET'
      && response.url().includes(`/stage-4/overrides/${fileId}`)
      && response.ok(),
  );
  await page.reload();
  const loaded = await loadPromise;
  await waitForReviewRows(page);

  // Then: the override is restored and the next autosave protects that loaded version
  await expect(card.locator('.target-value-input')).toHaveValue('Persisted');
  const version = loaded.headers().etag;
  expect(version).toBeTruthy();
  const versionedSavePromise = page.waitForRequest(
    (request) => request.method() === 'POST' && request.url().includes('/stage-4/overrides'),
  );
  const versionedSaveResponsePromise = page.waitForResponse(
    (response) => response.request().method() === 'POST'
      && response.url().includes('/stage-4/overrides')
      && response.ok(),
  );
  await card.locator('.target-value-input').fill('Persisted again');
  const versionedSave = await versionedSavePromise;
  expect(versionedSave.headers()['if-match']).toBe(version);
  await versionedSaveResponsePromise;
});

test('stage 5 advance waits for the latest override save', async ({ page }) => {
  await mockHarmonizeSuccess(page);

  // Given: a CSV with one harmonized term open for review
  const fileId = await uploadAndAnalyze(page, fileFixture('basic.csv'));
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileId, { 0: { col_0001: 'Suggested' } });

  await page.goto(`/stage-4?file_id=${fileId}`);
  await waitForReviewRows(page);

  const heldOverrideSaves = [];
  let nonConformanceChecks = 0;

  await page.route('**/stage-4/overrides', async (route) => {
    if (route.request().method() === 'POST') {
      heldOverrideSaves.push(route);
      return;
    }
    await route.continue();
  });
  await page.route('**/stage-4/non-conformant/**', async (route) => {
    nonConformanceChecks += 1;
    await route.continue();
  });

  const card = page.locator('.column-mode-grid .row-cell', {
    has: page.locator('.original-context-value', { hasText: 'Foo' }),
  }).first();
  const input = card.locator('.target-value-input');

  // When: the user changes a value, changes it again, then immediately advances
  await input.fill('Female');
  await expect.poll(() => heldOverrideSaves.length).toBe(1);
  await input.fill('Unknown');
  await page.click('#stageFiveButton');

  // Then: Stage 5 does not check PV conformance before the latest save is written
  await page.waitForTimeout(100);
  expect(nonConformanceChecks).toBe(0);

  await heldOverrideSaves[0].continue();
  await expect.poll(() => heldOverrideSaves.length).toBe(2);
  const latestPayload = heldOverrideSaves[1].request().postDataJSON();
  const latestHumanValues = Object.values(latestPayload.overrides)
    .flatMap((columns) => Object.values(columns).map((override) => override.human_value));
  expect(latestHumanValues).toContain('Unknown');
  expect(latestHumanValues).not.toContain('Female');
  expect(nonConformanceChecks).toBe(0);

  await heldOverrideSaves[1].continue();
  await expect.poll(() => nonConformanceChecks).toBe(1);
});

test('version menu lets the user choose from a long version list', async ({ page }) => {
  /*
   * Given: 20 versions exist for the default data model, exceeding the panel max-height
   * When:  the user opens the data-model popup and clicks the version trigger
   * Then:  all versions remain available, the latest starts selected, and an
   *        older version can be selected.
   */
  await mockDataModelsWithVersionCount(page, 20);
  await mockAnalyze(page);

  await page.goto('/stage-1');
  await page.setInputFiles(AGENT_FILE_INPUT, fileFixture('basic.csv'));
  await page.locator('#analyzeButton').waitFor({ state: 'visible' });
  await page.waitForFunction(() => !document.querySelector('#analyzeButton')?.disabled);
  await page.click('#analyzeButton');

  await page.locator('.data-model-dialog').waitFor({ state: 'visible' });

  // Negative check: panel hidden before trigger click
  await expect(page.locator('.data-model-dropdown--version .data-model-dropdown-panel')).toBeHidden();

  await page.click('#versionDropdownTrigger');
  await page.locator('.data-model-dropdown--version .data-model-dropdown-panel').waitFor({ state: 'visible' });

  const items = page.locator('.data-model-dropdown--version .data-model-dropdown-item');
  await expect(items).toHaveCount(20);
  await expect(items.first()).toHaveText('11.0.20');
  await expect(items.first()).toHaveAttribute('aria-selected', 'true');
  await page.getByRole('option', { name: '11.0.1', exact: true }).click();
  await expect(page.locator('#versionDropdownTrigger')).toContainText('11.0.1');
});

test('data model menus select models by pointer and keyboard and close on outside click', async ({ page }) => {
  /*
   * Given: the Stage 1 data model popup has two data models and multiple versions
   * When:  the user opens the data model dropdown and the version dropdown
   * Then:  visible selections stay in sync and clicking elsewhere dismisses
   *        the open menu.
   */
  await page.route('**/stage-1/data-models', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          data_model_key: 'alpha',
          label: 'Alpha Model',
          versions: [
            { external_version_number: '11.0.1' },
            { external_version_number: '11.0.3' },
          ],
        },
        {
          data_model_key: 'gc',
          label: 'Genomic Cancer',
          versions: [
            { external_version_number: '11.0.2' },
          ],
        },
      ]),
    });
  });
  await mockAnalyze(page);

  await page.goto('/stage-1');
  await page.setInputFiles(AGENT_FILE_INPUT, fileFixture('basic.csv'));
  await page.locator('#analyzeButton').waitFor({ state: 'visible' });
  await page.waitForFunction(() => !document.querySelector('#analyzeButton')?.disabled);
  await page.click('#analyzeButton');
  await page.locator('.data-model-dialog').waitFor({ state: 'visible' });

  await expect(page.locator('#dataModelDropdownTrigger')).toContainText('Alpha Model');
  await expect(page.locator('#versionDropdownTrigger')).toContainText('11.0.3');
  await expect(page.locator('.data-model-dropdown--model .data-model-dropdown-panel')).toBeHidden();

  // When: the data model dropdown opens.
  await page.click('#dataModelDropdownTrigger');
  await expect(page.locator('.data-model-dropdown--model .data-model-dropdown-panel')).toBeVisible();
  await expect(page.locator('.data-model-dropdown--model .data-model-dropdown-item')).toHaveCount(2);

  // When: the user clicks elsewhere in the dialog.
  await page.locator('.data-model-dialog-title').click();

  // Then: the open custom dropdown closes.
  await expect(page.locator('.data-model-dropdown--model .data-model-dropdown-panel')).toBeHidden();

  // When: selecting a model through the visible dropdown.
  await page.click('#dataModelDropdownTrigger');
  await page.locator('.data-model-dropdown--model .data-model-dropdown-item[data-value="gc"]').click();

  // Then: the dependent version selection updates.
  await expect(page.locator('#dataModelDropdownTrigger')).toContainText('Genomic Cancer');
  await expect(page.locator('#versionDropdownTrigger')).toContainText('11.0.2');

  // When: a keyboard user changes the custom model dropdown.
  await page.click('#dataModelDropdownTrigger');
  await page.keyboard.press('ArrowUp');
  await page.keyboard.press('Enter');

  // Then: the visible selection and its version update together.
  await expect(page.locator('#dataModelDropdownTrigger')).toContainText('Alpha Model');
  await expect(page.locator('#versionDropdownTrigger')).toContainText('11.0.3');

  // When/Then: the version dropdown also closes when the user clicks off it.
  await page.click('#versionDropdownTrigger');
  await expect(page.locator('.data-model-dropdown--version .data-model-dropdown-panel')).toBeVisible();
  await page.locator('.data-model-dialog-title').click();
  await expect(page.locator('.data-model-dropdown--version .data-model-dropdown-panel')).toBeHidden();
});

test('error handling: wrong file type and oversize upload', async ({ page }, testInfo) => {
  await mockDataModels(page);

  // Given: a non-CSV file is uploaded
  await page.goto('/stage-1');
  await expect(page.locator('#statusMessage')).toBeEmpty();
  await page.setInputFiles(AGENT_FILE_INPUT, fileFixture('not-csv.json'));

  // Then: upload error is shown
  await expect(page.locator('#statusMessage')).toContainText(/Only CSV|Unsupported|Upload failed/i);

  // Given: a CSV exceeds the upload limit.
  const oversizedPath = testInfo.outputPath('oversized.csv');
  fs.mkdirSync(path.dirname(oversizedPath), { recursive: true });
  const largeContent = 'col_a\n' + 'x'.repeat(26 * 1024 * 1024);
  fs.writeFileSync(oversizedPath, largeContent);

  // When: the oversized file is uploaded.
  await page.setInputFiles(AGENT_FILE_INPUT, oversizedPath);

  // Then: the user sees the size error.
  await expect(page.locator('#statusMessage')).toContainText(/exceeds|too large|Upload failed/i);
});

test('Stage 1 shows generic analyze errors for structured API failures', async ({ page }) => {
  await mockDataModels(page);
  await page.route('**/stage-1/analyze', async (route) => {
    await route.fulfill({
      status: 422,
      contentType: 'application/json',
      headers: { 'X-Request-ID': 'server-request-123' },
      body: JSON.stringify({
        detail: [
          {
            loc: ['body', 'external_version_number'],
            msg: 'Field required',
            type: 'missing',
          },
        ],
      }),
    });
  });

  // Given: a file has uploaded successfully and the user starts mapping
  await page.goto('/stage-1');
  await page.setInputFiles('#fileInput', fileFixture('basic.csv'));
  await page.locator('#analyzeButton').waitFor({ state: 'visible' });
  await page.waitForFunction(() => !document.querySelector('#analyzeButton')?.disabled);
  await page.click('#analyzeButton');
  await page.locator('.data-model-confirm-btn').click();

  // Then: the user gets a stable message without backend validation details
  await expect(page.locator('#statusMessage')).toContainText(
    "We couldn't start mapping for this file. Please refresh the page and try again.",
  );
  await expect(page.locator('#statusMessage')).not.toContainText('[object Object]');
  await expect(page.locator('#statusMessage')).not.toContainText('external_version_number');
  await expect(page.locator('#statusMessage')).not.toContainText('Field required');
});

test('Stage 1 shows upload progress and keeps the Map button disabled until upload completes', async ({ page }) => {
  await mockDataModels(page);
  let releaseUpload;
  const uploadCanFinish = new Promise((resolve) => {
    releaseUpload = resolve;
  });
  let markUploadStarted;
  const uploadStarted = new Promise((resolve) => {
    markUploadStarted = resolve;
  });
  await page.route('**/stage-1/upload', async (route) => {
    markUploadStarted();
    await uploadCanFinish;
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        file_id: 'abcdef0123456789abcdef0123456789',
        file_name: 'basic.csv',
        human_size: '24 B',
        content_type: 'text/csv',
        uploaded_at: '2026-05-20T18:00:00Z',
        tabular_format: 'csv',
        sheet_names: [],
        selected_sheet: null,
        sheet_previews: {},
      }),
    });
  });

  // Given: the upload page has no file yet
  await page.goto('/stage-1');
  await expect(page.locator('#analyzeButton')).toBeVisible();
  await expect(page.locator('#analyzeButton')).toBeDisabled();
  await expect(page.locator('#dropzoneUploading')).toBeHidden();

  // When: the user selects a file and upload is still in flight
  await page.setInputFiles(AGENT_FILE_INPUT, fileFixture('basic.csv'));
  await uploadStarted;

  // Then: the blocking upload indicator is visible and the action remains disabled
  await expect(page.locator('#dropzoneUploading')).toBeVisible();
  await expect(page.locator('#dropzoneUploading')).toContainText('Please wait while your file is uploaded');
  await expect(page.locator('#analyzeButton')).toBeDisabled();
  await expect(page.locator('#uploadingFileName')).toHaveText('basic.csv');

  // When: upload completes
  releaseUpload();

  // Then: the normal uploaded state returns and the action is enabled
  await expect(page.locator('#dropzoneFileStatus')).toHaveText('Uploaded');
  await expect(page.locator('#dropzoneUploading')).toBeHidden();
  await expect(page.locator('#analyzeButton')).toBeEnabled();
});

test('harmonization failure keeps mapping choices available for retry', async ({ page }) => {
  await mockHarmonizeFailure(page);

  // Given: a CSV is uploaded and analyzed
  const fileId = await uploadAndAnalyze(page, fileFixture('basic.csv'));

  // When: harmonize fails
  await page.locator('#harmonizeButton').click();

  // Then: Stage 2 keeps the user with their choices and shows a retryable error.
  await expect(page).toHaveURL(/\/stage-2/);
  await expect(page.locator('#harmonizeError')).toBeVisible();
  await expect(page.locator('#harmonizeError')).toContainText('Unable to start harmonization');
  await expect(page.locator('#harmonizeButton')).toBeEnabled();
});

test('multi-file isolation: overrides on one file do not affect another', async ({ page, context }) => {
  await mockHarmonizeSuccess(page);

  // Given: two files uploaded and harmonized
  const fileA = await uploadAndAnalyze(page, fileFixture('basic.csv'));
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileA, { 0: { col_0001: 'Suggested' } });

  const pageB = await context.newPage();
  await mockHarmonizeSuccess(pageB);
  const fileB = await uploadAndAnalyze(pageB, fileFixture('basic.csv'));
  await clickHarmonize(pageB);
  await expect(pageB.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileB, { 0: { col_0001: 'Suggested' } });

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

test('Stage 2 locks and Stage 5 is reachable after Stage 3 completes', async ({ page }) => {
  let harmonizeRequests = 0;
  await page.route('**/stage-3/harmonize', async (route) => {
    harmonizeRequests += 1;
    const payload = route.request().postDataJSON?.() ?? {};
    const fileId = payload.file_id ?? '';
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        job_id: 'e2e-job-locked',
        status: 'succeeded',
        detail: 'Harmonization completed.',
        next_stage_url: `/stage-4?file_id=${fileId}&job_id=e2e-job-locked&status=succeeded`,
        job_id_available: true,
        manifest_summary: null,
      }),
    });
  });

  // Given: Stage 3 has completed, but the user has not clicked Verify yet
  const fileId = await uploadAndAnalyze(page, fileFixture('basic.csv'));
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  expect(harmonizeRequests).toBe(1);

  seedHarmonization(fileId, {});

  // Then: Stage 4 and Stage 5 are both reachable
  await expect(page.locator('.progress-track .step[data-stage="verify"]')).not.toHaveClass(/unreachable/);
  await expect(page.locator('.progress-track .step[data-stage="review"]')).not.toHaveClass(/unreachable/);

  // And: the user can skip straight to the final review summary
  await page.click('.progress-track .step[data-stage="review"]');
  await page.waitForURL(/\/stage-5/);
  const stageFiveUrl = new URL(page.url());
  expect(stageFiveUrl.searchParams.get('file_id')).toBe(fileId);
  await page.locator('#summaryGrid').waitFor({ state: 'visible' });

  // And: Stage 5 can load from the durable workflow id in the URL alone
  await page.reload();
  await expect(page.locator('#summaryGrid')).not.toContainText('Unable to locate harmonization context.');

  // When: the user goes back to Stage 2
  await page.click('.progress-track .step[data-stage="mapping"]');
  await page.waitForURL(/\/stage-2/);

  // Then: mapping is inspection-only for the completed harmonization
  const stageTwoUrl = new URL(page.url());
  expect(stageTwoUrl.searchParams.get('file_id')).toBe(fileId);
  await expect(page.locator('#mappingLockBanner')).toBeVisible();
  await expect(page.locator('#harmonizeButton')).toContainText('Verify');

  await page.locator('.mapping-row', { hasText: 'col_a' }).click();
  await expect(page.locator('#cdePicker')).toBeDisabled();
  await page.evaluate(() => document.querySelector('#cdePicker')?.click());
  await expect(page.locator('#pickerDropdown')).toHaveCount(0);
  await page.locator('.takeover-btn--close').click();

  // And: continuing returns to verification without rerunning harmonization
  await page.locator('#harmonizeButton').click();
  await page.waitForURL(/\/stage-4/);
  expect(harmonizeRequests).toBe(1);
});

test('Stage 3 resumes a durable job and retries with only the workflow id', async ({ page }) => {
  const currentFileId = '22222222abcdef0022222222abcdef00';
  const durableJobId = 'durable-job';
  let harmonizePayload = null;
  let pollRequests = 0;

  await page.route(`**/stage-3/jobs/${durableJobId}**`, async (route) => {
    pollRequests += 1;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        job_id: durableJobId,
        status: 'failed',
        detail: 'Harmonization failed. Please retry.',
        next_stage_url: '/stage-4',
        job_id_available: false,
        manifest_summary: null,
      }),
    });
  });
  await page.route('**/stage-3/harmonize', async (route) => {
    harmonizePayload = route.request().postDataJSON?.() ?? {};
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        job_id: 'retry-job',
        status: 'succeeded',
        detail: 'Harmonization completed.',
        next_stage_url: `/stage-4?file_id=${currentFileId}&job_id=retry-job&status=succeeded`,
        job_id_available: true,
        manifest_summary: null,
      }),
    });
  });

  // Given: only durable workflow and job identity are present in the URL.
  await page.goto(`/stage-3?file_id=${currentFileId}&job_id=${durableJobId}`);

  // Then: Stage 3 polls that durable job without launching another run.
  await expect(page.locator('#stageThreeError')).toBeVisible();
  expect(pollRequests).toBe(1);
  expect(harmonizePayload).toBeNull();

  // When: the user retries the failed durable job.
  await page.locator('#retryButton').click();

  // Then: the retry needs only the workflow id; model and mapping choices stay server-owned.
  await expect(page.locator('#reviewButton')).toBeEnabled();
  expect(harmonizePayload).toEqual({ file_id: currentFileId });
});

test('column tabs keep each source column and transformation separate', async ({ page }) => {
  await mockHarmonizeSuccess(page);

  // Given: a CSV with changes in multiple columns
  const fileId = await uploadAndAnalyze(page, fileFixture('multi-column.csv'));
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileId, {
    0: { col_0001: 'UniqueA', col_0002: 'UniqueB' },
  });

  await page.goto(`/stage-4?file_id=${fileId}`);
  await waitForReviewRows(page);

  const columnPills = page.locator('.batch-progress-item.column-pill');
  await expect(columnPills).toHaveCount(2);

  // When: the reviewer selects the col_a tab.
  await columnPills.filter({ hasText: 'col_a' }).click();
  await waitForReviewRows(page);

  // Then: the values shown belong to col_a.
  await expect(page.locator('.original-context-value')).toContainText(['Foo']);
  await expect(page.locator('.target-value-input')).toHaveValue('UniqueA');

  // When: the reviewer selects the col_b tab.
  await columnPills.filter({ hasText: 'col_b' }).click();
  await waitForReviewRows(page);

  // Then: col_b has its own source value and transformation.
  await expect(page.locator('.original-context-value')).toContainText(['Apple']);
  await expect(page.locator('.target-value-input')).toHaveValue('UniqueB');
});

test('row mode shows all changed cells for each row', async ({ page }) => {
  await mockHarmonizeSuccess(page);

  // Given: a CSV with changes in multiple columns for the same row
  const fileId = await uploadAndAnalyze(page, fileFixture('multi-column.csv'));
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  // Row 0 has changes in both col_a and col_b
  seedHarmonization(fileId, {
    0: { col_0001: 'Changed_A', col_0002: 'Changed_B' },
  });

  await page.goto(`/stage-4?file_id=${fileId}`);
  await waitForReviewRows(page);

  // When: switching to row mode
  await page.click('#settingsButton');
  await page.selectOption('#reviewModeSelect', 'row');
  await page.click('#settingsCloseButton');
  await waitForReviewRows(page);

  // Then: the row shows exactly the two changed cells and their outputs.
  const firstRow = page.locator('.row-mode-row').first();
  await expect(firstRow).toBeVisible();
  await expect(firstRow.locator('.row-cell')).toHaveCount(2);
  await expect(firstRow).toContainText('col_a');
  await expect(firstRow).toContainText('col_b');
  const outputs = firstRow.locator('.target-value-input');
  await expect(outputs.nth(0)).toHaveValue('Changed_A');
  await expect(outputs.nth(1)).toHaveValue('Changed_B');
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

test('history dialog separates current output from accessible decision history', async ({ page }) => {
  await mockHarmonizeSuccess(page);

  // Given: a file with harmonization changes
  const fileId = await uploadAndAnalyze(page, fileFixture('basic.csv'));
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileId, { 0: { col_0001: 'Changed Value' } });

  // Navigate to Stage 5
  await page.goto(`/stage-5?file_id=${fileId}`);
  await expect(page.locator('.quality-certificate')).toBeVisible();
  const historyRow = page.locator('#changesTableBody tr[data-history-row]').first();
  await expect(historyRow).toBeVisible();
  await expect(page.getByRole('button', { name: 'View history' })).toHaveCount(0);

  // When: the keyboard reviewer opens history from the value row itself
  await historyRow.focus();
  await historyRow.press('Enter');
  const dialog = page.getByRole('dialog', { name: 'Current Output and Decision History' });
  await expect(dialog).toBeVisible();

  // Then: current output is separate from the historical event list
  await expect(dialog.locator('.history-current')).toContainText('Current output');
  await expect(dialog.locator('.history-timeline')).toBeVisible();

  const originalStep = dialog.locator('.history-step[data-source="original"]');
  await expect(originalStep).toContainText('Source value');
  const aiStep = dialog.locator('.history-step[data-source="ai"]');
  await expect(aiStep).toContainText('Data Chord');

  // Closing returns focus to the row that opened the dialog.
  await dialog.getByRole('button', { name: 'Close' }).click();
  await expect(dialog).toBeHidden();
  await expect(historyRow).toBeFocused();
});
