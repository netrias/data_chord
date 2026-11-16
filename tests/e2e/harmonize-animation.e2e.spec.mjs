import { test, expect } from '@playwright/test';

import { STAGE_THREE_FILE_ID } from './utils.mjs';

/* why: the spinner-or-canvas choice is made by CSS, which the jsdom unit tests do not load. */

const JOB_ID = 'job-loading-animation';

/** Answers every Stage 3 job poll with the given status, so the page stays in that state. */
const _openStageThree = async (page, status) => {
  await page.route('**/stage-3/jobs/*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ job_id: JOB_ID, status, detail: 'Mocked job state.', job_id_available: true }),
    });
  });
  await page.goto(`/stage-3?file_id=${STAGE_THREE_FILE_ID}&job_id=${JOB_ID}`);
};

const _canvas = (page) => page.locator('#loadingState canvas.harmonize-animation');
const _spinner = (page) => page.locator('#loadingState .loading-spinner--fallback');

test('Stage 3 shows the animation in place of the spinner while a job runs', async ({ page }) => {
  // Given: a harmonization job that is still running
  // When: Stage 3 loads with its animation script
  await _openStageThree(page, 'running');

  // Then: the loading panel shows the animation canvas and not the fallback spinner
  await expect(page.locator('#loadingState')).toBeVisible();
  await expect(_canvas(page)).toBeVisible();
  await expect(_spinner(page)).toBeHidden();
});

test('Stage 3 keeps the spinner when the animation script cannot load', async ({ page }) => {
  // Given: a running job in a browser that cannot fetch the animation script
  await page.route('**/harmonize-animation.js*', (route) => route.abort());

  // When: Stage 3 loads
  await _openStageThree(page, 'running');

  // Then: the loading panel shows the fallback spinner and not the canvas
  await expect(page.locator('#harmonizeProgressMessage')).toBeVisible();
  await expect(_spinner(page)).toBeVisible();
  await expect(_canvas(page)).toBeHidden();
});

test('Stage 3 hides the animation when the job has failed', async ({ page }) => {
  // Given: a harmonization job that has failed
  // When: Stage 3 loads and reports the failure
  await _openStageThree(page, 'failed');
  await expect(page.locator('#stageThreeError')).toBeVisible();

  // Then: the animation canvas is not shown
  await expect(_canvas(page)).toBeHidden();
});
