/** Stage 4 review navigation tests using the column-centric API response. */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { SORT_MODE } from '../../src/stage_4_review_results/static/shared_review_utils.js';
import {
  getCurrentEntries as getColumnCurrentEntries,
  getTotalUnits as getColumnTotalUnits,
} from '../../src/stage_4_review_results/static/review_mode_column.js';
import {
  getCurrentEntries as getRowCurrentEntries,
  getTotalUnits as getRowTotalUnits,
} from '../../src/stage_4_review_results/static/review_mode_row.js';

const BUCKET_CONFIDENCE = {
  high: 0.9,
  medium: 0.6,
  low: 0.3,
};

const createTransformation = ({
  originalValue = 'source',
  harmonizedValue = 'target',
  rowIndices = [1],
  bucket = 'high',
  recommendationType,
} = {}) => ({
  originalValue,
  harmonizedValue,
  confidence: BUCKET_CONFIDENCE[bucket],
  bucket,
  isChanged: originalValue !== harmonizedValue,
  recommendationType: recommendationType
    ?? (originalValue === harmonizedValue ? 'ai_unchanged' : 'ai_changed'),
  isPVConformant: false,
  pvSetAvailable: false,
  topSuggestions: [],
  rowIndices,
  rowCount: rowIndices.length,
  manualOverride: null,
});

const createColumn = (columnKey, transformations, sourceColumnIndex = 0) => ({
  columnKey,
  columnLabel: columnKey.replaceAll('_', ' '),
  targetCdeKey: `${columnKey}_cde`,
  targetCdeLabel: `${columnKey} CDE`,
  sourceColumnIndex,
  termCount: transformations.length,
  termsWithChanges: transformations.filter((entry) => entry.isChanged).length,
  transformations,
});

describe('empty review results', () => {
  it('shows no navigable work in either review mode', () => {
    const unchangedColumn = createColumn('diagnosis', [
      createTransformation({ originalValue: 'same', harmonizedValue: 'same' }),
    ]);

    assert.equal(getColumnTotalUnits([], 10), 0);
    assert.equal(getRowTotalUnits([], 10), 0);
    assert.equal(getColumnTotalUnits([unchangedColumn], 10), 0);
    assert.equal(getRowTotalUnits([unchangedColumn], 10), 0);
    assert.deepEqual(getColumnCurrentEntries([unchangedColumn], 1, 10).entries, []);
    assert.deepEqual(getRowCurrentEntries([unchangedColumn], 1, 10).entries, []);
  });
});

describe('column review navigation', () => {
  it('batches terms within a column before moving to the next column', () => {
    const columns = [
      createColumn('diagnosis', [
        createTransformation({ originalValue: 'alpha', rowIndices: [1] }),
        createTransformation({ originalValue: 'beta', rowIndices: [2] }),
        createTransformation({ originalValue: 'gamma', rowIndices: [3] }),
      ]),
      createColumn('sample_site', [
        createTransformation({ originalValue: 'lung', rowIndices: [4] }),
      ], 1),
    ];

    assert.equal(getColumnTotalUnits(columns, 2), 3);

    const firstBatch = getColumnCurrentEntries(columns, 1, 2);
    assert.deepEqual(firstBatch.entries.map((entry) => entry.originalValue), ['alpha', 'beta']);
    assert.equal(firstBatch.columnLabel, 'diagnosis');
    assert.equal(firstBatch.batchWithinColumn, 1);
    assert.equal(firstBatch.totalBatchesInColumn, 2);

    const secondBatch = getColumnCurrentEntries(columns, 2, 2);
    assert.deepEqual(secondBatch.entries.map((entry) => entry.originalValue), ['gamma']);
    assert.equal(secondBatch.batchWithinColumn, 2);

    const finalBatch = getColumnCurrentEntries(columns, 999, 2);
    assert.equal(finalBatch.unitIndex, 3);
    assert.equal(finalBatch.columnLabel, 'sample site');
    assert.deepEqual(finalBatch.entries.map((entry) => entry.originalValue), ['lung']);
  });

  it('sorts before slicing so confidence batches remain stable', () => {
    const columns = [createColumn('diagnosis', [
      createTransformation({ originalValue: 'high', bucket: 'high' }),
      createTransformation({ originalValue: 'low-a', bucket: 'low' }),
      createTransformation({ originalValue: 'medium', bucket: 'medium' }),
      createTransformation({ originalValue: 'low-b', bucket: 'low' }),
    ])];

    const firstBatch = getColumnCurrentEntries(columns, 1, 2, SORT_MODE.CONFIDENCE_ASC);
    const secondBatch = getColumnCurrentEntries(columns, 2, 2, SORT_MODE.CONFIDENCE_ASC);
    const descendingBatch = getColumnCurrentEntries(columns, 1, 2, SORT_MODE.CONFIDENCE_DESC);

    assert.deepEqual(firstBatch.entries.map((entry) => entry.originalValue), ['low-a', 'low-b']);
    assert.deepEqual(secondBatch.entries.map((entry) => entry.originalValue), ['medium', 'high']);
    assert.deepEqual(descendingBatch.entries.map((entry) => entry.originalValue), ['high', 'medium']);
  });
});

