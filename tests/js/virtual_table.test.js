import assert from 'node:assert/strict';
import { afterEach, beforeEach, describe, test } from 'node:test';
import { JSDOM } from 'jsdom';

import { mountFixedRowVirtualTable } from '../../src/stage_4_review_results/static/fixed_row_virtual_table.js';

const ROW_HEIGHT = 45;
const VIEWPORT_HEIGHT = ROW_HEIGHT * 10;

const _rows = (count) => Array.from(
  { length: count },
  (_, index) => `<tr data-row-index="${index}"><td>Row ${index + 1}</td><td>Value ${index + 1}</td></tr>`,
);

describe('virtual table', () => {
  let dom;
  let scrollElement;
  let contentElement;

  beforeEach(() => {
    dom = new JSDOM(`
      <div id="scroll-area">
        <table>
          <tbody id="content"></tbody>
        </table>
      </div>
    `, { pretendToBeVisual: true });
    scrollElement = dom.window.document.querySelector('#scroll-area');
    contentElement = dom.window.document.querySelector('#content');
    Object.defineProperty(scrollElement, 'clientHeight', { value: VIEWPORT_HEIGHT });
  });

  afterEach(() => {
    dom.window.close();
  });

  test('Given 100,000 rows, When the table mounts, Then the DOM contains only a bounded window', () => {
    // Given: a row set much larger than the visible viewport.
    const rows = _rows(100_000);
    let renderedRowCount = 0;

    // When: the virtual table mounts the row set.
    mountFixedRowVirtualTable({
      scrollElement,
      contentElement,
      rowCount: rows.length,
      renderRow: (index) => {
        renderedRowCount += 1;
        return rows[index];
      },
      columnCount: 2,
    });

    // Then: only the bounded first window is built and kept in the DOM.
    assert.equal(contentElement.querySelector('[data-row-index="0"]')?.textContent, 'Row 1Value 1');
    assert.ok(renderedRowCount < 50);
    assert.ok(contentElement.querySelectorAll('[data-row-index]').length < 50);
    assert.equal(contentElement.querySelectorAll('.virtual-table-spacer').length, 1);
  });

  test('Given a mounted 100,000-row table, When the user scrolls to the end, Then the last row renders in a bounded window', async () => {
    // Given: a large virtual table is mounted in a ten-row viewport.
    const rows = _rows(100_000);
    mountFixedRowVirtualTable({
      scrollElement,
      contentElement,
      rowCount: rows.length,
      renderRow: (index) => rows[index],
      columnCount: 2,
    });

    // When: the user scrolls to the end of the logical table.
    scrollElement.scrollTop = rows.length * ROW_HEIGHT;
    scrollElement.dispatchEvent(new dom.window.Event('scroll'));
    await new Promise((resolve) => dom.window.requestAnimationFrame(resolve));
    await new Promise((resolve) => dom.window.requestAnimationFrame(resolve));

    // Then: the last logical row is present and the DOM window remains bounded.
    assert.equal(
      contentElement.querySelector('[data-row-index="99999"]')?.textContent,
      'Row 100000Value 100000',
    );
    assert.ok(contentElement.querySelectorAll('[data-row-index]').length < 50);
  });

  test('Given no rows, When the table mounts, Then it shows one clear empty state', () => {
    // Given: the table has no rows to render.
    const rows = [];

    // When: the virtual table mounts the empty row set.
    mountFixedRowVirtualTable({
      scrollElement,
      contentElement,
      rowCount: rows.length,
      renderRow: (index) => rows[index],
      columnCount: 2,
    });

    // Then: one cell spans the table and explains that no rows are available.
    const emptyCell = contentElement.querySelector('.virtual-table-no-data td');
    assert.equal(emptyCell?.textContent, 'No rows to display.');
    assert.equal(emptyCell?.getAttribute('colspan'), '2');
  });
});
