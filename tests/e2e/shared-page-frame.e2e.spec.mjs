import { test, expect } from '@playwright/test';

import { STAGE_THREE_FILE_ID, mockCompletedStageThreeJob, stageThreeColumn } from './utils.mjs';

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

test('workflow actions stay inside narrow desktop and phone viewports', async ({ page }) => {
  // Given: the shared workflow frame is shown at the reported desktop width and a phone width
  for (const width of [968, 768, 390]) {
    await page.setViewportSize({ width, height: 921 });

    // When: each stage renders its action beside or below the workflow steps
    for (const path of ['/stage-1', '/stage-2', '/stage-3', '/stage-4', '/stage-5']) {
      await page.goto(path);

      // Then: the action and the full document stay inside the viewport
      const action = await page.locator('.progress-tracker-action').boundingBox();
      expect(action).not.toBeNull();
      expect(action.x).toBeGreaterThanOrEqual(0);
      expect(action.x + action.width).toBeLessThanOrEqual(width);
      const stepsFit = await page.locator('.progress-track').evaluate((track) => {
        const bounds = track.getBoundingClientRect();
        return [...track.children].every((child) => {
          const childBounds = child.getBoundingClientRect();
          return childBounds.left >= bounds.left && childBounds.right <= bounds.right;
        });
      });
      expect(stepsFit).toBe(true);
    }
  }
});

const _mixedStageThreeColumn = () => stageThreeColumn({ harmonized: 37, matched: 9, unresolved: 6 });
const LONG_STANDARD = { label: 'Childhood Cancer Data Initiative Submission Data Model', version: 'v2.1.0' };

/** Baseline of an element's first line: an empty inline-block's bottom edge sits on the baseline of its line. */
const _firstBaseline = (locator) => locator.evaluate((element) => {
  const probe = document.createElement('span');
  probe.style.display = 'inline-block';
  element.prepend(probe);
  const baseline = probe.getBoundingClientRect().bottom;
  probe.remove();
  return baseline;
});

for (const width of [1440, 1024]) {
  test(`Stage 3 puts the dial left of a wrapping headline on the dial baseline at ${width}px`, async ({ page }) => {
    const jobId = `stage-three-layout-${width}`;
    // Given: a completed Stage 3 result with a long standard name at desktop width
    await page.setViewportSize({ width, height: 1000 });
    await mockCompletedStageThreeJob(page, jobId, [_mixedStageThreeColumn()], LONG_STANDARD);

    // When: the completion summary is laid out
    await page.goto(`/stage-3?file_id=${STAGE_THREE_FILE_ID}&job_id=${jobId}`);
    const groups = page.getByRole('list', { name: 'Unique value results' }).getByRole('listitem');
    await expect(groups).toHaveCount(3);
    const headline = page.locator('#stageThreeHeadline');
    await expect(headline).toHaveText(
      '88% of 52 unique values now match Childhood Cancer Data Initiative Submission Data Model v2.1.0',
    );
    const title = await page.getByRole('heading', { name: 'Harmonization complete' }).boundingBox();
    const dial = await page.locator('#stageThreeDial').boundingBox();
    const headlineBox = await headline.boundingBox();
    const verdict = await page.locator('.stage-three-verdict').boundingBox();
    const headlineLineHeight = await headline.evaluate((element) => parseFloat(getComputedStyle(element).lineHeight));
    const dialBaseline = await _firstBaseline(page.locator('#stageThreeDialValue'));
    const headlineBaseline = await _firstBaseline(headline);
    const groupBoxes = await Promise.all([0, 1, 2].map((index) => groups.nth(index).boundingBox()));

    // Then: the title is above a round dial, the long name wraps beside the dial, and its first line sits on the dial baseline
    expect(title.y + title.height).toBeLessThanOrEqual(dial.y);
    expect(Math.abs(dial.width - dial.height)).toBeLessThanOrEqual(1);
    expect(dial.x + dial.width).toBeLessThan(headlineBox.x);
    expect(headlineBox.height).toBeGreaterThan(headlineLineHeight * 1.5);
    expect(headlineBox.x + headlineBox.width).toBeLessThanOrEqual(verdict.x + verdict.width + 1);
    expect(Math.abs(headlineBaseline - dialBaseline)).toBeLessThanOrEqual(1);
    // And: the three groups share one row below the headline with equal widths
    expect(headlineBox.x).toBeLessThanOrEqual(groupBoxes[0].x + 1);
    expect(headlineBox.y + headlineBox.height).toBeLessThan(groupBoxes[0].y);
    for (const box of groupBoxes) {
      expect(Math.abs(box.y - groupBoxes[0].y)).toBeLessThanOrEqual(1);
      expect(Math.abs(box.width - groupBoxes[0].width)).toBeLessThanOrEqual(1);
    }
    expect(groupBoxes[0].x + groupBoxes[0].width).toBeLessThanOrEqual(groupBoxes[1].x);
    expect(groupBoxes[1].x + groupBoxes[1].width).toBeLessThanOrEqual(groupBoxes[2].x);
  });
}