describe('row review navigation', () => {
  it('expands a grouped term into each affected source row', () => {
    const columns = [createColumn('diagnosis', [
      createTransformation({ originalValue: 'lung', rowIndices: [1, 2, 3] }),
    ])];

    assert.equal(getColumnTotalUnits(columns, 2), 1, 'one grouped term is one column card');
    assert.equal(getRowTotalUnits(columns, 2), 2, 'three source rows require two row batches');

    const firstBatch = getRowCurrentEntries(columns, 1, 2);
    const secondBatch = getRowCurrentEntries(columns, 2, 2);
    assert.deepEqual(firstBatch.entries.map((row) => row.rowIndex), [1, 2]);
    assert.deepEqual(secondBatch.entries.map((row) => row.rowIndex), [3]);
  });

  it('reconstructs rows across columns in source-column order', () => {
    const columns = [
      createColumn('diagnosis', [
        createTransformation({ originalValue: 'alpha', rowIndices: [1, 2] }),
      ]),
      createColumn('sample_site', [
        createTransformation({ originalValue: 'lung', rowIndices: [2] }),
      ], 1),
    ];

    const result = getRowCurrentEntries(columns, 1, 10);

    assert.deepEqual(result.entries.map((row) => row.rowIndex), [1, 2]);
    assert.deepEqual(result.entries[0].changedCells.map((cell) => cell.columnKey), ['diagnosis']);
    assert.deepEqual(
      result.entries[1].changedCells.map((cell) => cell.columnKey),
      ['diagnosis', 'sample_site'],
    );
  });

  it('sorts rows by their least-confident changed cell', () => {
    const columns = [
      createColumn('diagnosis', [
        createTransformation({ originalValue: 'row-1-a', rowIndices: [1], bucket: 'high' }),
        createTransformation({ originalValue: 'row-2-a', rowIndices: [2], bucket: 'high' }),
        createTransformation({ originalValue: 'row-3-a', rowIndices: [3], bucket: 'medium' }),
      ]),
      createColumn('sample_site', [
        createTransformation({ originalValue: 'row-1-b', rowIndices: [1], bucket: 'high' }),
        createTransformation({ originalValue: 'row-2-b', rowIndices: [2], bucket: 'low' }),
        createTransformation({ originalValue: 'row-3-b', rowIndices: [3], bucket: 'high' }),
      ], 1),
    ];

    const result = getRowCurrentEntries(columns, 1, 10, SORT_MODE.CONFIDENCE_ASC);
    const descending = getRowCurrentEntries(columns, 1, 10, SORT_MODE.CONFIDENCE_DESC);

    assert.deepEqual(result.entries.map((row) => row.rowIndex), [2, 3, 1]);
    assert.deepEqual(descending.entries.map((row) => row.rowIndex), [1, 3, 2]);
  });
});

describe('review filters and navigation bounds', () => {
  it('adds case-only and unchanged values only when the reviewer asks for them', () => {
    const columns = [createColumn('diagnosis', [
      createTransformation({ originalValue: 'Lung', harmonizedValue: 'Pulmonary' }),
      createTransformation({ originalValue: 'Lung', harmonizedValue: 'lung' }),
      createTransformation({ originalValue: 'Lung', harmonizedValue: 'Lung' }),
    ])];

    assert.equal(getColumnTotalUnits(columns, 10), 1);
    assert.equal(getRowTotalUnits(columns, 10), 1);

    const showEverything = {
      showCaseOnlyChanges: true,
      showUnchangedValues: true,
    };
    assert.equal(getColumnTotalUnits(columns, 10, showEverything), 1);

    const columnEntries = getColumnCurrentEntries(
      columns,
      1,
      10,
      SORT_MODE.ORIGINAL,
      showEverything,
    );
    assert.equal(columnEntries.entries.length, 3);

    const rowEntries = getRowCurrentEntries(
      columns,
      1,
      10,
      SORT_MODE.ORIGINAL,
      showEverything,
    );
    assert.equal(rowEntries.entries.length, 1);
    assert.equal(rowEntries.entries[0].changedCells.length, 3);
  });

  it('clamps invalid batch numbers to the nearest available batch', () => {
    const columns = [createColumn('diagnosis', [
      createTransformation({ originalValue: 'alpha', rowIndices: [1] }),
      createTransformation({ originalValue: 'beta', rowIndices: [2] }),
    ])];

    assert.equal(getColumnCurrentEntries(columns, 0, 1).unitIndex, 1);
    assert.equal(getColumnCurrentEntries(columns, 999, 1).unitIndex, 2);
    assert.equal(getRowCurrentEntries(columns, 0, 1).unitIndex, 1);
    assert.equal(getRowCurrentEntries(columns, 999, 1).unitIndex, 2);
  });
});
