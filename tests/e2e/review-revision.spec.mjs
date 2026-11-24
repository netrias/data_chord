import fs from 'node:fs/promises';
import path from 'node:path';
import AdmZip from 'adm-zip';
import { e2eEnv } from './runtime-env.mjs';
import { test, expect } from '@playwright/test';
import { uploadAndAnalyzeReal, fileFixture, clickHarmonize, parseDownloadedCsv, reviewParts } from './utils.mjs';

const warning = '#conformanceWarningDialog';
const reviewLink = '.progress-tracker [data-stage="review"] a';
const verifyLink = '.progress-tracker [data-stage="verify"] a';
const term = 'adamantinoma';
const correction = 'Carcinoma NOS';
const card = (page) => page.locator('.row-cell').filter({ has: page.locator('.original-context-value', {hasText: term}) });
const waitPost = (page, suffix) => page.waitForResponse(r => r.url().endsWith(suffix) && r.request().method() === 'POST');

async function start(page) {
  const id = await uploadAndAnalyzeReal(page, fileFixture('restore-original.csv'));
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled({timeout: 15_000});
  await page.click('#reviewButton');
  await expect(card(page).locator('.pv-combobox-link')).toHaveText(term);
  return id;
}

async function summary(page, click) {
  const response = waitPost(page, '/stage-5/review');
  await click();
  const value = await response;
  expect(value.status()).toBe(200);
  await expect(page.locator('#changesTableSection')).toBeVisible();
  return JSON.parse((await reviewParts(value)).get('summary'));
}

async function downloadArchive(page) {
  const download = page.waitForEvent('download');
  await page.click('#downloadResults');
  const archive = await download;
  const archivePath = await archive.path();
  expect(archive.suggestedFilename()).toMatch(/\.zip$/);
  return fs.readFile(archivePath);
}

async function uiDownload(page) {
  const bytes = await downloadArchive(page);
  return (await parseDownloadedCsv({body: async () => bytes})).map(row => row.diagnosis);
}

async function choose(page) {
  await card(page).locator('.pv-combobox-link').click();
  await page.locator('.pv-selection-option[data-value="Carcinoma NOS"]').click();
}

test('8. stale summary does not export newly nonconforming data without review', async ({page,context}) => {
  // Given A reviewed and downloaded an unchanged, clean, fully corrected result.
  const id = await start(page);
  const save = waitPost(page, '/stage-4/overrides');
  await choose(page);
  expect((await save).status()).toBe(200);
  const initial = await summary(page, () => page.click('#stageFiveButton'));
  expect(initial.non_conformant_items).toEqual([]);
  await expect(page.locator(warning)).toBeHidden();
  expect(await uiDownload(page)).toEqual(['Breast Cancer',correction,'Diabetes']);

  // When B restores the source and a freshly opened review correctly warns about it.
  const reviewer = await context.newPage();
  await reviewer.goto('/stage-4?file_id=' + id);
  await expect(card(reviewer).locator('.pv-combobox-link')).toHaveText(correction);
  const restored = waitPost(reviewer, '/stage-4/overrides');
  await card(reviewer).getByRole('button',{name:'Restore original value'}).click();
  expect((await restored).status()).toBe(200);
  const latest = await summary(reviewer, () => reviewer.click('#stageFiveButton'));
  await expect(reviewer.locator(warning)).toContainText(term);
  expect(latest.non_conformant_items.map(item => item.value)).toEqual([term]);

  // Then A can download the reviewed bytes even while offline, and refreshing loads B's revision.
  await context.setOffline(true);
  expect(await uiDownload(page)).toEqual(['Breast Cancer',correction,'Diabetes']);
  await expect(page.locator(warning)).toBeHidden();
  await context.setOffline(false);
  const refreshed = await summary(page, () => page.reload());
  expect(refreshed.non_conformant_items.map(item => item.value)).toEqual([term]);
  await expect(page.locator(warning)).toContainText(term);
  await page.click('#conformanceProceedButton');
  expect(await uiDownload(page)).toEqual(['Breast Cancer',term,'Diabetes']);
});

