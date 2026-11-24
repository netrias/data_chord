// The uploaded file stays named in the step-instruction box on every stage.
import fs from 'node:fs';
import { test, expect } from '@playwright/test';

import { AGENT_FILE_INPUT, clickHarmonize, createWorkbookFixture, fileFixture, uploadAndAnalyzeReal } from './utils.mjs';

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
  // Given a long-named CSV that was uploaded and analyzed
  await uploadAndAnalyzeReal(page, longNamedCsv());
  await expect(fileName(page)).toHaveText(LONG_NAME);

  // When the user moves through Harmonize, Verify, and Review
  const namesByStage = {};
  await clickHarmonize(page);
  await expect(page.locator('#reviewButton')).toBeEnabled({ timeout: 20_000 });
  namesByStage.harmonize = await fileName(page).textContent();
  await page.click('#reviewButton');
  await page.waitForURL(/\/stage-4/);
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

  // Then the line adds the counted rows and columns for Patients
  await expect(sizeLine).toHaveText(`${sizeOnly} · 3 rows · 2 columns`);

  // When the user selects the sheet Analyze did not count
  await page.locator('.workbook-tab[data-sheet-name="Keep"]').click();

  // Then the line shows the size only, and the tracker card names that sheet without counts
  await expect(sizeLine).toHaveText(sizeOnly);
  await fileName(page).hover();
  const facts = await cardFacts(page);
  expect(Object.keys(facts)).toEqual(['Size', 'Uploaded', 'Sheet']);
  expect(facts.Sheet).toBe('Keep');

  // When the user returns to the existing Map stage without analyzing Keep
  await page.locator('.step[data-stage="mapping"] .step-link').click();
  await page.waitForURL(/\/stage-2/);
  await fileName(page).hover();

  // Then downstream details describe the analyzed Patients sheet
  const mappedFacts = await cardFacts(page);
  expect(mappedFacts.Sheet).toBe('Patients');
  expect(mappedFacts.Rows).toBe('3');
  expect(mappedFacts.Columns).toBe('2');
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
