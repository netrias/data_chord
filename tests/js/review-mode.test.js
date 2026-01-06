/**
 * Review Mode Tests
 *
 * Tests for Stage 4 review mode logic including:
 * - getTotalUnits consistency between column and row modes
 * - rowHasChanges and getChangedCells change detection
 * - getCurrentEntries boundary handling
 */

import { describe, it } from 'node:test';
import assert from 'node:assert';
import { pathToFileURL } from 'node:url';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));

const sharedUtilsPath = join(__dirname, '../../src/stage_4_review_results/static/shared_review_utils.js');
const columnModePath = join(__dirname, '../../src/stage_4_review_results/static/review_mode_column.js');
const rowModePath = join(__dirname, '../../src/stage_4_review_results/static/review_mode_row.js');

const { rowHasChanges, getChangedCells } = await import(pathToFileURL(sharedUtilsPath).href);
const { getTotalUnits: getColumnTotalUnits, getCurrentEntries: getColumnCurrentEntries } = await import(pathToFileURL(columnModePath).href);
const { getTotalUnits: getRowTotalUnits, getCurrentEntries: getRowCurrentEntries } = await import(pathToFileURL(rowModePath).href);

/* Test data factories */
const createCell = (original, harmonized, columnKey = 'col1') => ({
  columnKey,
  columnLabel: columnKey.replace('_', ' '),
  originalValue: original,
  harmonizedValue: harmonized,
  confidence: 0.9,
  bucket: 'high',
  isChanged: original !== harmonized,
});

const createRow = (cells, rowNumber = 1) => ({
  rowNumber,
  sourceRowNumber: rowNumber,
  recordId: `record-${rowNumber}`,
  cells,
});

const createChangedRow = (rowNumber = 1) => createRow([
  createCell('original', 'harmonized', 'col1'),
], rowNumber);

const createUnchangedRow = (rowNumber = 1) => createRow([
  createCell('same', 'same', 'col1'),
], rowNumber);

describe('rowHasChanges', () => {
  describe('detects changes correctly', () => {
    it('returns true when originalValue differs from harmonizedValue', () => {
      const row = createChangedRow();
      assert.strictEqual(rowHasChanges(row), true);
    });

    it('returns false when all cells have matching values', () => {
      const row = createUnchangedRow();
      assert.strictEqual(rowHasChanges(row), false);
    });

    it('returns true if any cell has a change', () => {
      const row = createRow([
        createCell('same', 'same', 'col1'),
        createCell('original', 'harmonized', 'col2'),
      ]);
      assert.strictEqual(rowHasChanges(row), true);
    });
  });

  describe('handles edge cases', () => {
    it('returns false for null row', () => {
      assert.strictEqual(rowHasChanges(null), false);
    });

    it('returns false for undefined row', () => {
      assert.strictEqual(rowHasChanges(undefined), false);
    });

    it('returns false for row without cells', () => {
      assert.strictEqual(rowHasChanges({ rowNumber: 1 }), false);
    });

    it('returns false for row with empty cells array', () => {
      assert.strictEqual(rowHasChanges({ cells: [] }), false);
    });

    it('treats whitespace differences as changes (whitespace is semantically significant)', () => {
      const row = createRow([createCell('value', 'value ')]);
      assert.strictEqual(rowHasChanges(row), true);
    });

    it('treats null harmonizedValue as different from non-null original', () => {
      const row = createRow([createCell('original', null)]);
      assert.strictEqual(rowHasChanges(row), true);
    });
  });
});

describe('getChangedCells', () => {
  it('returns only cells where original differs from harmonized', () => {
    const row = createRow([
      createCell('same', 'same', 'col1'),
      createCell('original', 'harmonized', 'col2'),
      createCell('also-same', 'also-same', 'col3'),
    ]);

    const changed = getChangedCells(row);
    assert.strictEqual(changed.length, 1);
    assert.strictEqual(changed[0].columnKey, 'col2');
  });

  it('returns empty array for row with no changes', () => {
    const row = createUnchangedRow();
    const changed = getChangedCells(row);
    assert.strictEqual(changed.length, 0);
  });

  it('returns empty array for null row', () => {
    const changed = getChangedCells(null);
    assert.strictEqual(changed.length, 0);
  });

  it('excludes cells with empty originalValue', () => {
    const row = createRow([
      createCell('', 'harmonized', 'col1'),
      createCell('original', 'harmonized', 'col2'),
    ]);

    const changed = getChangedCells(row);
    assert.strictEqual(changed.length, 1);
    assert.strictEqual(changed[0].columnKey, 'col2');
  });
});

