import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import {
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
  source_index: 0,
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
    </main>
    <div class="takeover hidden" id="takeover">
      <div class="takeover-backdrop" data-action="close-takeover"></div>
      <div class="takeover-card" id="takeoverCard"></div>
    </div>
    <script>
      window.stageTwoConfig = {
        analyzeEndpoint: "/stage-1/analyze",
        columnDetailBase: "/stage-2/column-detail",
        targetSchema: "gc",
        targetExternalVersionNumber: "11.0.4",
        cdeCatalog: ${JSON.stringify(cdeCatalog)},
        noMappingLabel: "No Mapping",
        stageThreeUrl: "/stage-3"
      };
    </script>
    <script type="module" src="/assets/stage-2/stage_2_mappings.js"></script>
  </body>
</html>
`;

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

  // And: a real UI download reveals the Stage 5 start-over action.
  await expect(page.locator('#uploadNavAction')).toHaveClass(/hidden/);
  const downloadPromise = page.waitForEvent('download');
  await page.click('#downloadResults');
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.zip$/);
  await expect(page.locator('#uploadNavAction')).not.toHaveClass(/hidden/);
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
    stage3HarmonizePayload: sessionStorage.getItem('stage3HarmonizePayload'),
    stage3HarmonizeJob: sessionStorage.getItem('stage3HarmonizeJob'),
    maxReachedStage: sessionStorage.getItem('maxReachedStage'),
    unrelatedKey: sessionStorage.getItem('unrelatedKey'),
  }));
  expect(keysBeforeStartOver.currentFileSession).not.toBeNull();
  expect(keysBeforeStartOver.stage2Payload).not.toBeNull();
  expect(keysBeforeStartOver.stage3HarmonizePayload).not.toBeNull();
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
    stage3HarmonizePayload: sessionStorage.getItem('stage3HarmonizePayload'),
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
    stage3HarmonizePayload: sessionStorage.getItem('stage3HarmonizePayload'),
    stage3HarmonizeJob: sessionStorage.getItem('stage3HarmonizeJob'),
    maxReachedStage: sessionStorage.getItem('maxReachedStage'),
    unrelatedKey: sessionStorage.getItem('unrelatedKey'),
  }));
  expect(keysAfterConfirm).toEqual({
    currentFileSession: null,
    stage2Payload: null,
    stage3HarmonizePayload: null,
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
    total_rows: 5,
    columns: [
      _stage2Column('diagnosis'),
      _stage2Column('notes'),
      _stage2Column('age_value'),
      _stage2Column('unknown_field'),
      _stage2Column('empty_col'),
      _stage2Column('low_match'),
    ],
    cde_targets: {
      diagnosis: [{ target: 'dx', similarity: 0.95 }],
      notes: [{ target: 'notes_cde', similarity: 0.9 }],
      age_value: [{ target: 'age_cde', similarity: 0.9 }],
      empty_col: [{ target: 'empty_dx', similarity: 0.8 }],
      low_match: [{ target: 'low_dx', similarity: 0.8 }],
    },
    column_summaries: {
      diagnosis: { value_overlap_ratio: 0.8 },
      notes: { value_overlap_ratio: null },
      age_value: { value_overlap_ratio: null },
      unknown_field: { value_overlap_ratio: null },
      empty_col: { value_overlap_ratio: null },
      low_match: { value_overlap_ratio: 0.0 },
    },
    next_stage: 'mapping',
    next_step_hint: 'Review mappings.',
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

  await page.goto('/stage-2?file_id=abcdef0123456789abcdef0123456789&schema=gc&external_version_number=11.0.4');

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
    total_rows: 5,
    columns: [
      _stage2Column('dx_col'),
      _stage2Column('age_col'),
      _stage2Column('notes_col'),
      _stage2Column('junk_col'),
    ],
    cde_targets: {
      dx_col: [{ target: 'dx_cde', similarity: 0.95 }],
      age_col: [{ target: 'age_cde', similarity: 0.9 }],
      notes_col: [{ target: 'notes_cde', similarity: 0.9 }],
      // junk_col deliberately omitted — exercises the No-Mapping cell.
    },
    column_summaries: {
      dx_col: { value_overlap_ratio: 0.8 },
      age_col: { value_overlap_ratio: null },
      notes_col: { value_overlap_ratio: null },
      junk_col: { value_overlap_ratio: null },
    },
    next_stage: 'mapping',
    next_step_hint: 'Review mappings.',
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

  await page.goto('/stage-2?file_id=abcdef0123456789abcdef0123456789&schema=gc&external_version_number=11.0.4');

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
    total_rows: 6,
    columns: [
      _stage2Column('late_value', 'late_value', {
        has_non_empty_values: true,
      }),
      _stage2Column('all_blank', 'all_blank', {
        has_non_empty_values: false,
      }),
    ],
    cde_targets: {},
    column_summaries: {
      late_value: { value_overlap_ratio: null },
      all_blank: { value_overlap_ratio: null },
    },
    next_stage: 'mapping',
    next_step_hint: 'Review mappings.',
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

  await page.goto('/stage-2?file_id=abcdef0123456789abcdef0123456789&schema=gc&external_version_number=11.0.4');

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
   * Then:  the Stage 3 handoff includes column_renames separately from CDE overrides.
   */
  const payload = {
    file_id: 'abcdef0123456789abcdef0123456789',
    file_name: 'rename.csv',
    total_rows: 1,
    columns: [_stage2Column('col_0000', 'diagnosis')],
    cde_targets: {
      col_0000: [{ target: 'primary_diagnosis', similarity: 0.95 }],
    },
    column_summaries: {
      col_0000: { value_overlap_ratio: 1.0 },
    },
    next_stage: 'mapping',
    next_step_hint: 'Review mappings.',
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

  await page.goto('/stage-2?file_id=abcdef0123456789abcdef0123456789&schema=gc&external_version_number=11.0.4');

  // Negative: no rename has been handed off yet.
  const before = await page.evaluate(() => JSON.parse(sessionStorage.getItem('stage2Payload')).column_renames);
  expect(before).toBeUndefined();

  // Enable renaming and continue.
  await page.locator('#filterSidebarTrigger').click();
  await page.locator('.fs-rename-toggle').click();
  await page.locator('#harmonizeButton').click();
  await page.waitForURL(/\/stage-3/);

  const handoff = await page.evaluate(() => JSON.parse(sessionStorage.getItem('stage3HarmonizePayload')));
  expect(handoff.request.manual_overrides).toEqual({});
  expect(handoff.request.column_renames).toEqual({ col_0000: 'Primary Diagnosis' });
  expect(handoff.request).not.toHaveProperty('manifest');
  expect(handoff).not.toHaveProperty('manifest');
});

test('Stage 2 picker surfaces all AI candidates as separate rows', async ({ page }) => {
  /*
   * Given: cde_targets["diagnosis"] returns two ranked AI candidates.
   * When:  the user opens the picker for that column.
   * Then:  both candidates render as .dd-opt.ai rows in similarity order
   *        (top first) under the "AI recommended common data elements"
   *        section header, and neither key appears in the lower sections
   *        for CDEs with/without permissible values. Picking a candidate
   *        updates the row's target CDE while preserving the rewrite
   *        outcome for PV CDEs.
   */
  const payload = {
    file_id: 'abcdef0123456789abcdef0123456789',
    file_name: 'multi.csv',
    total_rows: 5,
    columns: [_stage2Column('diagnosis'), _stage2Column('notes')],
    cde_targets: {
      diagnosis: [
        { target: 'dx', similarity: 0.95 },
        { target: 'dx_alt', similarity: 0.82 },
      ],
      notes: [{ target: 'notes_cde', similarity: 0.9 }],
    },
    column_summaries: {
      diagnosis: { value_overlap_ratio: 0.8 },
      notes: { value_overlap_ratio: null },
    },
    next_stage: 'mapping',
    next_step_hint: 'Review mappings.',
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

  await page.goto('/stage-2?file_id=abcdef0123456789abcdef0123456789&schema=gc&external_version_number=11.0.4');

  // Open takeover for the diagnosis column, then open the picker.
  await page.locator('.mapping-row', { hasText: 'diagnosis' }).click();
  await page.locator('#cdePicker').click();

  // AI section: both candidates render as .dd-opt.ai rows under the AI
  // section header, top-first (similarity order). The per-row ✦ AI rec
  // badge is intentionally absent — the section header conveys it.
  await expect(
    page.locator('.dd-section-label', { hasText: 'AI recommended common data elements' })
  ).toBeVisible();
  const aiRows = page.locator('#pickerDropdown .dd-opt.ai');
  await expect(aiRows).toHaveCount(2);
  await expect(aiRows.nth(0)).toContainText('dx');
  await expect(aiRows.nth(1)).toContainText('dx_alt');
  await expect(aiRows.nth(0).locator('.ai-badge')).toHaveCount(0);

  // Dedup: each AI candidate appears exactly once across the whole dropdown.
  await expect(page.locator('#pickerDropdown .dd-opt[data-value="dx"]')).toHaveCount(1);
  await expect(page.locator('#pickerDropdown .dd-opt[data-value="dx_alt"]')).toHaveCount(1);

  // Default state: row reflects the top AI candidate.
  await expect(
    page.locator('.mapping-row', { hasText: 'diagnosis' }).locator('.mapping-row-target')
  ).toContainText('dx');
  await expect(
    page.locator('.mapping-row', { hasText: 'diagnosis' }).locator('.mapping-row-status.mapping-ico--rewrite')
  ).toBeVisible();

  // Picking the 2nd-ranked AI candidate updates the displayed target while the
  // row remains a rewrite outcome.
  await aiRows.nth(1).click();
  await expect(
    page.locator('.mapping-row', { hasText: 'diagnosis' }).locator('.mapping-row-target')
  ).toContainText('dx_alt');
  await expect(
    page.locator('.mapping-row', { hasText: 'diagnosis' }).locator('.mapping-row-status.mapping-ico--rewrite')
  ).toBeVisible();
  // The ✦ AI rec badge on the picker button marks "the selected CDE is an AI
  // recommendation", not "the user accepted the top default" — so picking the
  // 2nd-ranked AI candidate must keep the badge visible on the button.
  await expect(page.locator('#cdePicker .ai-badge')).toBeVisible();

  // Picking a non-AI catalog CDE updates the target and still remains a rewrite
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
  const fileId = await uploadAndAnalyze(page, fileFixture('basic.tsv'));
  const preOverrides = await page.request.get(`/stage-4/overrides/${fileId}`);
  expect(await preOverrides.json()).toBeNull();

  // When: the user harmonizes and downloads the result
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileId, { 0: { col_a: 'Baz, still one cell' } });

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
  const preOverrides = await page.request.get(`/stage-4/overrides/${fileId}`);
  expect(await preOverrides.json()).toBeNull();

  // When: the user harmonizes and downloads the result
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  seedHarmonization(fileId, { 0: { col_a: 'Baz, still one cell' } });

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

test('version dropdown panel renders below trigger and scrolls when versions overflow', async ({ page }) => {
  /*
   * Given: 20 versions exist for the default data model, exceeding the panel max-height
   * When:  the user opens the data-model popup and clicks the version trigger
   * Then:  the panel renders below the trigger (no overlap), shows all 20 items,
   *        is scrollable (scrollHeight > clientHeight), and the latest version
   *        sits at index 0 selected
   */
  await mockDataModelsWithVersionCount(page, 20);
  await mockAnalyze(page);

  await page.goto('/stage-1');
  await page.setInputFiles('#fileInput', fileFixture('basic.csv'));
  await page.locator('#analyzeButton').waitFor({ state: 'visible' });
  await page.waitForFunction(() => !document.querySelector('#analyzeButton')?.disabled);
  await page.click('#analyzeButton');

  await page.locator('.data-model-dialog').waitFor({ state: 'visible' });

  // Negative check: panel hidden before trigger click
  await expect(page.locator('.data-model-dropdown--version .data-model-dropdown-panel')).toBeHidden();

  await page.click('#versionDropdownTrigger');
  await page.locator('.data-model-dropdown--version .data-model-dropdown-panel').waitFor({ state: 'visible' });

  const geom = await page.evaluate(() => {
    const trigger = document.querySelector('#versionDropdownTrigger');
    const panel = document.querySelector('.data-model-dropdown--version .data-model-dropdown-panel');
    /* Scroll lives on the inner list; outer panel only clips for rounded corners. */
    const list = document.querySelector('.data-model-dropdown--version .data-model-dropdown-list');
    return {
      triggerBottom: trigger.getBoundingClientRect().bottom,
      panelTop: panel.getBoundingClientRect().top,
      listClientHeight: list.clientHeight,
      listScrollHeight: list.scrollHeight,
      itemCount: panel.querySelectorAll('.data-model-dropdown-item').length,
    };
  });
  expect(geom.panelTop).toBeGreaterThanOrEqual(geom.triggerBottom);
  expect(geom.itemCount).toBe(20);
  expect(geom.listScrollHeight).toBeGreaterThan(geom.listClientHeight);

  const items = page.locator('.data-model-dropdown--version .data-model-dropdown-item');
  await expect(items.first()).toHaveText('11.0.20');
  await expect(items.first()).toHaveAttribute('aria-selected', 'true');
});

test('data model dropdown shares custom styling and custom dropdowns close on outside click', async ({ page }) => {
  /*
   * Given: the Stage 1 data model popup has two data models and multiple versions
   * When:  the user opens the data model dropdown and the version dropdown
   * Then:  both use the same custom trigger styling, the hidden native select
   *        remains available, and clicking elsewhere in the dialog dismisses
   *        the open custom dropdown.
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
            { version_label: 'v1', version_number: 1, external_version_number: '11.0.1', is_default: false },
            { version_label: 'v3', version_number: 3, external_version_number: '11.0.3', is_default: true },
          ],
        },
        {
          data_model_key: 'gc',
          label: 'Genomic Cancer',
          versions: [
            { version_label: 'v2', version_number: 2, external_version_number: '11.0.2', is_default: true },
          ],
        },
      ]),
    });
  });
  await mockAnalyze(page);

  await page.goto('/stage-1');
  await page.setInputFiles('#fileInput', fileFixture('basic.csv'));
  await page.locator('#analyzeButton').waitFor({ state: 'visible' });
  await page.waitForFunction(() => !document.querySelector('#analyzeButton')?.disabled);
  await page.click('#analyzeButton');
  await page.locator('.data-model-dialog').waitFor({ state: 'visible' });

  // Given: the native data model select is hidden, not removed.
  await expect(page.locator('#dataModelSelect')).toHaveClass(/sr-only/);
  await expect(page.locator('.data-model-dropdown--model .data-model-dropdown-panel')).toBeHidden();

  // When: the data model dropdown opens.
  await page.click('#dataModelDropdownTrigger');
  await expect(page.locator('.data-model-dropdown--model .data-model-dropdown-panel')).toBeVisible();
  await expect(page.locator('.data-model-dropdown--model .data-model-dropdown-item')).toHaveCount(2);

  // Then: the model trigger uses the same styling as the version trigger.
  const triggerStyles = await page.evaluate(() => {
    const model = getComputedStyle(document.querySelector('#dataModelDropdownTrigger'));
    const version = getComputedStyle(document.querySelector('#versionDropdownTrigger'));
    return {
      sameBorderRadius: model.borderRadius === version.borderRadius,
      samePadding: model.padding === version.padding,
      sameBackgroundImage: model.backgroundImage === version.backgroundImage,
      sameTextAlign: model.textAlign === version.textAlign,
    };
  });
  expect(triggerStyles).toEqual({
    sameBorderRadius: true,
    samePadding: true,
    sameBackgroundImage: true,
    sameTextAlign: true,
  });

  // When: the user clicks elsewhere in the dialog.
  await page.locator('.data-model-dialog-title').click();

  // Then: the open custom dropdown closes.
  await expect(page.locator('.data-model-dropdown--model .data-model-dropdown-panel')).toBeHidden();

  // When: selecting a model through the custom dropdown.
  await page.click('#dataModelDropdownTrigger');
  await page.locator('.data-model-dropdown--model .data-model-dropdown-item[data-value="alpha"]').click();

  // Then: the hidden select and dependent version dropdown stay in sync.
  await expect(page.locator('#dataModelSelect')).toHaveValue('alpha');
  await expect(page.locator('#versionDropdownTrigger')).toContainText('11.0.3');

  // When: automation changes the hidden select directly.
  await page.selectOption('#dataModelSelect', 'gc');

  // Then: the visible custom dropdown and version list still stay in sync.
  await expect(page.locator('#dataModelDropdownTrigger')).toContainText('Genomic Cancer');
  await expect(page.locator('#versionDropdownTrigger')).toContainText('11.0.2');

  // When: a keyboard user changes the custom model dropdown.
  await page.click('#dataModelDropdownTrigger');
  await page.keyboard.press('ArrowUp');
  await page.keyboard.press('Enter');

  // Then: the same state sync path is used.
  await expect(page.locator('#dataModelSelect')).toHaveValue('alpha');
  await expect(page.locator('#dataModelDropdownTrigger')).toContainText('Alpha Model');
  await expect(page.locator('#versionDropdownTrigger')).toContainText('11.0.3');

  // When/Then: the version dropdown also closes when the user clicks off it.
  await page.click('#versionDropdownTrigger');
  await expect(page.locator('.data-model-dropdown--version .data-model-dropdown-panel')).toBeVisible();
  await page.locator('.data-model-dialog-title').click();
  await expect(page.locator('.data-model-dropdown--version .data-model-dropdown-panel')).toBeHidden();
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
        file_id: 'abc12345',
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
  await page.setInputFiles('#fileInput', fileFixture('basic.csv'));
  await uploadStarted;

  // Then: the blocking upload indicator is visible and the action remains disabled
  await expect(page.locator('#dropzoneUploading')).toBeVisible();
  await expect(page.locator('#dropzoneUploading')).toContainText('Please wait while your file is uploaded');
  await expect(page.locator('#analyzeButton')).toBeDisabled();

  // When: upload completes
  releaseUpload();

  // Then: the normal uploaded state returns and the action is enabled
  await expect(page.locator('#dropzoneFileStatus')).toHaveText('Uploaded');
  await expect(page.locator('#dropzoneUploading')).toBeHidden();
  await expect(page.locator('#analyzeButton')).toBeEnabled();
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

  // And: Stage 5 can load from the URL alone
  const savedStageThreePayload = await page.evaluate(() => sessionStorage.getItem('stage3HarmonizePayload'));
  await page.evaluate(() => sessionStorage.removeItem('stage3HarmonizePayload'));
  await page.reload();
  await expect(page.locator('#summaryGrid')).not.toContainText('Unable to locate harmonization context.');
  await page.evaluate((payload) => {
    if (payload) sessionStorage.setItem('stage3HarmonizePayload', payload);
  }, savedStageThreePayload);

  // When: the user goes back to Stage 2
  await page.click('.progress-track .step[data-stage="mapping"]');
  await page.waitForURL(/\/stage-2/);

  // Then: mapping is inspection-only for the completed harmonization
  const stageTwoUrl = new URL(page.url());
  expect(stageTwoUrl.searchParams.get('file_id')).toBe(fileId);
  expect(stageTwoUrl.searchParams.get('schema')).toBe('gc');
  expect(stageTwoUrl.searchParams.get('external_version_number')).toBe('11.0.4');
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

test('Stage 3 ignores stale session payload when URL points at a new file', async ({ page }) => {
  let harmonizePayload = null;
  const currentFileId = '22222222abcdef00';

  await page.route('**/stage-3/harmonize', async (route) => {
    harmonizePayload = route.request().postDataJSON?.() ?? {};
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        job_id: 'e2e-job-current-file',
        status: 'succeeded',
        detail: 'Harmonization completed.',
        next_stage_url: `/stage-4?file_id=${currentFileId}&job_id=e2e-job-current-file&status=succeeded`,
        job_id_available: true,
        manifest_summary: null,
      }),
    });
  });

  // Given: the browser has a Stage 3 payload for a previous workflow
  await page.goto('/stage-1');
  await page.evaluate(() => {
    sessionStorage.setItem(
      'stage3HarmonizePayload',
      JSON.stringify({
        request: {
          file_id: '11111111abcdef00',
          target_schema: 'stale',
          target_external_version_number: '99.0.0',
          manual_overrides: {},
        },
      }),
    );
  });

  // When: Stage 3 is opened for a different file from the URL alone
  await page.goto(`/stage-3?file_id=${currentFileId}&target_schema=gc&external_version_number=11.0.4`);
  await expect(page.locator('#reviewButton')).toBeEnabled();

  // Then: harmonization starts with the current URL file, not the stale session file
  expect(harmonizePayload?.file_id).toBe(currentFileId);
  expect(harmonizePayload?.target_schema).toBe('gc');
  expect(harmonizePayload?.target_external_version_number).toBe('11.0.4');
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
