import { expect, test } from '@playwright/test';

test.skip(
  process.env.RUN_VIRTUAL_TABLE_PERF !== 'true',
  'Set RUN_VIRTUAL_TABLE_PERF=true to run the virtual-table benchmark.',
);

const _runVirtualTableBenchmark = async (page) => page.evaluate(async () => {
  const { mountFixedRowVirtualTable } = await import('/assets/stage-4/fixed_row_virtual_table.js');
  const style = document.createElement('style');
  style.textContent = `
    #benchmark-scroll { height: 450px; overflow: auto; }
    #benchmark-table { border-collapse: collapse; }
    #benchmark-table td { box-sizing: border-box; height: 45px; padding: 12px 16px; white-space: nowrap; }
    .virtual-table-spacer td { padding: 0 !important; border: 0 !important; }
  `;
  document.head.appendChild(style);
  document.body.innerHTML = `
    <div id="benchmark-scroll">
      <table id="benchmark-table"><tbody id="benchmark-content"></tbody></table>
    </div>
  `;

  const preparationStarted = performance.now();
  const rows = Array.from(
    { length: 100_000 },
    (_, rowIndex) => Array.from(
      { length: 10 },
      (_, columnIndex) => `row-${rowIndex + 1}-column-${columnIndex + 1}`,
    ),
  );
  const preparationMs = performance.now() - preparationStarted;

  const scrollElement = document.querySelector('#benchmark-scroll');
  const contentElement = document.querySelector('#benchmark-content');
  const mountStarted = performance.now();
  const virtualTable = mountFixedRowVirtualTable({
    scrollElement,
    contentElement,
    rowCount: rows.length,
    renderRow: (rowIndex) => {
      const cells = rows[rowIndex].map((value) => `<td>${value}</td>`);
      return `<tr>${cells.join('')}</tr>`;
    },
    columnCount: 10,
  });
  const mountMs = performance.now() - mountStarted;

  const scrollDurations = [];
  let maximumRenderedRows = 0;
  for (let step = 1; step <= 20; step += 1) {
    const scrollStarted = performance.now();
    scrollElement.scrollTop = scrollElement.scrollHeight * (step / 20);
    scrollElement.dispatchEvent(new Event('scroll'));
    await new Promise((resolve) => requestAnimationFrame(resolve));
    scrollDurations.push(performance.now() - scrollStarted);
    maximumRenderedRows = Math.max(
      maximumRenderedRows,
      contentElement.querySelectorAll('[data-virtual-row]').length,
    );
  }
  const sortedScrollDurations = [...scrollDurations].sort((left, right) => left - right);
  const medianScrollMs = sortedScrollDurations[Math.floor(sortedScrollDurations.length / 2)];
  const p95ScrollMs = sortedScrollDurations[Math.ceil(sortedScrollDurations.length * 0.95) - 1];

  const measurement = {
    preparationMs: Math.round(preparationMs),
    mountMs: Math.round(mountMs),
    medianScrollMs: Math.round(medianScrollMs),
    p95ScrollMs: Math.round(p95ScrollMs),
    maximumRenderedRows,
    lastRowVisible: contentElement.querySelector('[data-virtual-row="99999"]') !== null,
  };
  virtualTable.destroy();
  return measurement;
});

test('virtual table keeps a 100,000-row dataset responsive and the DOM bounded', async ({ page }) => {
  // Given: a browser page can load the production virtual-table module.
  await page.goto('/stage-1');

  // When: the complete 100,000-row benchmark runs.
  const result = await _runVirtualTableBenchmark(page);

  // Then: start and end rendering use a small DOM window; timings remain informational.
  console.log(`Virtual-table benchmark: ${JSON.stringify(result)}`);
  expect(result.maximumRenderedRows).toBeLessThan(50);
  expect(result.lastRowVisible).toBe(true);
});
