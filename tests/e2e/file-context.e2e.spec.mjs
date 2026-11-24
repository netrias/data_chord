// The uploaded file stays named in the step-instruction box on every stage.
import fs from 'node:fs';
import path from 'node:path';
import { test, expect } from '@playwright/test';

import {
  AGENT_FILE_INPUT,
  clickHarmonize,
  createWorkbookFixture,
  fileFixture,
  mockHarmonizeSuccess,
  seedHarmonization,
  uploadAndAnalyze,
  uploadAndAnalyzeReal,
} from './utils.mjs';
import { e2eEnv } from './runtime-env.mjs';

const LONG_NAME = 'clinical_trial_participants_enrollment_export_with_a_very_long_descriptive_name_2026_final_v3.csv';
const longNamedCsv = () => ({
  name: LONG_NAME,
  mimeType: 'text/csv',
  buffer: fs.readFileSync(fileFixture('restore-original.csv')),
});

const fileLine = (page) => page.locator('#stepInstructionFile');
const fileName = (page) => page.locator('.step-instruction-file-name');
const fileCard = (page) => page.getByRole('tooltip').filter({ has: page.locator('.step-instruction-file-card-name') });

const cardFacts = (page) => page.locator('.step-instruction-file-facts').evaluate((list) => {
  const terms = [...list.querySelectorAll('dt')].map((term) => term.textContent);
  const details = [...list.querySelectorAll('dd')].map((detail) => detail.textContent);
  return Object.fromEntries(terms.map((term, index) => [term, details[index]]));
});

const boxSize = (page) => page.locator('#stepInstruction').evaluate((box) => {
  const bounds = box.getBoundingClientRect();
  return { width: bounds.width, height: bounds.height };
});

const sessionFile = (page) => page.evaluate(() => JSON.parse(sessionStorage.getItem('currentFileSession')));

test('the uploaded file name stays in the instruction box from Map to Review', async ({ page }) => {
  // Given a long-named CSV that was uploaded and analyzed, with a stored harmonization result
  // why: a mocked job and seeded result keep this test out of the server's shared job slots.
  await mockHarmonizeSuccess(page);
  const fileId = await uploadAndAnalyze(page, {
    name: LONG_NAME,
    mimeType: 'text/csv',
    buffer: fs.readFileSync(fileFixture('basic.csv')),
  });
  await expect(fileName(page)).toHaveText(LONG_NAME);
  seedHarmonization(fileId);

  // When the user moves through Harmonize, Verify, and Review
  const namesByStage = {};
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled();
  namesByStage.harmonize = await fileName(page).textContent();
  await page.click('#reviewButton');
  await page.waitForURL(/\/stage-4/);
  // Stage 4 wires its Review button only after its rows load.
  await expect(page.locator('.column-mode-grid, .row-mode-wrapper, .review-empty').first()).toBeVisible();
  await expect(fileLine(page)).toBeVisible();
  namesByStage.verify = await fileName(page).textContent();
  await page.click('#stageFiveButton');
  await page.waitForURL(/\/stage-5/);
  await expect(fileLine(page)).toBeVisible();
  namesByStage.review = await fileName(page).textContent();

  // Then every stage names the same uploaded file
  expect(namesByStage).toEqual({ harmonize: LONG_NAME, verify: LONG_NAME, review: LONG_NAME });
});

