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

const { rowHasChanges, getChangedCells, sortEntriesByConfidence, getMinConfidence, SORT_MODE } = await import(pathToFileURL(sharedUtilsPath).href);
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

/* Test data factories for sorting */
const createCellWithConfidence = (original, harmonized, bucket, columnKey = 'col1') => ({
  columnKey,
  columnLabel: columnKey.replace('_', ' '),
  originalValue: original,
  harmonizedValue: harmonized,
  confidence: bucket === 'high' ? 0.9 : bucket === 'medium' ? 0.6 : 0.3,
  bucket,
  isChanged: original !== harmonized,
});

const createRowWithConfidence = (cells, rowNumber = 1) => ({
  rowNumber,
  sourceRowNumber: rowNumber,
  recordId: `record-${rowNumber}`,
  cells,
});

describe('sortEntriesByConfidence', () => {
  describe('returns entries in correct order', () => {
    it('returns original order when sortMode is ORIGINAL', () => {
      // Given: entries in mixed confidence order
      const entries = [
        { bucket: 'high', originalValue: 'a' },
        { bucket: 'low', originalValue: 'b' },
        { bucket: 'medium', originalValue: 'c' },
      ];

      // When: sorting with ORIGINAL mode
      const result = sortEntriesByConfidence(entries, SORT_MODE.ORIGINAL);

      // Then: order is unchanged
      assert.strictEqual(result[0].originalValue, 'a');
      assert.strictEqual(result[1].originalValue, 'b');
      assert.strictEqual(result[2].originalValue, 'c');
    });

    it('sorts low-to-high when sortMode is CONFIDENCE_ASC', () => {
      // Given: entries with different confidence levels
      const entries = [
        { bucket: 'high', originalValue: 'high-entry' },
        { bucket: 'low', originalValue: 'low-entry' },
        { bucket: 'medium', originalValue: 'medium-entry' },
      ];

      // When: sorting ascending (lowest confidence first)
      const result = sortEntriesByConfidence(entries, SORT_MODE.CONFIDENCE_ASC);

      // Then: low → medium → high
      assert.strictEqual(result[0].originalValue, 'low-entry');
      assert.strictEqual(result[1].originalValue, 'medium-entry');
      assert.strictEqual(result[2].originalValue, 'high-entry');
    });

    it('sorts high-to-low when sortMode is CONFIDENCE_DESC', () => {
      // Given: entries with different confidence levels
      const entries = [
        { bucket: 'low', originalValue: 'low-entry' },
        { bucket: 'high', originalValue: 'high-entry' },
        { bucket: 'medium', originalValue: 'medium-entry' },
      ];

      // When: sorting descending (highest confidence first)
      const result = sortEntriesByConfidence(entries, SORT_MODE.CONFIDENCE_DESC);

      // Then: high → medium → low
      assert.strictEqual(result[0].originalValue, 'high-entry');
      assert.strictEqual(result[1].originalValue, 'medium-entry');
      assert.strictEqual(result[2].originalValue, 'low-entry');
    });
  });

  describe('does not mutate original array', () => {
    it('returns a new array when sorting', () => {
      const entries = [
        { bucket: 'high', originalValue: 'a' },
        { bucket: 'low', originalValue: 'b' },
      ];
      const original = [...entries];

      sortEntriesByConfidence(entries, SORT_MODE.CONFIDENCE_ASC);

      // Original array should be unchanged
      assert.strictEqual(entries[0].originalValue, original[0].originalValue);
      assert.strictEqual(entries[1].originalValue, original[1].originalValue);
    });
  });

  describe('handles edge cases', () => {
    it('returns empty array unchanged', () => {
      const result = sortEntriesByConfidence([], SORT_MODE.CONFIDENCE_ASC);
      assert.strictEqual(result.length, 0);
    });

    it('handles null/undefined sortMode by returning original order', () => {
      const entries = [{ bucket: 'high' }, { bucket: 'low' }];
      const result = sortEntriesByConfidence(entries, null);
      assert.strictEqual(result, entries); // Same reference for ORIGINAL mode
    });

    it('uses fallback of 0 for unknown bucket', () => {
      // Given: entry with unknown bucket type
      const entries = [
        { bucket: 'high', originalValue: 'high' },
        { bucket: 'unknown', originalValue: 'unknown' },
        { bucket: 'low', originalValue: 'low' },
      ];

      // When: sorting ascending
      const result = sortEntriesByConfidence(entries, SORT_MODE.CONFIDENCE_ASC);

      // Then: unknown (0) comes before low (1)
      assert.strictEqual(result[0].originalValue, 'unknown');
      assert.strictEqual(result[1].originalValue, 'low');
      assert.strictEqual(result[2].originalValue, 'high');
    });
  });
});

describe('getMinConfidence', () => {
  it('returns minimum confidence from array of cells', () => {
    const cells = [
      { bucket: 'high' },
      { bucket: 'low' },
      { bucket: 'medium' },
    ];

    const result = getMinConfidence(cells);

    // low = 1, medium = 2, high = 3
    assert.strictEqual(result, 1);
  });

  it('returns Infinity for empty array', () => {
    const result = getMinConfidence([]);
    assert.strictEqual(result, Infinity);
  });

  it('returns Infinity for null/undefined', () => {
    assert.strictEqual(getMinConfidence(null), Infinity);
    assert.strictEqual(getMinConfidence(undefined), Infinity);
  });

  it('handles single cell', () => {
    const cells = [{ bucket: 'medium' }];
    const result = getMinConfidence(cells);
    assert.strictEqual(result, 2);
  });
});

