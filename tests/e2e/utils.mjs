import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { expect } from '@playwright/test';
import AdmZip from 'adm-zip';

import { e2eEnv } from './runtime-env.mjs';

export const fixturesDir = path.resolve('tests/e2e/fixtures');
const E2E_TARGET_MODEL = 'gc';
const E2E_TARGET_VERSION_NUMBER = 2;
const E2E_TARGET_EXTERNAL_VERSION_NUMBER = '11.0.4';
const E2E_TARGET_CDE = 'primary_diagnosis';
const E2E_TARGET_CDE_ID = 376;

export const fileFixture = (name) => path.join(fixturesDir, name);

export const getFileIdFromUrl = (page) => {
  const url = new URL(page.url());
  return url.searchParams.get('file_id');
};

export const uploadAndAnalyze = async (page, filePath) => {
  await mockDataModels(page);
  await mockAnalyze(page);
  await page.goto('/stage-1');
  await page.setInputFiles('#fileInput', filePath);
  await page.locator('#analyzeButton').waitFor({ state: 'attached' });
  await page.locator('#analyzeButton').waitFor({ state: 'visible' });
  await page.waitForFunction(() => !document.querySelector('#analyzeButton')?.disabled);
  await page.click('#analyzeButton');
  const confirmButton = page.locator('.data-model-confirm-btn');
  await confirmButton.waitFor({ state: 'visible' });
  await confirmButton.click();
  await page.waitForURL(/\/stage-2/);
  await expect(page.locator('#mappingRows .mapping-row-target').first()).toContainText(
    E2E_TARGET_CDE,
  );
  return getFileIdFromUrl(page);
};

export const uploadAndAnalyzeSheet = async (page, filePath, sheetName) => {
  await mockDataModels(page);
  await mockAnalyze(page);
  await page.goto('/stage-1');
  await page.setInputFiles('#fileInput', filePath);
  await page.locator('#analyzeButton').waitFor({ state: 'attached' });
  await page.locator('#analyzeButton').waitFor({ state: 'visible' });
  await page.waitForFunction(() => !document.querySelector('#analyzeButton')?.disabled);
  // Workbook tabs render once upload finishes; click the named tab to select.
  const sheetTab = page.locator(`.workbook-tab[data-sheet-name="${sheetName}"]`);
  await sheetTab.waitFor({ state: 'visible' });
  await sheetTab.click();
  await page.click('#analyzeButton');
  const confirmButton = page.locator('.data-model-confirm-btn');
  await confirmButton.waitFor({ state: 'visible' });
  await confirmButton.click();
  await page.waitForURL(/\/stage-2/);
  await expect(page.locator('#mappingRows .mapping-row-target').first()).toContainText(
    E2E_TARGET_CDE,
  );
  return getFileIdFromUrl(page);
};

export const clickHarmonize = async (page) => {
  const button = page.locator('#harmonizeButton');
  await button.waitFor({ state: 'attached' });
  await button.waitFor({ state: 'visible' });
  await page.waitForFunction(() => !document.querySelector('#harmonizeButton')?.disabled);
  await button.click();
  await page.waitForURL(/\/stage-3/);
};

export const mockHarmonizeSuccess = async (page) => {
  await page.route('**/stage-3/harmonize', async (route) => {
    const payload = route.request().postDataJSON?.() ?? {};
    const fileId = payload.file_id ?? '';
    const response = {
      job_id: 'e2e-job-1',
      status: 'succeeded',
      detail: 'Harmonization completed.',
      next_stage_url: `/stage-4?file_id=${fileId}&job_id=e2e-job-1&status=succeeded`,
      job_id_available: true,
      manifest_summary: null,
    };
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(response),
    });
  });
};

export const mockAnalyze = async (page) => {
  await page.route('**/stage-1/analyze', async (route) => {
    const payload = route.request().postDataJSON?.() ?? {};
    const fileId = payload.file_id ?? '';
    if (fileId && payload.sheet_name) {
      persistSelectedSheet(fileId, payload.sheet_name);
    }
    const response = {
      file_id: fileId,
      file_name: 'test.csv',
      total_rows: 3,
      columns: [
        {
          column_name: 'col_a',
          column_key: 'col_a',
          source_index: 0,
          header: 'col_a',
          inferred_type: 'text',
          sample_values: ['Foo', 'Bar'],
          confidence_bucket: 'high',
          confidence_score: 0.95,
        },
      ],
      cde_targets: {
        col_a: [{ target: E2E_TARGET_CDE, similarity: 0.95 }],
      },
      column_summaries: {
        col_a: { value_overlap_ratio: 0.5 },
      },
      next_stage: 'mapping',
      next_step_hint: 'Review AI-suggested column mappings once ready.',
      manual_overrides: {},
      manifest: {
        column_mappings: {
          col_a: { cde_key: E2E_TARGET_CDE, cde_id: E2E_TARGET_CDE_ID },
        },
      },
    };
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(response),
    });
  });
};


export const mockColumnDetail = async (page) => {
  await page.route('**/stage-2/column-detail/**', async (route) => {
    const url = new URL(route.request().url());
    const parts = url.pathname.split('/');
    const columnKey = decodeURIComponent(parts[parts.length - 1] ?? '');
    const response = {
      column_key: columnKey,
      profile: {
        column_key: columnKey,
        total_rows: 3,
        distinct_values: [
          { value: 'Foo', count: 2 },
          { value: 'Bar', count: 1 },
        ],
        null_count: 0,
        total_distinct: 2,
        null_pct: 0.0,
        is_all_unique: false,
      },
      match_counts: {},
      overlap_by_cde: {},
      cde_types: {},
      selected_pvs: null,
    };
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(response),
    });
  });
};