test('hover and keyboard focus show the file details card', async ({ page }) => {
  // Given an analyzed CSV on Stage 2 with its details card closed
  await uploadAndAnalyzeReal(page, longNamedCsv());
  const session = await sessionFile(page);
  await expect(fileCard(page)).toBeHidden();

  // When the pointer rests on the file name
  await fileName(page).hover();

  // Then the card names the full file and the facts Analyze counted
  await expect(fileCard(page)).toBeVisible();
  await expect(page.locator('.step-instruction-file-card-name')).toHaveText(LONG_NAME);
  const facts = await cardFacts(page);
  expect(facts).toEqual({
    Rows: '3',
    Columns: '2',
    Size: `${session.human_size} · CSV`,
    Uploaded: expect.stringMatching(/\d{4}.*\d{1,2}:\d{2}/),
  });

  // Given the pointer moves away and the card closes
  await page.mouse.move(1000, 600);
  await expect(fileCard(page)).toBeHidden();

  // When the keyboard moves focus from the instruction text to the file name
  await page.locator('.step-instruction-text').focus();
  await page.keyboard.press('Tab');

  // Then the focused file name opens the same card and describes itself with it
  await expect(fileName(page)).toBeFocused();
  await expect(fileCard(page)).toBeVisible();
  await expect(fileName(page)).toHaveAttribute('aria-describedby', 'stepInstructionFileCard');
});

test('without an uploaded file the instruction box shows only the instruction', async ({ page }) => {
  // Given a fresh tab with no file in the session
  await page.goto('/stage-1');
  expect(await sessionFile(page)).toBeNull();

  // When the Stage 1 instruction box renders
  await expect(page.locator('.step-instruction-text')).not.toHaveText('');

  // Then the file line is absent and the box is exactly the instruction text
  await expect(fileLine(page)).toBeHidden();
  const textBox = await page.locator('.step-instruction-text').boundingBox();
  const box = await boxSize(page);
  expect(box).toEqual({ width: textBox.width, height: textBox.height });
  await expect(page.locator('.step-instruction-text')).toHaveCSS('border-bottom-style', 'dashed');
});

for (const viewport of [{ width: 1280, height: 800 }, { width: 1100, height: 800 }]) {
  test(`a long file name stays on one line at ${viewport.width}px without widening the box`, async ({ page }) => {
    // Given the Stage 1 instruction box size before any upload
    await page.setViewportSize(viewport);
    await page.goto('/stage-1');
    await expect(page.locator('.step-instruction-text')).not.toHaveText('');
    const emptyBox = await boxSize(page);

    // When a file with a long name is uploaded
    await page.setInputFiles(AGENT_FILE_INPUT, longNamedCsv());
    await expect(fileName(page)).toHaveText(LONG_NAME);

    // Then the name is cut with an ellipsis on one line and the box keeps its width
    const line = await fileName(page).evaluate((name) => ({
      cut: name.scrollWidth > name.clientWidth,
      oneLine: name.getBoundingClientRect().height <= 18,
      overflow: getComputedStyle(name).textOverflow,
    }));
    expect(line).toEqual({ cut: true, oneLine: true, overflow: 'ellipsis' });
    const fileBox = await boxSize(page);
    expect(fileBox.width).toBe(emptyBox.width);
    expect(fileBox.height - emptyBox.height).toBeGreaterThan(0);
    expect(fileBox.height - emptyBox.height).toBeLessThanOrEqual(24);
  });
}

