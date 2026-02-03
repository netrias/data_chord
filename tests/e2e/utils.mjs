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
  await page.goto('/stage-1');
  await page.setInputFiles('#fileInput', filePath);
  await page.locator('#analyzeButton').waitFor({ state: 'attached' });
  await page.locator('#analyzeButton').waitFor({ state: 'visible' });
  await page.waitForFunction(() => !document.querySelector('#analyzeButton')?.disabled);
  await page.click('#analyzeButton');
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
  const [headerLine, ...lines] = content.trim().split(/\r?\n/);
  const headers = headerLine.split(',');
  return lines.map((line) => {
    const values = line.split(',');
    return Object.fromEntries(headers.map((header, idx) => [header, values[idx]]));
  });
};
