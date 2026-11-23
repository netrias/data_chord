import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import { AGENT_FILE_INPUT, fileFixture, uploadAndAnalyzeReal } from './utils.mjs';

const replacementBytes = fs.readFileSync(fileFixture('basic.csv'));
const tracker = (page, stage) => page.locator(`.progress-tracker [data-stage="${stage}"] .step-link`);

const _uploadReplacement = async (page) => {
  const responsePromise = page.waitForResponse((response) => response.url().endsWith('/stage-1/upload'));
  await page.setInputFiles(AGENT_FILE_INPUT, {
    name: 'replacement.csv', mimeType: 'text/csv', buffer: replacementBytes,
  });
  const response = await responsePromise;
  expect(response.status()).toBe(201);
  await expect(page.locator('#dropzoneFileStatus')).toHaveText('Uploaded');
  return (await response.json()).file_id;
};

test('replacement starts its own workflow and tracker navigation follows it after analysis', async ({ page }) => {
  // Given an analyzed workflow whose Upload URL still identifies the old file.
  const oldId = await uploadAndAnalyzeReal(page, fileFixture('basic.csv'));
  await page.goto(`/stage-1?file_id=${oldId}&data_model_key=old-model&external_version_number=old-version&job_id=old-job`);
  await expect(tracker(page, 'mapping')).toHaveAttribute('aria-disabled', 'false');

  // When a replacement is uploaded and verified.
  const newId = await _uploadReplacement(page);

  // Then its URL and session agree, and old progress cannot skip its analysis.
  expect(newId).not.toBe(oldId);
  expect(new URL(page.url()).search).toBe(`?file_id=${newId}`);
  await expect(tracker(page, 'upload')).toHaveAttribute('href', `/stage-1?file_id=${newId}`);
  await expect(tracker(page, 'mapping')).toHaveAttribute('aria-disabled', 'true');
  await expect(tracker(page, 'mapping')).not.toHaveAttribute('href');
  await expect(page.locator('#analyzeButton')).toBeEnabled();
  expect(await page.evaluate(() => JSON.parse(sessionStorage.getItem('currentFileSession')).file_id)).toBe(newId);

  // When the replacement completes real local analysis and returns to Upload.
  await page.reload();
  await expect(page.locator('#dropzoneFileName')).toHaveText('replacement.csv');
  await page.locator('#analyzeButton').click();
  await page.locator('.data-model-confirm-btn').click();
  await page.waitForURL(/\/stage-2/);
  await expect(page.locator('#mappingRows .mapping-row').first()).toBeVisible();
  expect(new URL(page.url()).searchParams.get('file_id')).toBe(newId);
  await tracker(page, 'upload').click();
  await page.waitForURL(/\/stage-1/);

  // Then both the rendered tracker link and its destination identify the replacement.
  await expect(page.locator('#dropzoneFileName')).toHaveText('replacement.csv');
  await expect(tracker(page, 'mapping')).toHaveAttribute('href', `/stage-2?file_id=${newId}`);
  await tracker(page, 'mapping').click();
  await page.waitForURL(/\/stage-2/);
  expect(new URL(page.url()).searchParams.get('file_id')).toBe(newId);
  await expect(page.locator('#mappingRows .mapping-row').first()).toBeVisible();
});

test('failed replacement cannot revive the old workflow through the tracker', async ({ page }) => {
  // Given an analyzed old workflow and a replacement whose fingerprint will fail.
  const oldId = await uploadAndAnalyzeReal(page, fileFixture('basic.csv'));
  await page.goto(`/stage-1?file_id=${oldId}`);
  await page.route('**/stage-1/upload', async (route) => {
    const response = await page.request.post(route.request().url(), { multipart: {
      file: { name: 'replacement.csv', mimeType: 'text/csv', buffer: replacementBytes },
      expected_sha256: '0'.repeat(64), expected_size_bytes: String(replacementBytes.length),
    } });
    expect(response.status()).toBe(400);
    await route.fulfill({ response });
  });

  // When the replacement is rejected by the real server.
  await page.setInputFiles(AGENT_FILE_INPUT, {
    name: 'replacement.csv', mimeType: 'text/csv', buffer: replacementBytes,
  });
  await expect(page.locator('#dropzoneFileStatus')).toHaveText('Upload failed');

  // Then neither the old identity nor its progress can become the failed upload's context.
  expect(new URL(page.url()).searchParams.get('file_id')).toBeNull();
  expect(await page.evaluate(() => sessionStorage.getItem('currentFileSession'))).toBeNull();
  await expect(page.locator('#analyzeButton')).toBeDisabled();
  await expect(tracker(page, 'upload')).toHaveAttribute('href', '/stage-1');
  for (const stage of ['mapping', 'harmonize', 'verify', 'review']) {
    await expect(tracker(page, stage)).toHaveAttribute('aria-disabled', 'true');
    await expect(tracker(page, stage)).not.toHaveAttribute('href');
  }
  await tracker(page, 'upload').click();
  await page.waitForURL(/\/stage-1$/);
});