test('the upload widget shows Analyze counts for the analyzed sheet only', async ({ page }) => {
  // Given a two-sheet workbook uploaded with the Patients sheet selected
  await page.goto('/stage-1');
  await page.setInputFiles(AGENT_FILE_INPUT, createWorkbookFixture());
  await page.locator('.workbook-tab[data-sheet-name="Patients"]').click();
  const sizeLine = page.locator('#dropzoneFileSize');
  await expect(sizeLine).not.toHaveText('—');
  const sizeOnly = await sizeLine.textContent();
  expect(sizeOnly).not.toContain('rows');

  // When Patients is analyzed and the user returns to Stage 1
  await page.click('#analyzeButton');
  await page.locator('.data-model-confirm-btn').click();
  await page.waitForURL(/\/stage-2/);
  await expect(page.locator('#mappingRows .mapping-row').first()).toBeVisible();
  await page.locator('.step[data-stage="upload"] .step-link').click();
  await page.waitForURL(/\/stage-1/);

  // Then the line and the card add the counted rows and columns for Patients
  await expect(sizeLine).toHaveText(`${sizeOnly} · 3 rows · 2 columns`);
  await fileName(page).hover();
  const patientsFacts = await cardFacts(page);
  expect(patientsFacts).toEqual({
    Rows: '3',
    Columns: '2',
    Size: `${sizeOnly} · XLSX`,
    Uploaded: expect.stringMatching(/\d{4}.*\d{1,2}:\d{2}/),
    Sheet: 'Patients',
  });

  // When the user selects the sheet Analyze did not count
  await page.mouse.move(1000, 600);
  await page.locator('.workbook-tab[data-sheet-name="Keep"]').click();

  // Then the line shows the size only, and the card names that sheet with the same upload facts and no counts
  await expect(sizeLine).toHaveText(sizeOnly);
  await fileName(page).hover();
  expect(await cardFacts(page)).toEqual({
    Size: `${sizeOnly} · XLSX`,
    Uploaded: patientsFacts.Uploaded,
    Sheet: 'Keep',
  });

  // When the user returns to the existing Map stage without analyzing Keep
  await page.locator('.step[data-stage="mapping"] .step-link').click();
  await page.waitForURL(/\/stage-2/);
  await fileName(page).hover();

  // Then downstream details describe the analyzed Patients sheet, with the same upload facts
  expect(await cardFacts(page)).toEqual(patientsFacts);
});

test('the workbook sheet cannot change while Analyze is waiting', async ({ page }) => {
  // Given Patients is selected and the Analyze response is held
  await page.goto('/stage-1');
  await page.setInputFiles(AGENT_FILE_INPUT, createWorkbookFixture());
  const patients = page.getByRole('tab', { name: 'Patients' });
  const keep = page.getByRole('tab', { name: 'Keep' });
  await patients.click();
  const responseGate = Promise.withResolvers();
  await page.route('**/stage-1/analyze', async (route) => {
    await responseGate.promise;
    await route.continue();
  });
  await page.click('#analyzeButton');
  const requestPending = page.waitForRequest('**/stage-1/analyze');
  await page.locator('.data-model-confirm-btn').click();
  const request = await requestPending;
  expect(request.postDataJSON().sheet_name).toBe('Patients');

  try {
    // When the user tries to select Keep with the keyboard while Analyze is pending
    await patients.press('ArrowLeft');

    // Then Patients remains selected until its counts arrive
    await expect(patients).toHaveAttribute('aria-selected', 'true');
    await expect(keep).toHaveAttribute('aria-selected', 'false');
  } finally {
    responseGate.resolve();
  }

  await page.waitForURL(/\/stage-2/);
  await fileName(page).hover();
  const facts = await cardFacts(page);
  expect(facts.Sheet).toBe('Patients');
  expect(facts.Rows).toBe('3');
  expect(facts.Columns).toBe('2');
});

test('a new upload replaces the earlier file and its counts', async ({ page }) => {
  // Given file A analyzed, with its counts in the tracker card
  await uploadAndAnalyzeReal(page, longNamedCsv());
  await fileName(page).hover();
  expect(await cardFacts(page)).toMatchObject({ Rows: '3', Columns: '2' });
  await page.locator('.step[data-stage="upload"] .step-link').click();
  await page.waitForURL(/\/stage-1/);

  // When file B is uploaded on Stage 1
  await page.setInputFiles(AGENT_FILE_INPUT, fileFixture('basic.csv'));
  await expect(page.locator('#dropzoneFileStatus')).toHaveText('Uploaded');

  // Then the line names B and the card shows only B's upload facts
  await expect(fileName(page)).toHaveText('basic.csv');
  const fixtureSize = fs.statSync(fileFixture('basic.csv')).size;
  await page.mouse.move(1000, 600);
  await fileName(page).hover();
  expect(await cardFacts(page)).toEqual({
    Size: `${fixtureSize.toFixed(1)} B · CSV`,
    Uploaded: expect.stringMatching(/\d{4}.*\d{1,2}:\d{2}/),
  });
});

