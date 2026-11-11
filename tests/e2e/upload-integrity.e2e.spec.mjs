import { test, expect } from '@playwright/test';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import { AGENT_FILE_INPUT, fileFixture, createWorkbookFixture } from './utils.mjs';

const mismatchMessage = 'The uploaded file did not match the selected file. Please try again.';

for (const format of ['csv', 'tsv', 'xlsx']) {
  test(`browser verifies exact ${format.toUpperCase()} upload bytes`, async ({ page }) => {
    // Given original file bytes, including text encoding and complete binary workbooks.
    const bytes = format === 'xlsx'
      ? fs.readFileSync(createWorkbookFixture())
      : Buffer.from(`\ufeffid${format === 'tsv' ? '\t' : ','}value\r\n1${format === 'tsv' ? '\t' : ','}café\r\n`);
    await page.goto('/stage-1');
    const responsePromise = page.waitForResponse((response) => response.url().endsWith('/stage-1/upload'));

    // When the real upload page hashes and sends the selected File.
    await page.setInputFiles(AGENT_FILE_INPUT, {
      name: `source.${format}`,
      mimeType: format === 'xlsx' ? 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        : format === 'tsv' ? 'text/tab-separated-values' : 'text/csv',
      buffer: bytes,
    });
    const response = await responsePromise;

    // Then the server independently agrees with an external hash and enables Map.
    expect(response.status()).toBe(201);
    expect((await response.json()).verification).toEqual({
      sha256: createHash('sha256').update(bytes).digest('hex'), size_bytes: bytes.length,
    });
    await expect(page.locator('#dropzoneFileStatus')).toHaveText('Uploaded');
    await expect(page.locator('#analyzeButton')).toBeEnabled();
  });
}

for (const alteration of ['changed byte', 'truncated file', 'wrong size']) {
  test(`browser rejects a ${alteration} during transfer`, async ({ page }) => {
    // Given a source file and a transfer that changes its bytes after browser hashing.
    const bytes = fs.readFileSync(fileFixture('basic.csv'));
    let rejectedStatus;
    await page.route('**/stage-1/upload', async (route) => {
      const body = route.request().postDataBuffer();
      const sha256 = createHash('sha256').update(bytes).digest('hex');
      expect(body.toString()).toContain(sha256);
      expect(body.toString()).toContain(String(bytes.length));
      let modified = Buffer.from(bytes);
      if (alteration === 'changed byte') {
        modified[0] ^= 1;
      } else if (alteration === 'truncated file') {
        modified = bytes.subarray(0, 3);
      }
      // Chromium's interception API omits file-part bytes. Rebuild that part
      // while preserving the fingerprint submitted by the real browser.
      const response = await page.request.post(route.request().url(), { multipart: {
        file: { name: 'basic.csv', mimeType: 'text/csv', buffer: modified },
        expected_sha256: sha256,
        expected_size_bytes: String(bytes.length + (alteration === 'wrong size' ? 1 : 0)),
      } });
      rejectedStatus = response.status();
      await route.fulfill({ response });
    });
    await page.goto('/stage-1');

    // When the original selected file is submitted through the real UI.
    await page.setInputFiles(AGENT_FILE_INPUT, fileFixture('basic.csv'));

    // Then verification fails and no workflow is activated in the browser.
    await expect(page.locator('#statusMessage')).toHaveText(mismatchMessage);
    expect(rejectedStatus).toBe(400);
    await expect(page.locator('#dropzoneFile .upload-error-mark')).toBeVisible();
    await expect(page.locator('#dropzoneFile .upload-success-mark')).toBeHidden();
    await expect(page.locator('#dropzoneFileStatus')).toHaveCSS('color', 'rgb(185, 28, 28)');
    await expect(page.locator('#analyzeButton')).toBeDisabled();
    await expect(page.locator('#analyzeButton .btn-3d-front')).toHaveCSS('background-image', 'none');
    await expect(page.locator('#analyzeButton .btn-3d-front')).toHaveCSS('background-color', 'rgb(241, 245, 249)');
    await expect(page.locator('#analyzeButton .btn-3d-shadow')).toBeHidden();
    expect(await page.evaluate(() => sessionStorage.getItem('currentFileSession'))).toBeNull();
  });
}

test('a successful HTTP response without a matching receipt cannot activate a file', async ({ page }) => {
  // Given an old server response that has no verification receipt.
  await page.route('**/stage-1/upload', (route) => route.fulfill({
    status: 201, contentType: 'application/json', body: JSON.stringify({ file_id: 'a'.repeat(32) }),
  }));
  await page.goto('/stage-1');
  // When the browser uploads a file.
  await page.setInputFiles(AGENT_FILE_INPUT, fileFixture('basic.csv'));
  // Then the response is rejected instead of being shown as a verified upload.
  await expect(page.locator('#statusMessage')).toHaveText(mismatchMessage);
  await expect(page.locator('#analyzeButton')).toBeDisabled();
  expect(await page.evaluate(() => sessionStorage.getItem('currentFileSession'))).toBeNull();
});

