import { execFileSync } from 'node:child_process';
import path from 'node:path';
import AdmZip from 'adm-zip';

export const fixturesDir = path.resolve('tests/e2e/fixtures');

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
    const response = {
      file_id: fileId,
      file_name: 'test.csv',
      total_rows: 3,
      columns: [
        {
          column_id: 0,
          column_name: 'col_a',
          inferred_type: 'text',
          sample_values: ['Foo', 'Bar'],
          confidence_bucket: 'high',
          confidence_score: 0.95,
        },
      ],
      cde_targets: {},
      next_stage: 'mapping',
      next_step_hint: 'Review AI-suggested column mappings once ready.',
      manual_overrides: {},
      manifest: {
        column_mappings: [
          {
            column_name: 'col_a',
            cde_key: 'col_a',
            cde_id: 1,
            harmonization: 'harmonizable',
            alternatives: [{ target: 'col_a', confidence: 0.9, cde_id: 1, harmonization: 'harmonizable' }],
          },
        ],
      },
    };
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(response),
    });
  });
};

/** Analyze mock with two columns exercising different harmonization states.
 *
 * First column is 'harmonizable' (no badge); second is 'no_permissible_values'
 * (renders the "Values pass through" badge on its top-suggestion cell).
 */
export const mockAnalyzeHarmonizationMix = async (page) => {
  await page.route('**/stage-1/analyze', async (route) => {
    const payload = route.request().postDataJSON?.() ?? {};
    const fileId = payload.file_id ?? '';
    const response = {
      file_id: fileId,
      file_name: 'test.csv',
      total_rows: 2,
      columns: [
        {
          column_id: 0,
          column_name: 'diagnosis',
          inferred_type: 'text',
          sample_values: ['Cancer'],
          confidence_bucket: 'high',
          confidence_score: 0.9,
        },
        {
          column_id: 1,
          column_name: 'middle_name',
          inferred_type: 'text',
          sample_values: ['Ann'],
          confidence_bucket: 'high',
          confidence_score: 1.0,
        },
      ],
      cde_targets: {
        diagnosis: [{ target: 'disease_type', confidence: 0.9, harmonization: 'harmonizable' }],
        middle_name: [{ target: 'middle_name', confidence: 1.0, harmonization: 'no_permissible_values' }],
      },
      next_stage: 'mapping',
      next_step_hint: 'Review AI-suggested column mappings once ready.',
      manual_overrides: {},
      manifest: {
        column_mappings: [
          {
            column_name: 'diagnosis',
            cde_key: 'disease_type',
            cde_id: 323,
            harmonization: 'harmonizable',
            alternatives: [{ target: 'disease_type', confidence: 0.9, cde_id: 323, harmonization: 'harmonizable' }],
          },
          {
            column_name: 'middle_name',
            cde_key: 'middle_name',
            cde_id: 316,
            harmonization: 'no_permissible_values',
            alternatives: [{ target: 'middle_name', confidence: 1.0, cde_id: 316, harmonization: 'no_permissible_values' }],
          },
        ],
      },
    };
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(response),
    });
  });
};

/** Same shape as mockAnalyze but with zero effective mappings.
 *
 * cde_targets is empty and column_mappings is an empty list so
 * build_column_assignments produces no CDE assignments. Used to test the Stage 2
 * submittability gate without touching the stable mockAnalyze fixture.
 */
export const mockAnalyzeNoMappings = async (page) => {
  await page.route('**/stage-1/analyze', async (route) => {
    const payload = route.request().postDataJSON?.() ?? {};
    const fileId = payload.file_id ?? '';
    const response = {
      file_id: fileId,
      file_name: 'test.csv',
      total_rows: 3,
      columns: [
        {
          column_id: 0,
          column_name: 'col_a',
          inferred_type: 'text',
          sample_values: ['Foo', 'Bar'],
          confidence_bucket: 'high',
          confidence_score: 0.95,
        },
      ],
      cde_targets: {},
      next_stage: 'mapping',
      next_step_hint: 'Review AI-suggested column mappings once ready.',
      manual_overrides: {},
      manifest: {
        column_mappings: [],
      },
    };
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(response),
    });
  });
};

/** Inject real CDE options into Stage 2's server-rendered config.
 *
 * Stage 2 reads CDE options from window.stageTwoConfig.cdeOptions, which the server
 * populates from the Netrias cache. In E2E tests that cache is empty because the
 * Netrias client is not reachable, so the override combobox only contains "No Mapping".
 * Intercept the assignment and inject real CDEs so interaction tests can actually
 * select a non-"No Mapping" option.
 */
export const injectCdeOptions = async (page, cdes) => {
  const payload = cdes ?? [
    { cde_id: 1, cde_key: 'primary_diagnosis', label: 'primary_diagnosis', description: 'Primary diagnosis' },
  ];
  await page.addInitScript((options) => {
    let value;
    Object.defineProperty(window, 'stageTwoConfig', {
      get() {
        return value;
      },
      set(incoming) {
        value = { ...incoming, cdeOptions: options };
      },
      configurable: true,
    });
  }, payload);
};

export const mockDataModels = async (page) => {
  await page.route('**/stage-1/data-models', async (route) => {
    const models = [
      {
        key: 'test-data-model',
        label: 'Test Data Model',
        versions: ['v1'],
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
  ];
  const hasChanges = Object.keys(changes).length > 0;
  if (hasChanges) {
    args.push('--changes', JSON.stringify(changes));
  }
  if (options.noManifest) {
    args.push('--no-manifest');
  }
  execFileSync('uv', args, { stdio: 'inherit' });
};

export const parseDownloadedCsv = async (response) => {
  const buffer = await response.body();
  const zip = new AdmZip(Buffer.from(buffer));
  const entry = zip.getEntries().find((item) => item.entryName.endsWith('.csv'));
  if (!entry) {
    throw new Error('No CSV found in download zip.');
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
  const headers = parseCsvLine(headerLine);
  return lines.map((line) => {
    const values = parseCsvLine(line);
    return Object.fromEntries(headers.map((header, idx) => [header, values[idx] ?? '']));
  });
};

const parseCsvLine = (line) => {
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
    } else if (char === ',' && !inQuotes) {
      values.push(current);
      current = '';
    } else {
      current += char;
    }
  }

  values.push(current);
  return values;
};