describe('getTotalUnits consistency', () => {
  describe('both modes return 0 for empty data', () => {
    it('column mode returns 0 for empty rows array', () => {
      const result = getColumnTotalUnits([], 9);
      assert.strictEqual(result, 0);
    });

    it('row mode returns 0 for empty rows array', () => {
      const result = getRowTotalUnits([], 5);
      assert.strictEqual(result, 0);
    });

    it('column mode returns 0 when no rows have changes', () => {
      const rows = [createUnchangedRow(1), createUnchangedRow(2)];
      const result = getColumnTotalUnits(rows, 9);
      assert.strictEqual(result, 0);
    });

    it('row mode returns 0 when no rows have changes', () => {
      const rows = [createUnchangedRow(1), createUnchangedRow(2)];
      const result = getRowTotalUnits(rows, 5);
      assert.strictEqual(result, 0);
    });
  });

  describe('both modes count correctly with data', () => {
    it('column mode counts units based on unique entries per column', () => {
      const rows = [
        createChangedRow(1),
        createChangedRow(2),
        createChangedRow(3),
      ];
      /* 3 rows with same original value = 1 unique entry, fits in 1 batch of 9 */
      const result = getColumnTotalUnits(rows, 9);
      assert.strictEqual(result >= 1, true);
    });

    it('row mode counts batches based on changed rows', () => {
      const rows = [
        createChangedRow(1),
        createChangedRow(2),
        createChangedRow(3),
      ];
      /* 3 changed rows with batch size 2 = 2 batches */
      const result = getRowTotalUnits(rows, 2);
      assert.strictEqual(result, 2);
    });

    it('row mode rounds up partial batches', () => {
      const rows = [
        createChangedRow(1),
        createChangedRow(2),
        createChangedRow(3),
      ];
      /* 3 rows with batch size 5 = 1 batch (ceil(3/5)) */
      const result = getRowTotalUnits(rows, 5);
      assert.strictEqual(result, 1);
    });
  });
});

describe('getCurrentEntries boundary handling', () => {
  describe('column mode', () => {
    it('returns empty entries for empty rows', () => {
      const result = getColumnCurrentEntries([], 1, 9);
      assert.strictEqual(result.entries.length, 0);
    });

    it('returns empty entries when no rows have changes', () => {
      const rows = [createUnchangedRow(1)];
      const result = getColumnCurrentEntries(rows, 1, 9);
      assert.strictEqual(result.entries.length, 0);
    });

    it('clamps currentUnit to valid range', () => {
      const rows = [createChangedRow(1)];
      const result = getColumnCurrentEntries(rows, 999, 9);
      /* Should clamp to last valid unit */
      assert.strictEqual(result.unitIndex >= 1, true);
    });

    it('returns correct totalUnits in metadata', () => {
      const rows = [createChangedRow(1)];
      const result = getColumnCurrentEntries(rows, 1, 9);
      assert.strictEqual(result.totalUnits >= 1, true);
    });
  });

  describe('row mode', () => {
    it('returns empty entries for empty rows', () => {
      const result = getRowCurrentEntries([], 1, 5);
      assert.strictEqual(result.entries.length, 0);
    });

    it('returns empty entries when no rows have changes', () => {
      const rows = [createUnchangedRow(1)];
      const result = getRowCurrentEntries(rows, 1, 5);
      assert.strictEqual(result.entries.length, 0);
    });

    it('clamps currentUnit to valid range', () => {
      const rows = [createChangedRow(1)];
      const result = getRowCurrentEntries(rows, 999, 5);
      /* Should clamp to last valid unit (1) */
      assert.strictEqual(result.unitIndex, 1);
    });

    it('returns correct entries for valid batch', () => {
      const rows = [
        createChangedRow(1),
        createChangedRow(2),
        createChangedRow(3),
      ];
      const result = getRowCurrentEntries(rows, 1, 2);
      /* First batch should have 2 entries */
      assert.strictEqual(result.entries.length, 2);
    });

    it('returns remaining entries for last partial batch', () => {
      const rows = [
        createChangedRow(1),
        createChangedRow(2),
        createChangedRow(3),
      ];
      const result = getRowCurrentEntries(rows, 2, 2);
      /* Second batch should have 1 entry (3 total, batch size 2) */
      assert.strictEqual(result.entries.length, 1);
    });
  });
});

describe('integration: navigation edge cases', () => {
  it('getTotalUnits of 0 prevents infinite flash loops', () => {
    /* When getTotalUnits returns 0, currentUnit >= totalUnits is true for currentUnit=1
       This ensures the flash behavior triggers correctly on empty data */
    const rows = [];
    const columnTotal = getColumnTotalUnits(rows, 9);
    const rowTotal = getRowTotalUnits(rows, 5);

    assert.strictEqual(columnTotal, 0);
    assert.strictEqual(rowTotal, 0);

    /* currentUnit=1 >= totalUnits=0 should be true, triggering flash */
    assert.strictEqual(1 >= columnTotal, true);
    assert.strictEqual(1 >= rowTotal, true);
  });

  it('single entry results in totalUnits of 1', () => {
    const rows = [createChangedRow(1)];

    const columnTotal = getColumnTotalUnits(rows, 9);
    const rowTotal = getRowTotalUnits(rows, 5);

    assert.strictEqual(columnTotal >= 1, true);
    assert.strictEqual(rowTotal, 1);
  });
});