describe('sorting integration with getCurrentEntries', () => {
  describe('column mode applies sorting correctly', () => {
    it('entries are sorted within column batches', () => {
      // Given: rows with varying confidence levels
      const rows = [
        createRowWithConfidence([createCellWithConfidence('high-val', 'harmonized', 'high')], 1),
        createRowWithConfidence([createCellWithConfidence('low-val', 'harmonized', 'low')], 2),
        createRowWithConfidence([createCellWithConfidence('med-val', 'harmonized', 'medium')], 3),
      ];

      // When: getting entries with ascending sort
      const result = getColumnCurrentEntries(rows, 1, 9, SORT_MODE.CONFIDENCE_ASC);

      // Then: entries should be ordered low → medium → high
      assert.strictEqual(result.entries.length, 3);
      assert.strictEqual(result.entries[0].originalValue, 'low-val');
      assert.strictEqual(result.entries[1].originalValue, 'med-val');
      assert.strictEqual(result.entries[2].originalValue, 'high-val');
    });

    it('entries are sorted descending when requested', () => {
      // Given: rows with varying confidence levels
      const rows = [
        createRowWithConfidence([createCellWithConfidence('low-val', 'harmonized', 'low')], 1),
        createRowWithConfidence([createCellWithConfidence('high-val', 'harmonized', 'high')], 2),
        createRowWithConfidence([createCellWithConfidence('med-val', 'harmonized', 'medium')], 3),
      ];

      // When: getting entries with descending sort
      const result = getColumnCurrentEntries(rows, 1, 9, SORT_MODE.CONFIDENCE_DESC);

      // Then: entries should be ordered high → medium → low
      assert.strictEqual(result.entries.length, 3);
      assert.strictEqual(result.entries[0].originalValue, 'high-val');
      assert.strictEqual(result.entries[1].originalValue, 'med-val');
      assert.strictEqual(result.entries[2].originalValue, 'low-val');
    });

    it('batch boundaries are calculated on sorted data', () => {
      // Given: 4 entries with unique original values that will span 2 batches of 2
      const rows = [
        createRowWithConfidence([createCellWithConfidence('high-1', 'h1', 'high')], 1),
        createRowWithConfidence([createCellWithConfidence('low-1', 'l1', 'low')], 2),
        createRowWithConfidence([createCellWithConfidence('high-2', 'h2', 'high')], 3),
        createRowWithConfidence([createCellWithConfidence('low-2', 'l2', 'low')], 4),
      ];

      // When: getting first batch with ascending sort, entriesPerBatch = 2
      const batch1 = getColumnCurrentEntries(rows, 1, 2, SORT_MODE.CONFIDENCE_ASC);

      // Then: first batch contains the 2 lowest confidence entries
      assert.strictEqual(batch1.entries.length, 2, `Expected 2 entries in batch 1, got ${batch1.entries.length}`);
      assert.strictEqual(batch1.entries[0].bucket, 'low');
      assert.strictEqual(batch1.entries[1].bucket, 'low');

      // When: getting second batch
      const batch2 = getColumnCurrentEntries(rows, 2, 2, SORT_MODE.CONFIDENCE_ASC);

      // Then: second batch contains the 2 highest confidence entries
      assert.strictEqual(batch2.entries.length, 2, `Expected 2 entries in batch 2, got ${batch2.entries.length}`);
      assert.strictEqual(batch2.entries[0].bucket, 'high');
      assert.strictEqual(batch2.entries[1].bucket, 'high');
    });
  });

  describe('row mode applies sorting correctly', () => {
    it('rows are sorted by minimum cell confidence ascending', () => {
      // Given: rows where each has cells with different confidence
      const rows = [
        createRowWithConfidence([createCellWithConfidence('r1', 'h', 'high')], 1),
        createRowWithConfidence([createCellWithConfidence('r2', 'h', 'low')], 2),
        createRowWithConfidence([createCellWithConfidence('r3', 'h', 'medium')], 3),
      ];

      // When: getting entries with ascending sort
      const result = getRowCurrentEntries(rows, 1, 5, SORT_MODE.CONFIDENCE_ASC);

      // Then: rows sorted by their minimum confidence (row 2 first with low)
      assert.strictEqual(result.entries.length, 3);
      assert.strictEqual(result.entries[0].rowIndex, 2); // low confidence
      assert.strictEqual(result.entries[1].rowIndex, 3); // medium confidence
      assert.strictEqual(result.entries[2].rowIndex, 1); // high confidence
    });

    it('rows are sorted by minimum cell confidence descending', () => {
      // Given: rows where each has cells with different confidence
      const rows = [
        createRowWithConfidence([createCellWithConfidence('r1', 'h', 'low')], 1),
        createRowWithConfidence([createCellWithConfidence('r2', 'h', 'high')], 2),
        createRowWithConfidence([createCellWithConfidence('r3', 'h', 'medium')], 3),
      ];

      // When: getting entries with descending sort
      const result = getRowCurrentEntries(rows, 1, 5, SORT_MODE.CONFIDENCE_DESC);

      // Then: rows sorted by their minimum confidence descending (row 2 first with high)
      assert.strictEqual(result.entries.length, 3);
      assert.strictEqual(result.entries[0].rowIndex, 2); // high confidence
      assert.strictEqual(result.entries[1].rowIndex, 3); // medium confidence
      assert.strictEqual(result.entries[2].rowIndex, 1); // low confidence
    });
  });
});
