import { test, expect } from '@playwright/test';

import { STAGE_THREE_FILE_ID } from './utils.mjs';

/* why: the spinner-or-canvas choice is made by CSS, which the jsdom unit tests do not load. */

const JOB_ID = 'job-loading-animation';

/** Answers every Stage 3 job poll with the given status, so the page stays in that state. */
const _mockJob = async (page, status) => {
  await page.route('**/stage-3/jobs/*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ job_id: JOB_ID, status, detail: 'Mocked job state.', job_id_available: true, elapsed_seconds: 180 }),
    });
  });
};
const _openStageThree = (page) => page.goto(`/stage-3?file_id=${STAGE_THREE_FILE_ID}&job_id=${JOB_ID}`);

const _canvas = (page) => page.locator('#loadingState canvas.harmonize-animation');
const _spinner = (page) => page.locator('#loadingState .loading-spinner--fallback');

test('Stage 3 shows the animation in place of the spinner while a job runs', async ({ page }) => {
  // Given a running harmonization job
  await _mockJob(page, 'running');

  // When Stage 3 loads with its animation script
  await _openStageThree(page);

  // Then: the loading panel shows the animation canvas and not the fallback spinner
  await expect(page.locator('#loadingState')).toBeVisible();
  await expect(_canvas(page)).toBeVisible();
  await expect(_spinner(page)).toBeHidden();
});

test('Stage 3 keeps the spinner when the animation script cannot load', async ({ page }) => {
  // Given a running job and a browser that prefers reduced motion and blocks the animation script
  await _mockJob(page, 'running');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.route('**/harmonize-animation.js*', (route) => route.abort());

  // When Stage 3 loads and polls the job
  await _openStageThree(page);

  // Then the poll updates progress and the fallback stays visible without motion
  await expect(page.locator('#harmonizeProgressMessage')).toContainText('Still working after 3m 00s');
  await expect(_spinner(page)).toHaveCSS('animation-name', 'none');
  await expect(_spinner(page)).toBeVisible();
  await expect(_canvas(page)).toBeHidden();
});

test('Stage 3 hides the animation when the job has failed', async ({ page }) => {
  // Given a failed harmonization job
  await _mockJob(page, 'failed');

  // When Stage 3 loads and polls the job
  await _openStageThree(page);

  // Then it displays the failure and hides the animation
  await expect(page.locator('#stageThreeError')).toBeVisible();
  await expect(_canvas(page)).toBeHidden();
});

test('Stage 3 falls back to the spinner when a running canvas loses its context', async ({ page }) => {
  // Given the canvas is visible for a running job
  await _mockJob(page, 'running');
  await _openStageThree(page);
  await expect(_canvas(page)).toBeVisible();

  // When the canvas reports a lost context
  await _canvas(page).dispatchEvent('contextlost');

  // Then the spinner is visible and the broken canvas is hidden
  await expect(_spinner(page)).toBeVisible();
  await expect(_canvas(page)).toBeHidden();
});