test('a page for another workflow does not name the stored file', async ({ page }) => {
  // Given file A analyzed and named on its own Stage 2 page
  const fileId = await uploadAndAnalyzeReal(page, longNamedCsv());
  await expect(fileLine(page)).toBeVisible();
  const otherFileId = 'f'.repeat(32);
  expect(otherFileId).not.toBe(fileId);

  // When the same tab opens a stage URL for a different workflow
  await page.goto(`/stage-2?file_id=${otherFileId}`);
  await expect(page.locator('.step-instruction-text')).not.toHaveText('');

  // Then the file line stays hidden while A's record is still in the session
  await expect(fileLine(page)).toBeHidden();
  expect((await sessionFile(page)).file_id).toBe(fileId);
});

test('each part of the instruction box opens only its own tooltip', async ({ page }) => {
  // Given an analyzed file on Stage 2 with both tooltips closed
  await uploadAndAnalyzeReal(page, longNamedCsv());
  const instructionTooltip = page.locator('.step-instruction-tooltip');
  await expect(instructionTooltip).toBeHidden();
  await expect(fileCard(page)).toBeHidden();

  // When the pointer rests on the file name
  await fileName(page).hover();

  // Then only the file card opens
  await expect(fileCard(page)).toBeVisible();
  await expect(instructionTooltip).toBeHidden();

  // When the pointer moves to the instruction text
  await page.locator('.step-instruction-text').hover();

  // Then only the instruction tooltip opens
  await expect(instructionTooltip).toBeVisible();
  await expect(fileCard(page)).toBeHidden();
});

test('reselecting a retired model restores the workbook name and recounts the same sheet', async ({ page }) => {
  // Given a saved workbook whose model version retires while Map is open
  const workbookName = LONG_NAME.replace(/\.csv$/, '.xlsx');
  await page.goto('/stage-1');
  await page.setInputFiles(AGENT_FILE_INPUT, {
    name: workbookName,
    mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    buffer: fs.readFileSync(createWorkbookFixture()),
  });
  await page.getByRole('tab', { name: 'Patients' }).click();
  await page.locator('#analyzeButton').click();
  await page.locator('.data-model-confirm-btn').click();
  await page.waitForURL(/\/stage-2/);
  const { file_id: fileId, uploaded_at: uploadedAt, human_size: size } = await sessionFile(page);
  const mappingRow = page.locator('#mappingRows .mapping-row').first();
  await expect(mappingRow).toBeVisible();
  const planPath = path.join(e2eEnv.DATA_CHORD_WORKFLOW_STORAGE_DIR, 'workflows', fileId, 'json', 'workflow_state.json');
  const plan = JSON.parse(fs.readFileSync(planPath, 'utf8'));
  expect(plan.selected_sheet).toBe('Patients');
  fs.writeFileSync(planPath, JSON.stringify({ ...plan, external_version_number: 'retired' }));

  // When the user opens a column's details after their browser file details were cleared
  await page.evaluate(() => sessionStorage.clear());
  await mappingRow.click();

  // Then Stage 1 restores the same filename, worksheet, and original upload time
  await expect(page.locator('#statusMessage')).toContainText('The previous model version is no longer available');
  await expect(page.getByRole('tab', { name: 'Patients' })).toHaveAttribute('aria-selected', 'true');
  await expect(fileName(page)).toHaveText(workbookName);
  await fileName(page).hover();
  await expect(fileCard(page)).toBeVisible();
  const restoredFacts = await cardFacts(page);
  expect(restoredFacts).toEqual({ Size: size + ' · XLSX', Uploaded: expect.any(String), Sheet: 'Patients' });
  expect((await sessionFile(page)).uploaded_at).toBe(uploadedAt);

  // When the user selects a current model version and runs Analyze
  await page.locator('#analyzeButton').click();
  await page.locator('.data-model-confirm-btn').click();

  // Then Map names the same workbook and shows the new counts for Patients
  await expect(page).toHaveURL('/stage-2?file_id=' + fileId + '&data_model_key=gc&external_version_number=11.0.4');
  await expect(fileName(page)).toHaveText(workbookName);
  await fileName(page).hover();
  expect(await cardFacts(page)).toEqual({ Rows: '3', Columns: '2', ...restoredFacts });

  // When the user goes back to Upload and reloads it after successful analysis.
  await page.goBack();
  await page.reload();

  // Then the recovery marker is gone and the upload still has its new counts.
  await expect(page).toHaveURL('/stage-1?file_id=' + fileId);
  await expect(page.locator('#statusMessage')).toBeEmpty();
  await expect(page.locator('#dropzoneFileSize')).toHaveText(size + ' · 3 rows · 2 columns');
});