test('unavailable Web Crypto stops the browser before any upload', async ({ page }) => {
  // Given an insecure or unsupported browser with no digest API.
  await page.addInitScript(() => Object.defineProperty(globalThis.crypto, 'subtle', { value: undefined }));
  const uploads = [];
  page.on('request', (request) => { if (request.url().endsWith('/stage-1/upload')) uploads.push(request); });
  await page.goto('/stage-1');
  // When a file is selected.
  await page.setInputFiles(AGENT_FILE_INPUT, fileFixture('basic.csv'));
  // Then the app explains the requirement and makes no unverified request.
  await expect(page.locator('#statusMessage')).toContainText('HTTPS or localhost');
  expect(uploads).toHaveLength(0);
  await expect(page.locator('#fileInput')).toBeEnabled();
  await expect(page.locator('#analyzeButton')).toBeDisabled();
});

test('hash failure permits retry without submitting an unverified file', async ({ page }) => {
  // Given a browser digest API that fails once.
  await page.addInitScript(() => {
    const digest = crypto.subtle.digest.bind(crypto.subtle);
    let fail = true;
    crypto.subtle.digest = (...args) => {
      if (fail) { fail = false; return Promise.reject(new Error('private diagnostic')); }
      return digest(...args);
    };
    sessionStorage.setItem('maxReachedStage', 'review');
  });
  const uploads = [];
  page.on('request', (request) => { if (request.url().endsWith('/stage-1/upload')) uploads.push(request); });
  await page.goto('/stage-1');
  // When hashing fails and the user retries the same file.
  await page.setInputFiles(AGENT_FILE_INPUT, fileFixture('basic.csv'));
  await expect(page.locator('#statusMessage')).toHaveText('We could not prepare this file for upload. Please select it again.');
  await expect(page.locator('#dropzoneFile .upload-error-mark')).toBeVisible();
  expect(uploads).toHaveLength(0);
  await expect(page.locator('#analyzeButton')).toBeDisabled();
  await expect(page.locator('.progress-tracker [data-stage="mapping"] .step-link')).toHaveAttribute('aria-disabled', 'false');
  await page.setInputFiles(AGENT_FILE_INPUT, []);
  await page.setInputFiles(AGENT_FILE_INPUT, fileFixture('basic.csv'));
  // Then only the verified retry is uploaded and Map becomes available.
  await expect(page.locator('#dropzoneFileStatus')).toHaveText('Uploaded');
  await expect(page.locator('#dropzoneFile .upload-success-mark')).toBeVisible();
  await expect(page.locator('#dropzoneFile .upload-error-mark')).toBeHidden();
  expect(uploads).toHaveLength(1);
  await expect(page.locator('#analyzeButton')).toBeEnabled();
});

test('selection stays locked until the original file finishes hashing', async ({ page }) => {
  // Given hashing is held pending while the user can still interact with the page.
  await page.addInitScript(() => {
    const digest = crypto.subtle.digest.bind(crypto.subtle);
    const pending = new Promise((resolve) => { window.mockReleaseDigest = resolve; });
    crypto.subtle.digest = async (...args) => { await pending; return digest(...args); };
    sessionStorage.setItem('maxReachedStage', 'review');
  });
  const uploads = [];
  page.on('request', (request) => { if (request.url().endsWith('/stage-1/upload')) uploads.push(request); });
  const previousFileId = 'a'.repeat(32);
  await page.goto(`/stage-1?file_id=${previousFileId}`);
  const mappingLink = page.locator('.progress-tracker [data-stage="mapping"] .step-link');
  await expect(mappingLink).toHaveAttribute('aria-disabled', 'false');
  await page.setInputFiles(AGENT_FILE_INPUT, fileFixture('basic.csv'));
  await expect(page.locator('#uploadingTitle')).toHaveText('Preparing upload…');
  await expect(page.locator('#fileInput')).toBeDisabled();
  await expect(page.locator('#analyzeButton')).toBeDisabled();
  await expect(mappingLink).toHaveAttribute('aria-disabled', 'true');
  await expect(mappingLink).not.toHaveAttribute('href');
  expect(uploads).toHaveLength(0);
  // When an old workflow link is clicked and another file is dropped before hashing is released.
  await mappingLink.click();
  await expect(page).toHaveURL(new RegExp(`/stage-1\\?file_id=${previousFileId}$`));
  await page.evaluate(() => {
    const transfer = new DataTransfer();
    transfer.items.add(new File(['value\nwrong\n'], 'replacement.csv', { type: 'text/csv' }));
    document.querySelector('#dropzone').dispatchEvent(new DragEvent('drop', { bubbles: true, dataTransfer: transfer }));
    window.mockReleaseDigest();
  });
  // Then the original file alone becomes the active upload.
  await expect(page.locator('#dropzoneFileStatus')).toHaveText('Uploaded');
  await expect(page.locator('#dropzoneFileName')).toHaveText('basic.csv');
  expect(uploads).toHaveLength(1);
  await expect(mappingLink).toHaveAttribute('aria-disabled', 'false');
  expect(await page.evaluate(async () => {
    const { readFromSession, CURRENT_FILE_SESSION_KEY } = await import('/assets/shared/storage-keys.js');
    return readFromSession(CURRENT_FILE_SESSION_KEY).original_name;
  })).toBe('basic.csv');
});
