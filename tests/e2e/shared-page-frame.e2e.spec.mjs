import { test, expect } from '@playwright/test';

test('late help text does not move the workflow steps', async ({ page }) => {
  // Given the Stage 1 frame after its help text is populated
  await page.goto('/stage-1');
  const helpText = page.locator('.step-instruction-text');
  await expect(helpText).not.toHaveText('');
  const populatedSteps = await page.locator('.progress-track').boundingBox();
  expect(populatedSteps).not.toBeNull();

  // When the help slot is temporarily empty, as it is before JavaScript starts
  await helpText.evaluate((element) => { element.textContent = ''; });

  // Then the workflow steps keep the same position and width
  const emptySteps = await page.locator('.progress-track').boundingBox();
  expect(emptySteps).not.toBeNull();
  expect(emptySteps.x).toBe(populatedSteps.x);
  expect(emptySteps.width).toBe(populatedSteps.width);
});

test('workflow steps stay aligned through all five stages', async ({ page }) => {
  // Given the workflow-step position on Stage 1
  await page.goto('/stage-1');
  const firstStageSteps = await page.locator('.progress-track').boundingBox();
  expect(firstStageSteps).not.toBeNull();

  // When each later stage renders its own action in the shared frame
  const laterStageBoxes = [];
  for (const path of ['/stage-2', '/stage-3', '/stage-4', '/stage-5']) {
    await page.goto(path);
    laterStageBoxes.push(await page.locator('.progress-track').boundingBox());
  }

  // Then the workflow steps keep one stable position and width
  for (const stageSteps of laterStageBoxes) {
    expect(stageSteps).not.toBeNull();
    expect(stageSteps.x).toBe(firstStageSteps.x);
    expect(stageSteps.width).toBe(firstStageSteps.width);
  }
});

test('workflow links identify the current step and support keyboard navigation', async ({ page }) => {
  // Given Stage 2 identifies Map as the current step
  await page.goto('/stage-2');
  const currentStep = page.locator('.step-link[aria-current="step"]');
  await expect(currentStep).toHaveText(/Map/);

  // When the user focuses the earlier Upload step and presses Enter
  const uploadLink = page.locator('.step[data-stage="upload"] .step-link');
  await uploadLink.focus();
  await uploadLink.press('Enter');

  // Then native link navigation returns to Stage 1
  await expect(page).toHaveURL(/\/stage-1$/);
});

test('future workflow links stay disabled when the shared script cannot start', async ({ page }) => {
  // Given the shared progress script is unavailable on Stage 1
  await page.route('**/assets/shared/step-instruction-ui.js*', (route) => route.abort());
  await page.goto('/stage-1');
  const reviewLink = page.locator('.step[data-stage="review"] .step-link');
  await expect(reviewLink).not.toHaveAttribute('href');
  await expect(reviewLink).toHaveAttribute('aria-disabled', 'true');
  await expect(reviewLink).toHaveAttribute('tabindex', '-1');

  // When the user tries the future Review step
  await reviewLink.click();

  // Then the page stays on Stage 1
  await expect(page).toHaveURL(/\/stage-1$/);
});