for (const target of ['forward', 'failed save', 'tracker']) {
  test('9. ' + target + ' preserves the last edit', async ({page}) => {
    // Given Review is reachable from Verify, and the durable workflow contains the original decision.
    const id = await start(page);
    const before = await summary(page, () => page.click('#stageFiveButton'));
    expect(before.non_conformant_items.map(item => item.value)).toEqual([term]);
    await page.click('#conformanceProceedButton');
    await page.click(verifyLink);
    await expect(card(page).locator('.pv-combobox-link')).toHaveText(term);
    await expect(page.locator(reviewLink)).toHaveAttribute('aria-disabled','false');
    const attempts = [];
    page.on('request', r => { if(r.url().endsWith('/stage-4/overrides')) attempts.push(r.postDataJSON()); });
    if (target === 'failed save') {
      await page.route('**/stage-4/overrides', route => route.fulfill({status:503,contentType:'application/json',body:'{"detail":"temporary review outage"}'}), {times:1});
    }

    // When the reviewer selects a permissible value and immediately uses the available navigation control.
    await card(page).locator('.pv-combobox-link').click();
    const next = target === 'tracker' ? reviewLink : '#stageFiveButton';
    const completion = target === 'failed save' ? waitPost(page,'/stage-4/overrides') : waitPost(page,'/stage-5/review');
    await page.locator('.pv-selection-option[data-value="Carcinoma NOS"]').click();
    await expect(card(page).locator('.pv-combobox-link')).toHaveText(correction);
    await page.locator(next).click();
    let completed = await completion;
    if (target === 'failed save') {
      expect(completed.status()).toBe(503);
      await expect(page).toHaveURL(/stage-4/);
      await expect(card(page).locator('.pv-combobox-link')).toHaveText(correction);
      const retry = waitPost(page,'/stage-5/review');
      await page.click('#stageFiveButton');
      completed = await retry;
    }

    // Then the persisted row, the summary and downloaded cell must each contain the reviewer's decision.
    expect(completed.status()).toBe(200);
    const result = JSON.parse((await reviewParts(completed)).get('summary'));
    const r = await page.request.post('/stage-4/rows', {data:{file_id:id}});
    const saved = (await r.json()).reviewState?.overrides?.['2']?.col_0001?.human_value ?? null;
    if (result.non_conformant_items.length) await page.click('#conformanceProceedButton');
    const exported = await uiDownload(page);
    const actual = {saved,warnings:result.non_conformant_items.map(item=>item.value),exported};
    console.log('OBSERVED 9 ' + target + ' ' + JSON.stringify({...actual,saveAttempts:attempts.length}));
    expect(actual).toEqual({saved:correction,warnings:[],exported:['Breast Cancer',correction,'Diabetes']});
  });
}

for (const missing of [['manifest'], ['cde_mapping'], ['manifest', 'cde_mapping']]) {
  test(`17. a reviewer can download after losing ${missing.join(' and ')}`, async ({ page }, testInfo) => {
    // Given a saved manual correction and one or both audit inputs missing at the first visit to Review.
    const id = await start(page);
    const saved = waitPost(page, '/stage-4/overrides');
    await choose(page);
    expect((await saved).status()).toBe(200);
    const workflow = path.join(e2eEnv.DATA_CHORD_WORKFLOW_STORAGE_DIR, 'workflows', id);
    const auditInputs = { manifest: 'artifacts/harmonization_manifest_base.parquet', cde_mapping: 'json/cde_mapping.json' };
    await fs.mkdir(testInfo.outputDir, { recursive: true });
    for (const name of missing) {
      await fs.rename(path.join(workflow, auditInputs[name]), testInfo.outputPath(name));
    }

    // When the reviewer opens and refreshes the summary, the warning remains visible before downloading.
    await page.click('#stageFiveButton');
    const warningText = page.locator('#auditWarning');
    await expect(warningText).toContainText('run the workflow again');
    await page.reload();
    await expect(warningText).toContainText('run the workflow again');
    const message = await warningText.innerText();

    // Then the ZIP contains the corrected value, every available audit and the same warning as the page.
    const bytes = await downloadArchive(page);
    const archive = new AdmZip(bytes);
    const entries = archive.getEntries().map(entry => entry.entryName);
    expect(archive.readAsText('AUDIT-WARNING.txt').trim()).toBe(message.trim());
    for (const name of Object.keys(auditInputs)) {
      expect(entries.some(entry => entry.endsWith(`_${name}.json`))).toBe(!missing.includes(name));
      if (missing.includes(name)) expect(message).toContain(`_${name}.json`);
    }
    expect((await parseDownloadedCsv({ body: async () => bytes })).map(row => row.diagnosis))
      .toEqual(['Breast Cancer', correction, 'Diabetes']);
  });
}