test('hovering the filename closes a focused instruction tooltip', async ({ page }) => {
  // Given a file is selected and the instruction is focused by a click.
  await uploadAndAnalyzeReal(page, longNamedCsv());
  const instruction = page.locator('.step-instruction-text');
  const instructionTip = page.locator('#stepInstructionTooltip');
  await instruction.click();
  await expect(instruction).toBeFocused();
  await expect(instructionTip).toBeVisible();

  // When the pointer moves directly over the filename.
  await fileName(page).hover();

  // Then only the filename tooltip remains visible.
  await expect(fileCard(page)).toBeVisible();
  await expect(instructionTip).toBeHidden();
});

test('a rejected storage write does not keep the old worksheet counts visible', async ({ page }) => {
  // Given Patients has been analyzed and the user is back on Upload.
  await page.goto('/stage-1');
  await page.setInputFiles(AGENT_FILE_INPUT, createWorkbookFixture());
  await page.getByRole('tab', { name: 'Patients' }).click();
  await page.locator('#analyzeButton').click();
  await page.locator('.data-model-confirm-btn').click();
  await page.waitForURL(/\/stage-2/);
  await expect(page.locator('#mappingRows .mapping-row').first()).toBeVisible();
  await page.locator('[data-stage="upload"] .step-link').click();
  await page.waitForURL(/\/stage-1/);
  const sizeLine = page.locator('#dropzoneFileSize');
  const { human_size: size } = await sessionFile(page);
  await expect(sizeLine).toHaveText(`${size} · 3 rows · 2 columns`);
  await page.evaluate(() => {
    const setItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key === 'currentFileSession') throw new Error('Session storage is full');
      return setItem.call(this, key, value);
    };
  });

  // When the user switches to Keep and browser storage rejects the write.
  await page.getByRole('tab', { name: 'Keep' }).click();

  // Then both displays reflect the new, unanalyzed worksheet.
  await expect(sizeLine).toHaveText(size);
  await fileName(page).hover();
  expect(await cardFacts(page)).toEqual({
    Size: `${size} · XLSX`,
    Uploaded: expect.any(String),
    Sheet: 'Keep',
  });

  // When the new analysis succeeds but the tracker still cannot save browser metadata.
  await page.locator('#analyzeButton').click();
  await page.locator('.data-model-confirm-btn').click();
  await page.waitForURL(/\/stage-2/);

  // Then Map uses the new sheet and does not display the previous sheet's facts.
  await expect(page.locator('#mappingRows .mapping-row').first()).toContainText('status');
  await expect(fileLine(page)).toBeHidden();

});