test('an explicit old workflow URL never displays a different session file', async ({ page }) => {
  // Given a replaced workflow and browser metadata for the new file.
  const oldId = await uploadAndAnalyzeReal(page, fileFixture('basic.csv'));
  await page.goto(`/stage-1?file_id=${oldId}`);
  const newId = await _uploadReplacement(page);

  // When Back returns to the earlier analyzed workflow.
  await page.goBack();

  // Then its explicit URL still owns the page despite the new upload's session.
  expect(new URL(page.url()).pathname).toBe('/stage-2');
  expect(new URL(page.url()).searchParams.get('file_id')).toBe(oldId);
  await expect(page.locator('#mappingRows .mapping-row').first()).toBeVisible();

  // When a saved link explicitly requests the old workflow's Upload page.
  await page.goto(`/stage-1?file_id=${oldId}`);

  // Then the replacement is not silently shown as the old workflow.
  expect(new URL(page.url()).searchParams.get('file_id')).toBe(oldId);
  await expect(page.locator('#dropzoneFile')).toBeHidden();
  await expect(page.locator('#dropzoneCopy')).toBeVisible();
  await expect(page.locator('#analyzeButton')).toBeDisabled();
  await expect(tracker(page, 'upload')).toHaveAttribute('href', `/stage-1?file_id=${oldId}`);

  // When the current workflow's matching URL is opened again.
  await page.goto(`/stage-1?file_id=${newId}`);

  // Then its compatible session metadata still restores the correct file.
  await expect(page.locator('#dropzoneFileName')).toHaveText('replacement.csv');
  await expect(page.locator('#analyzeButton')).toBeEnabled();
});

test('a root Upload visit can still resume the current session', async ({ page }) => {
  // Given a verified current upload.
  await page.goto('/stage-1');
  const fileId = await _uploadReplacement(page);

  // When Upload is opened without an explicit workflow ID.
  await page.goto('/stage-1');

  // Then the current session supplies the file and the rendered tracker identity.
  await expect(page.locator('#dropzoneFileName')).toHaveText('replacement.csv');
  await expect(page.locator('#analyzeButton')).toBeEnabled();
  await expect(tracker(page, 'upload')).toHaveAttribute('href', `/stage-1?file_id=${fileId}`);
});

test('a locally invalid replacement clears the previous workflow and its progress', async ({ page }) => {
  // Given an analyzed workflow and a file that fails browser format validation.
  const oldId = await uploadAndAnalyzeReal(page, fileFixture('basic.csv'));
  await page.goto(`/stage-1?file_id=${oldId}`);
  const uploads = [];
  page.on('request', (request) => {
    if (request.url().endsWith('/stage-1/upload')) uploads.push(request);
  });

  // When the user selects an unsupported replacement.
  await page.setInputFiles(AGENT_FILE_INPUT, {
    name: 'replacement.txt', mimeType: 'text/plain', buffer: replacementBytes,
  });

  // Then no request or active workflow remains and the old progress is unreachable.
  await expect(page.locator('#statusMessage')).toHaveText('Only CSV, TSV, or XLSX files are supported right now.');
  expect(uploads).toHaveLength(0);
  expect(new URL(page.url()).searchParams.get('file_id')).toBeNull();
  expect(await page.evaluate(() => sessionStorage.getItem('currentFileSession'))).toBeNull();
  await expect(page.locator('#analyzeButton')).toBeDisabled();
  await expect(tracker(page, 'upload')).toHaveAttribute('href', '/stage-1');
  await expect(tracker(page, 'mapping')).toHaveAttribute('aria-disabled', 'true');
  await expect(tracker(page, 'mapping')).not.toHaveAttribute('href');
});