test('Stage 3 result summary stacks inside a phone viewport', async ({ page }) => {
  const jobId = 'stage-three-phone-layout';
  // Given: a completed Stage 3 result with a long standard name at phone width
  await page.setViewportSize({ width: 390, height: 921 });
  await mockCompletedStageThreeJob(page, jobId, [_mixedStageThreeColumn()], LONG_STANDARD);

  // When: the completion summary is laid out
  await page.goto(`/stage-3?file_id=${STAGE_THREE_FILE_ID}&job_id=${jobId}`);
  const groups = page.getByRole('list', { name: 'Unique value results' }).getByRole('listitem');
  await expect(groups).toHaveCount(3);

  // Then: the title, dial, and headline stack in reading order, groups stack, and nothing leaves the viewport
  const title = await page.getByRole('heading', { name: 'Harmonization complete' }).boundingBox();
  const dial = await page.locator('#stageThreeDial').boundingBox();
  const headline = await page.locator('#stageThreeHeadline').boundingBox();
  expect(title.y + title.height).toBeLessThanOrEqual(dial.y);
  expect(dial.y + dial.height).toBeLessThanOrEqual(headline.y);
  await expect(page.locator('#stageThreeHeadline')).toHaveCSS('text-align', 'center');
  const first = await groups.nth(0).boundingBox();
  const second = await groups.nth(1).boundingBox();
  expect(headline.y + headline.height).toBeLessThanOrEqual(first.y);
  expect(first.y + first.height).toBeLessThanOrEqual(second.y);
  const panel = await page.locator('#stageThreeComplete').boundingBox();
  const summary = await page.locator('.stage-three-summary').boundingBox();
  expect(panel.x).toBeGreaterThanOrEqual(0);
  expect(panel.x + panel.width).toBeLessThanOrEqual(390);
  expect(summary.x + summary.width).toBeLessThanOrEqual(panel.x + panel.width + 1);
  expect(headline.x).toBeGreaterThanOrEqual(panel.x);
  expect(headline.x + headline.width).toBeLessThanOrEqual(panel.x + panel.width + 1);
});

const UNBROKEN_STANDARD = { label: 'CCDI_Submission_Data_Model_v2.1.0_final_release_candidate', version: null };

for (const width of [1024, 390]) {
  test(`Stage 3 wraps a standard name with no spaces inside the card at ${width}px`, async ({ page }) => {
    const jobId = `stage-three-unbroken-name-${width}`;
    // Given: a completed Stage 3 result whose standard name has no spaces to break at
    await page.setViewportSize({ width, height: 1000 });
    await mockCompletedStageThreeJob(page, jobId, [_mixedStageThreeColumn()], UNBROKEN_STANDARD);

    // When: the completion summary is laid out
    await page.goto(`/stage-3?file_id=${STAGE_THREE_FILE_ID}&job_id=${jobId}`);
    const headline = page.locator('#stageThreeHeadline');
    await expect(headline).toHaveText(`88% of 52 unique values now match ${UNBROKEN_STANDARD.label}`);
    const headlineBox = await headline.boundingBox();
    /* why: the summary and verdict grow with an unbreakable name; the completion panel keeps the card width. */
    const panel = await page.locator('#stageThreeComplete').boundingBox();

    // Then: the name breaks inside the card instead of widening the summary past it
    expect(headlineBox.x).toBeGreaterThanOrEqual(panel.x);
    expect(headlineBox.x + headlineBox.width).toBeLessThanOrEqual(panel.x + panel.width + 1);
  });
}

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

test('Stage 1 uses its valid session workflow when Map is selected', async ({ page }) => {
  const fileId = '0123456789abcdef0123456789abcdef';

  // Given: Stage 1 has created a valid workflow and made Map reachable
  await page.goto('/stage-1');
  await page.evaluate((activeFileId) => {
    sessionStorage.setItem('currentFileSession', JSON.stringify({ file_id: activeFileId }));
    sessionStorage.setItem('maxReachedStage', 'mapping');
  }, fileId);
  await page.reload();

  // When: the user selects Map from the workflow tracker
  await page.locator('.step[data-stage="mapping"] .step-link').click();

  // Then: Stage 2 receives the workflow created on Stage 1
  await expect(page).toHaveURL(`/stage-2?file_id=${fileId}`);
});

test('later stages do not replace an invalid URL workflow from session state', async ({ page }) => {
  const sessionFileId = 'fedcba9876543210fedcba9876543210';

  // Given: Stage 4 has an invalid URL identity and a different session workflow
  await page.goto('/stage-1');
  await page.evaluate((activeFileId) => {
    sessionStorage.setItem('currentFileSession', JSON.stringify({ file_id: activeFileId }));
    sessionStorage.setItem('maxReachedStage', 'review');
  }, sessionFileId);
  await page.goto('/stage-4?file_id=not-a-workflow');

  // When: the user selects Review from the workflow tracker
  await page.locator('.step[data-stage="review"] .step-link').click();

  // Then: navigation does not select either the invalid or unrelated workflow
  await expect(page).toHaveURL('/stage-5');
  await expect(page.locator('#summaryGrid')).toContainText('Unable to locate this harmonization workflow.');
});

test('later stages reject duplicate workflow ids', async ({ page }) => {
  const firstFileId = '0123456789abcdef0123456789abcdef';
  const secondFileId = 'fedcba9876543210fedcba9876543210';

  // Given: Stage 4 receives two different workflow identities
  await page.goto('/stage-1');
  await page.evaluate(() => sessionStorage.setItem('maxReachedStage', 'review'));
  await page.goto(`/stage-4?file_id=${firstFileId}&file_id=${secondFileId}`);

  // When: the user selects Review from the workflow tracker
  await page.locator('.step[data-stage="review"] .step-link').click();

  // Then: navigation does not select either ambiguous workflow
  await expect(page).toHaveURL('/stage-5');
  await expect(page.locator('#summaryGrid')).toContainText('Unable to locate this harmonization workflow.');
});
