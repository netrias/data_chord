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

/** Proves the page has exactly one visible heading, with this level and name. */
const _expectOnlyHeading = async (page, level, name) => {
  await expect(page.getByRole('heading')).toHaveCount(1);
  await expect(page.getByRole('heading', { level, name, exact: true })).toBeVisible();
};

/** Elapsed seconds of the job the page last rendered; the page stores it in the same step that updates the progress text. */
const _storedElapsedSeconds = (page) => page.evaluate(
  () => JSON.parse(sessionStorage.getItem('stage3HarmonizeJob') ?? 'null')?.elapsed_seconds ?? null,
);

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
  // and the loading headline is the only visible heading, so the stage title does not repeat it
  await expect(page.locator('#stageThreeHeader')).toBeHidden();
  await _expectOnlyHeading(page, 1, 'Harmonizing your dataset');
});

test('Stage 3 keeps the spinner when the animation script cannot load', async ({ page }) => {
  // Given a running job and a browser that prefers reduced motion and blocks the animation script
  await _mockJob(page, 'running');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.route('**/harmonize-animation.js*', (route) => route.abort());

  // When Stage 3 loads and polls the job
  await _openStageThree(page);

  // Then the poll updates progress and the fallback stays visible without motion
  await expect(page.locator('#harmonizeProgressMessage')).toHaveText('Still working after 3 min. Larger datasets can take several minutes.');
  await expect(_spinner(page)).toHaveCSS('animation-name', 'none');
  await expect(_spinner(page)).toBeVisible();
  await expect(_canvas(page)).toBeHidden();
});

test('Stage 3 shows only the failure, not the loading panel, when the job has failed', async ({ page }) => {
  // Given a failed harmonization job
  await _mockJob(page, 'failed');

  // When Stage 3 loads and polls the job
  await _openStageThree(page);

  // Then it shows the failure title and error, and hides the whole loading panel with its time estimate
  await expect(page.locator('#stageThreeError')).toBeVisible();
  await expect(page.locator('#stageThreeError')).toHaveText('Mocked job state.');
  await _expectOnlyHeading(page, 2, 'Harmonization Failed');
  await expect(page.locator('#loadingState')).toBeHidden();
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

test('Stage 3 leaves the progress text alone when a poll is still in the same minute', async ({ page }) => {
  // Given a running job whose elapsed time grows by 5 seconds on each poll, starting at 3 min,
  // and later polls held until the test is watching the progress text
  let polls = 0;
  let releaseLaterPolls;
  const observerReady = new Promise((resolve) => { releaseLaterPolls = resolve; });
  await page.route('**/stage-3/jobs/*', async (route) => {
    const elapsed = 180 + 5 * polls;
    polls += 1;
    if (elapsed > 180) await observerReady;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ job_id: JOB_ID, status: 'running', job_id_available: true, elapsed_seconds: elapsed }),
    });
  });
  await _openStageThree(page);
  const message = page.locator('#harmonizeProgressMessage');
  await expect(message).toHaveText('Still working after 3 min. Larger datasets can take several minutes.');
  await message.evaluate((node) => {
    window.progressMutations = 0;
    new MutationObserver((records) => { window.progressMutations += records.length; })
      .observe(node, { childList: true, characterData: true, subtree: true });
  });

  // When the next poll is released and the page has rendered it, which answers with 3 min 5 s
  releaseLaterPolls();
  await expect.poll(() => _storedElapsedSeconds(page), { timeout: 10_000 }).toBe(185);

  // Then the live-region text was not rewritten, so screen readers have nothing new to announce
  expect(await page.evaluate(() => window.progressMutations)).toBe(0);
  await expect(message).toHaveText('Still working after 3 min. Larger datasets can take several minutes.');
});

test('Stage 3 shows only the empty-state heading when there is no job', async ({ page }) => {
  // Given a Stage 3 URL with no file or job

  // When Stage 3 loads
  await page.goto('/stage-3');

  // Then the empty-state message is the only visible heading, without the "Harmonizing" title
  await expect(page.locator('#stageThreeEmptyState')).toBeVisible();
  await _expectOnlyHeading(page, 1, 'We need a harmonization job');
});

test('Stage 3 resets the progress text when Retry starts a new job', async ({ page }) => {
  // Given a job that ran for 12 min and then failed, and a new start request that stays pending
  let polls = 0;
  await page.route('**/stage-3/jobs/*', async (route) => {
    const status = polls === 0 ? 'running' : 'failed';
    polls += 1;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ job_id: JOB_ID, status, detail: 'Mocked job state.', job_id_available: true, elapsed_seconds: 720 }),
    });
  });
  let releaseStart;
  const startHeld = new Promise((resolve) => { releaseStart = resolve; });
  await page.route('**/stage-3/harmonize', async (route) => {
    await startHeld;
    await route.abort();
  });
  await _openStageThree(page);
  const message = page.locator('#harmonizeProgressMessage');
  await expect(message).toHaveText('Still running after 12 min. Large datasets can take a while; keep this tab open.');
  const retry = page.locator('#retryButton');
  await expect(retry).toBeVisible({ timeout: 10_000 });

  // When the user clicks Retry and the new job has not started yet
  await retry.click();

  // Then the loading panel shows the default estimate, not the old job's elapsed time
  await expect(page.locator('#loadingState')).toBeVisible();
  await expect(message).toHaveText('This usually takes 1–2 minutes.');
  releaseStart();
});