export const mockDataModels = async (page) => {
  await page.route('**/stage-1/data-models', async (route) => {
    const models = [
      {
        key: E2E_TARGET_MODEL,
        label: 'Genomic Cancer',
        versions: [
          {
            version_label: `v${E2E_TARGET_VERSION_NUMBER}`,
            version_number: E2E_TARGET_VERSION_NUMBER,
            external_version_number: E2E_TARGET_EXTERNAL_VERSION_NUMBER,
            is_default: true,
          },
        ],
      },
    ];
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(models),
    });
  });
};

/**
 * Mock data models with N synthetic versions; latest is the default.
 * Used to exercise overflow/scroll behavior on the version dropdown panel.
 */
export const mockDataModelsWithVersionCount = async (page, count) => {
  await page.route('**/stage-1/data-models', async (route) => {
    const versions = Array.from({ length: count }, (_, i) => ({
      version_label: `v${i + 1}.0`,
      version_number: i + 1,
      external_version_number: `11.0.${i + 1}`,
      is_default: i === count - 1,
    }));
    const models = [
      {
        key: 'test-data-model',
        label: 'Test Data Model',
        versions,
      },
    ];
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(models),
    });
  });
};

export const mockHarmonizeFailure = async (page) => {
  await page.route('**/stage-3/harmonize', async (route) => {
    await route.fulfill({
      status: 500,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Unable to start harmonization job.' }),
    });
  });
};

export const seedHarmonization = (fileId, changes = {}, options = {}) => {
  const args = [
    'run',
    'python',
    path.resolve('tests/e2e/support/seed_harmonization.py'),
    '--file-id',
    fileId,
    '--upload-base-dir',
    e2eEnv.DATA_CHORD_UPLOAD_DIR,
  ];
  const hasChanges = Object.keys(changes).length > 0;
  if (hasChanges) {
    args.push('--changes', JSON.stringify(changes));
  }
  if (options.noManifest) {
    args.push('--no-manifest');
  }
  execFileSync('uv', args, { env: e2eEnv, stdio: 'inherit' });
};

export const createWorkbookFixture = () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'data-chord-e2e-xlsx-'));
  const workbookPath = path.join(tmpDir, 'workbook.xlsx');
  execFileSync('uv', [
    'run',
    'python',
    path.resolve('tests/e2e/support/create_workbook_fixture.py'),
    '--output',
    workbookPath,
  ], { env: e2eEnv, stdio: 'inherit' });
  return workbookPath;
};

export const parseDownloadedWorkbook = async (response, sheetName) => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'data-chord-e2e-download-'));
  const zipPath = path.join(tmpDir, 'download.zip');
  fs.writeFileSync(zipPath, Buffer.from(await response.body()));
  const output = execFileSync('uv', [
    'run',
    'python',
    path.resolve('tests/e2e/support/read_downloaded_workbook.py'),
    '--zip-path',
    zipPath,
    '--sheet-name',
    sheetName,
  ], { encoding: 'utf-8', env: e2eEnv });
  return JSON.parse(output);
};

const persistSelectedSheet = (fileId, sheetName) => {
  execFileSync('uv', [
    'run',
    'python',
    path.resolve('tests/e2e/support/select_sheet.py'),
    '--file-id',
    fileId,
    '--sheet-name',
    sheetName,
  ], { env: e2eEnv, stdio: 'inherit' });
};

export const parseDownloadedCsv = async (response) => {
  return parseDownloadedTabular(response, '.csv', ',');
};

export const parseDownloadedTabular = async (response, suffix, delimiter) => {
  const buffer = await response.body();
  const zip = new AdmZip(Buffer.from(buffer));
  const entries = zip.getEntries();
  const entry = entries.find((item) => item.entryName.endsWith(suffix));
  if (!entry) {
    const entryNames = entries.map((item) => item.entryName).join(', ');
    throw new Error(`No ${suffix} found in download zip. Entries: ${entryNames}`);
  }
  const content = entry.getData().toString('utf-8');
  const lines = content.split(/\r?\n/);
  if (lines.length > 0 && lines[lines.length - 1] === '') {
    lines.pop();
  }
  const headerLine = lines.shift();
  if (!headerLine) {
    return [];
  }
  const headers = parseDelimitedLine(headerLine, delimiter);
  return lines.map((line) => {
    const values = parseDelimitedLine(line, delimiter);
    return Object.fromEntries(headers.map((header, idx) => [header, values[idx] ?? '']));
  });
};

const parseDelimitedLine = (line, delimiter) => {
  const values = [];
  let current = '';
  let inQuotes = false;

  for (let i = 0; i < line.length; i += 1) {
    const char = line[i];
    if (char === '"') {
      if (inQuotes && line[i + 1] === '"') {
        current += '"';
        i += 1;
      } else {
        inQuotes = !inQuotes;
      }
    } else if (char === delimiter && !inQuotes) {
      values.push(current);
      current = '';
    } else {
      current += char;
    }
  }

  values.push(current);
  return values;
};
