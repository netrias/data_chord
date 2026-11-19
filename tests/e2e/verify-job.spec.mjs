// Verify links belong to the run whose results the reviewer opened.
import { expect, test } from '@playwright/test';
import { clickHarmonize, fileFixture, uploadAndAnalyzeReal } from './utils.mjs';

const correction = 'Carcinoma NOS';
const value = page => page.locator('.row-cell')
  .filter({ has: page.locator('.original-context-value', { hasText: 'adamantinoma' }) });

async function openUpload(page) {
  const fileId = await uploadAndAnalyzeReal(page, fileFixture('restore-original.csv'));
  await clickHarmonize(page);
  await page.click('#reviewButton');
  await expect(value(page).locator('.pv-combobox-link')).toHaveText('adamantinoma');
  return fileId;
}

async function selectCorrection(page) {
  await value(page).locator('.pv-combobox-link').click();
  await page.locator('.pv-selection-option[data-value="Carcinoma NOS"]').click();
}

async function rerun(page, fileId) {
  const response = await page.request.post('/stage-3/harmonize', { data: { file_id: fileId } });
  expect(response.ok()).toBeTruthy();
  const job = await response.json();
  await expect.poll(async () => {
    const status = await page.request.get('/stage-3/jobs/' + job.job_id, { params: { file_id: fileId } });
    return (await status.json()).status;
  }).toBe('succeeded');
}

for (const action of ['reopen the link', 'save from the open tab', 'advance to Review']) {
  test('superseded Verify job when reviewers ' + action, async ({ page }) => {
    // Given an open Verify tab for a completed job and a second completed job on the same upload.
    const fileId = await openUpload(page);
    const oldUrl = page.url();
    await rerun(page, fileId);

    // When the reviewer returns to the old link or saves from the tab that was already open.
    if (action === 'reopen the link') await page.goto(oldUrl);
    else if (action === 'advance to Review') await page.click('#stageFiveButton');
    else await selectCorrection(page);

    // Then the old tab explains the rerun and opens the current job only on request.
    await expect(page.getByText(/This job was superseded/)).toBeVisible();
    await expect(value(page)).toHaveCount(0);
    await page.getByRole('link', { name: 'Open current job' }).click();
    await expect(value(page).locator('.pv-combobox-link')).toHaveText('adamantinoma');
    expect(new URL(page.url()).searchParams.get('job_id')).toBeTruthy();
    expect(new URL(page.url()).searchParams.get('job_id')).not.toBe(new URL(oldUrl).searchParams.get('job_id'));
  });
}

test('uploading the same file twice leaves both Verify jobs independently editable', async ({ page, context }) => {
  // Given two uploads of the identical spreadsheet with their own completed jobs.
  const first = await openUpload(page);
  const other = await context.newPage();
  const second = await openUpload(other);
  expect(second).not.toBe(first);

  // When the reviewer saves a correction in the first job.
  const save = page.waitForResponse(r => r.url().endsWith('/stage-4/overrides'));
  await selectCorrection(page);
  expect((await save).ok()).toBeTruthy();

  // Then reopening both jobs shows the correction only in the first, and the second remains editable.
  await page.reload();
  await other.reload();
  await expect(value(page).locator('.pv-combobox-link')).toHaveText(correction);
  await expect(value(other).locator('.pv-combobox-link')).toHaveText('adamantinoma');
  const secondSave = other.waitForResponse(r => r.url().endsWith('/stage-4/overrides'));
  await selectCorrection(other);
  expect((await secondSave).ok()).toBeTruthy();
  await other.reload();
  await expect(value(other).locator('.pv-combobox-link')).toHaveText(correction);
});

for (const action of ['warning', 'tracker']) {
  test('return to Verify from an older Review via the ' + action, async ({ page }) => {
    // Given a prepared Review with an unapproved value and a later job on the same upload.
    const fileId = await openUpload(page);
    await page.goto('/stage-5?file_id=' + fileId);
    await expect(page.locator('#conformanceWarningDialog')).toBeVisible();
    await rerun(page, fileId);

    // When the reviewer follows the warning or tracker back to Verify.
    if (action === 'warning') await page.click('#conformanceReturnButton');
    else {
      await page.click('#conformanceProceedButton');
      await page.locator('.progress-tracker [data-stage="verify"] a').click();
    }

    // Then Verify identifies the superseded job and requires a deliberate move to current data.
    await expect(page.locator('#reviewError')).toContainText('superseded');
    await expect(value(page)).toHaveCount(0);
    await page.getByRole('link', { name: 'Open current job' }).click();
    await expect(value(page).locator('.pv-combobox-link')).toHaveText('adamantinoma');
  });
}
