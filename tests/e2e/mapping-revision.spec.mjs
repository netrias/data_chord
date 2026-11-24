import fs from 'node:fs';
import path from 'node:path';
import { e2eEnv } from "./runtime-env.mjs";
import { test, expect } from '@playwright/test';
import { uploadAndAnalyzeReal, clickHarmonize, createWorkbookFixture } from './utils.mjs';

const state = (id, kind) => {
  const filename = path.join(e2eEnv.DATA_CHORD_WORKFLOW_STORAGE_DIR, 'workflows', id, 'json', kind + '.json');
  return fs.existsSync(filename) ? JSON.parse(fs.readFileSync(filename)) : null;
};

const csv = (label) => ({name: label + '.csv', mimeType:'text/csv', buffer:Buffer.from(label + '\nDiabetes\nHypertension\n')});
const col = (page) => page.locator('#mappingRows .mapping-row-col').first();

for (const warm of [false,true]) {
  test('2 URL identity ' + (warm?'previous workflow cached':'clean tab control'), async ({page,context}) => {
    // Given independently analyzed A and B, a user follows B's valid link from a clean tab or from A.
    const idA = await uploadAndAnalyzeReal(page, csv('source_for_A'));
    const pageB = await context.newPage();
    const idB = await uploadAndAnalyzeReal(pageB, csv('source_for_B'));
    const aBefore = state(idA,'workflow_state');
    const bBefore = state(idB,'workflow_state');
    expect(state(idA,'stage_three_job')).toBeNull();
    expect(state(idB,'stage_three_job')).toBeNull();
    const actor = warm ? page : await context.newPage();
    // When the user opens B's link and submits the mapping shown.
    await actor.goto(pageB.url());
    await expect(col(actor)).toBeVisible();
    const heading = (await col(actor).innerText()).trim();
    const requests = [];
    const statuses = [];
    actor.on('response',res=>{
      if (['/stage-2/choices','/stage-3/harmonize'].includes(new URL(res.url()).pathname)){
        requests.push(res.request().postDataJSON().file_id);
        statuses.push(res.status());
      }
    });
    await clickHarmonize(actor);
    await expect(actor.locator('#reviewButton')).toBeEnabled({timeout:20000});
    // Then B is the only workflow that can be changed or started by submitting B's page.
    const aAfter = state(idA,'workflow_state');
    const bAfter = state(idB,'workflow_state');
    const outcome = {
      heading, submissions:requests.map(id=>id===idA?'A':id===idB?'B':'unknown'),
      statuses, aChoicesChanged:JSON.stringify(aBefore)!==JSON.stringify(aAfter),
      bChoicesChanged:JSON.stringify(bBefore)!==JSON.stringify(bAfter),
      aJob:state(idA,'stage_three_job')?.status??null,bJob:state(idB,'stage_three_job')?.status??null,
    };
    console.log('FINDING2',warm?'warm':'clean',JSON.stringify(outcome));
    expect(outcome).toEqual({heading:'source_for_B',submissions:['B','B'],statuses:[200,200],aChoicesChanged:false,bChoicesChanged:true,aJob:null,bJob:'succeeded'});
  });
}

test('10 completed file reanalysis selects a new editable worksheet',async({page,context})=>{
  // Given a completed first worksheet, revisiting its mapping offers Verify and the existing job.
  const id=await uploadAndAnalyzeReal(page,createWorkbookFixture());
  const mapUrl=page.url();
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled({timeout:20000});
  const jobA=state(id,'stage_three_job');
  await page.goto(mapUrl);
  await expect(page.locator('#harmonizeButton')).toHaveText(/Verify/);
  await page.click('#harmonizeButton');
  await page.waitForURL(/stage-4/);
  const before=await page.request.post('/stage-4/rows',{data:{file_id:id}});
  expect(before.status()).toBe(200);
  // When the same tab reanalyzes the upload after selecting the second worksheet.
  await page.goto('/stage-1');
  await page.locator('.workbook-tab[data-sheet-name="Patients"]').click();
  await page.locator('#analyzeButton').click();
  await page.locator('.data-model-confirm-btn').click();
  await page.waitForURL(/stage-2/);
  await expect(col(page)).toContainText('col_a');
  const button = (await page.locator('#harmonizeButton').innerText()).trim();
  await page.click('#harmonizeButton');
  await page.waitForURL(/stage-[34]/);
  const destination=new URL(page.url()).pathname;
  if (destination === '/stage-3') await expect(page.locator('#reviewButton')).toBeEnabled({timeout:20000});
  const status=await page.request.post('/stage-4/rows',{data:{file_id:id}});
  const detail = status.ok()?null:(await status.json()).detail;
  // Then a reanalyzed plan must be editable and runnable; use a clean tab to check backend readiness separately.
  const fresh=await context.newPage();
  await fresh.goto(mapUrl);
  await expect(col(fresh)).toContainText('col_a');
  await expect(fresh.locator('#harmonizeButton')).toHaveText(/Harmonize/);
  await clickHarmonize(fresh);
  await expect(fresh.locator('#reviewButton')).toBeEnabled({timeout:20000});
  const audit=state(id,'cde_mapping');
  expect(audit.mappings.map(m=>m.source_column_name)).toContain('col_a');
  const outcome = {originalJob:jobA.status, unchangedRevisit:before.status(), button, destination, reviewStatus:status.status(),detail,cleanTabNewAudit:audit.mappings.map(m=>m.source_column_name)};
  console.log('FINDING10',JSON.stringify(outcome));
  expect(button).toMatch(/Harmonize/);
  expect(destination).toBe('/stage-3');
});

test('the same reviewer can retry Start after a temporary capacity failure', async ({ page }) => {
  // Given a successful confirmation with the first Start temporarily rejected.
  await uploadAndAnalyzeReal(page, csv('diagnosis'));
  await page.route('**/stage-3/harmonize', (route) => route.fulfill({
    status: 429, json: { detail: 'All workers are busy. Try again.' },
  }), { times: 1 });
  await page.locator('#harmonizeButton').click();
  await expect(page.locator('#harmonizeError')).toContainText('All workers are busy');

  // When the reviewer retries the same choices, the page submits the revision it just saved.
  await clickHarmonize(page);

  // Then the accepted job completes with the confirmed plan.
  await expect(page.locator('#reviewButton')).toBeEnabled({ timeout: 20_000 });
});
