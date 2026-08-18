import { expect, test } from '@playwright/test';

const _mountTable = async (page, rowCount = 1_000) => {
  await page.evaluate(async (count) => {
    const { mountFixedRowVirtualTable } = await import('/assets/stage-4/fixed_row_virtual_table.js');
    const style = document.createElement('style');
    style.textContent = `
      #test-scroll { height: 50vh; overflow: auto; }
      #test-table { border-collapse: collapse; }
      #test-table td { box-sizing: border-box; height: 45px; padding: 12px 16px; white-space: nowrap; }
      .virtual-table-spacer td { padding: 0 !important; border: 0 !important; }
    `;
    document.head.appendChild(style);
    document.body.innerHTML = `
      <div id="test-scroll" tabindex="0" aria-label="Scrollable row context">
        <table id="test-table">
          <thead><tr><th>Row</th><th>Value</th></tr></thead>
          <tbody id="test-content"></tbody>
        </table>
      </div>
    `;
    const scrollElement = document.querySelector('#test-scroll');
    const contentElement = document.querySelector('#test-content');
    window.testVirtualTable = mountFixedRowVirtualTable({
      scrollElement,
      contentElement,
      rowCount: count,
      renderRow: (index) => `<tr><td>${index + 1}</td><td>Value ${index + 1}</td></tr>`,
      columnCount: 2,
    });
  }, rowCount);
};

test('mount renders the first logical rows without growing the DOM', async ({ page }) => {
  // Given: a browser viewport is ready for a 1,000-row table.
  await page.goto('/stage-1');

  // When: the fixed-row virtual table mounts.
  await _mountTable(page);

  // Then: the first row is exposed with table position metadata in a bounded DOM window.
  await expect(page.locator('[data-virtual-row="0"]')).toContainText('Value 1');
  await expect(page.locator('#test-table')).toHaveAttribute('aria-rowcount', '1001');
  expect(await page.locator('[data-virtual-row]').count()).toBeLessThan(50);
});

test('bottom scroll renders the last logical row without growing the DOM', async ({ page }) => {
  // Given: a 1,000-row fixed-height virtual table is mounted.
  await page.goto('/stage-1');
  await _mountTable(page);

  // When: the user scrolls to the end of the table.
  await page.locator('#test-scroll').evaluate(async (scrollElement) => {
    scrollElement.scrollTop = scrollElement.scrollHeight;
    scrollElement.dispatchEvent(new Event('scroll'));
    await new Promise((resolve) => requestAnimationFrame(resolve));
    await new Promise((resolve) => requestAnimationFrame(resolve));
  });

  // Then: row 1,000 is visible with its logical position and the DOM remains bounded.
  await expect(page.locator('[data-virtual-row="999"]')).toContainText('Value 1000');
  await expect(page.locator('[data-virtual-row="999"]')).toHaveAttribute('aria-rowindex', '1001');
  expect(await page.locator('[data-virtual-row]').count()).toBeLessThan(50);
});

test('destroy cancels a queued scroll render', async ({ page }) => {
  // Given: a mounted table has recorded its initial DOM window.
  await page.goto('/stage-1');
  await _mountTable(page);
  const initialRows = await page.locator('#test-content').innerHTML();

  // When: the table is destroyed while a scroll render is queued.
  await page.evaluate(() => {
    const scrollElement = document.querySelector('#test-scroll');
    scrollElement.scrollTop = scrollElement.scrollHeight;
    scrollElement.dispatchEvent(new Event('scroll'));
    window.testVirtualTable.destroy();
  });

  // Then: the queued work does not replace the original DOM window.
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(resolve)));
  expect(await page.locator('#test-content').innerHTML()).toBe(initialRows);
});

test('desktop resize fills the newly visible table area', async ({ page }) => {
  // Given: a virtual table is mounted in the default desktop viewport.
  await page.goto('/stage-1');
  await _mountTable(page);
  const initialRowCount = await page.locator('[data-virtual-row]').count();

  // When: the desktop viewport becomes taller.
  await page.setViewportSize({ width: 1280, height: 1_200 });

  // Then: the table adds enough rows for the larger viewport but stays bounded.
  await expect.poll(() => page.locator('[data-virtual-row]').count()).toBeGreaterThan(initialRowCount);
  expect(await page.locator('[data-virtual-row]').count()).toBeLessThan(50);
});