for (const pathKind of ['bare', 'different workflow']) {
  test(`Stage 1 shows the stored upload only for its own URL or the ${pathKind} case`, async ({ page }) => {
    // Given an analyzed file whose browser session is available.
    const fileId = await uploadAndAnalyzeReal(page, longNamedCsv());
    await expect(fileName(page)).toHaveText(LONG_NAME);
    const differentFileId = 'f'.repeat(32);
    expect(fileId).not.toBe(differentFileId);

    // When the browser opens Stage 1 with this URL shape.
    await page.goto(pathKind === 'bare' ? '/stage-1' : `/stage-1?file_id=${differentFileId}`);

    // Then the dropzone and tracker agree on whether this page owns the uploaded file.
    if (pathKind === 'bare') {
      await expect(fileName(page)).toHaveText(LONG_NAME);
      await expect(page.locator('#dropzoneFileName')).toHaveText(LONG_NAME);
    } else {
      await expect(fileLine(page)).toBeHidden();
      await expect(page.locator('#analyzeButton')).toBeDisabled();
    }
  });
}

test('replacing a file keeps the new tracker name when storage refuses both changes', async ({ page }) => {
  // Given an analyzed file and session storage that can only be read.
  await uploadAndAnalyzeReal(page, longNamedCsv());
  await page.evaluate(() => sessionStorage.setItem('maxReachedStage', 'review'));
  await page.locator('[data-stage="upload"] .step-link').click();
  await page.waitForURL(/\/stage-1/);
  await expect(fileName(page)).toHaveText(LONG_NAME);
  await page.evaluate(() => {
    const fail = () => { throw new Error('Session storage is read-only'); };
    Storage.prototype.setItem = fail;
    Storage.prototype.removeItem = fail;
  });

  // When the user replaces the file through the real upload route.
  await page.setInputFiles(AGENT_FILE_INPUT, fileFixture('basic.csv'));
  await expect(page.locator('#dropzoneFileStatus')).toHaveText('Uploaded');

  // Then the page names the replacement and omits the previous counts and progress.
  for (const stage of ['mapping', 'harmonize', 'verify', 'review']) {
    await expect(page.locator(`[data-stage="${stage}"] .step-link`)).toHaveAttribute('aria-disabled', 'true');
  }
  await expect(fileName(page)).toHaveText('basic.csv');
  await fileName(page).hover();
  expect(await cardFacts(page)).toEqual({
    Size: `${fs.statSync(fileFixture('basic.csv')).size.toFixed(1)} B · CSV`,
    Uploaded: expect.any(String),
  });
});


test('the keyboard-focused filename tooltip wins over a stationary pointer', async ({ page }) => {
  // Given the pointer is over the focused instruction and its tooltip is open.
  await uploadAndAnalyzeReal(page, longNamedCsv());
  const instruction = page.locator('.step-instruction-text');
  const instructionTip = page.locator('#stepInstructionTooltip');
  await instruction.click();
  await expect(instructionTip).toBeVisible();

  // When the keyboard moves focus to the filename without moving the pointer.
  await page.keyboard.press('Tab');

  // Then the tooltip follows keyboard focus and the previous one is hidden.
  await expect(fileName(page)).toBeFocused();
  await expect(fileCard(page)).toBeVisible();
  await expect(instructionTip).toBeHidden();
});

test('canceling the replacement picker keeps the current upload', async ({ page }) => {
  // Given the current upload has completed Analyze.
  await uploadAndAnalyzeReal(page, longNamedCsv());
  await page.locator('[data-stage="upload"] .step-link').click();
  await page.waitForURL(/\/stage-1/);
  await expect(fileName(page)).toHaveText(LONG_NAME);

  // When the user opens the replacement picker and dismisses it without a selection.
  const chooser = page.waitForEvent('filechooser');
  await page.locator('#changeFileButton').click();
  await chooser;
  await page.locator('#fileInput').dispatchEvent('cancel');

  // Then both Upload and the tracker still identify the analyzed file.
  await expect(page.locator('#dropzoneFileName')).toHaveText(LONG_NAME);
  await expect(page.locator('#dropzoneFileSize')).toContainText('3 rows · 2 columns');
  await expect(fileName(page)).toHaveText(LONG_NAME);
});
