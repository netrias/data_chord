/** Pure summary and layout rules for the Stage 5 change details chart. */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import {
  MIN_BAND,
  layoutFlow,
  summarizeChanges,
} from '../../src/stage_5_review_summary/static/stage_5_change_flow.js';

const column = (name, ai, you, same, changedRows = 0) => ({
  column: name,
  distinct_terms: ai + you + same,
  ai_changes: ai,
  manual_changes: you,
  unchanged: same,
  changed_rows: changedRows,
});

const bandsByColumn = (layout) => Object.fromEntries(layout.left.map((node) => [
  node.column.name,
  node.bands.map((band) => band.target.outcome.id),
]));

describe('Stage 5 change details', () => {
  it('summarizes unique-value outcomes and changed rows across columns', () => {
    // Given two columns with different outcome counts and changed rows
    const columns = [column('diagnosis', 4, 2, 9, 31), column('sex', 1, 0, 2, 7)];

    // When the summary line totals are computed
    const totals = summarizeChanges(columns);

    // Then each total is the sum of the matching column field
    assert.deepEqual(totals, { ai: 5, you: 2, same: 11, rowsChanged: 38 });
  });

  it('sorts rows by changed count, then total, and keeps bands thick enough to see', () => {
    // Given one large column and small columns whose single values would scale below the minimum band
    const columns = [
      column('race', 1, 0, 3),
      column('morphology', 2, 1, 900),
      column('stage', 1, 0, 1),
      column('site', 40, 5, 10),
    ];
    const totalUnique = columns.reduce((sum, item) => sum + item.distinct_terms, 0);
    assert.equal(totalUnique, 964);

    // When the flow is laid out
    const layout = layoutFlow(columns, 900);

    // Then rows run by changed count descending, ties broken by total unique values
    assert.deepEqual(layout.left.map((node) => node.column.name), ['site', 'morphology', 'race', 'stage']);
    // Then each column's bands fill its node exactly and none is thinner than the minimum
    const oneValueBands = layout.bands.filter((band) => band.value === 1);
    assert.ok(oneValueBands.length > 0);
    for (const node of layout.left) {
      const bandTotal = node.bands.reduce((sum, band) => sum + band.t, 0);
      assert.equal(bandTotal, node.h, `${node.column.name} bands should sum to its node height`);
      for (const band of node.bands) {
        assert.ok(band.t >= MIN_BAND, `${node.column.name} ${band.target.outcome.id} band is ${band.t}px`);
      }
    }
  });

  it('leaves out columns that have no unique values', () => {
    // Given a column with values and a column with none
    const columns = [column('diagnosis', 2, 1, 3), column('empty_column', 0, 0, 0)];
    assert.equal(columns[1].distinct_terms, 0);

    // When the flow is laid out
    const layout = layoutFlow(columns, 900);

    // Then only the column with values is drawn
    assert.deepEqual(layout.left.map((node) => node.column.name), ['diagnosis']);
  });

  it('draws no "by you" band for a column without manual changes', () => {
    // Given one column with manual changes and one without
    const columns = [column('diagnosis', 2, 1, 3), column('sex', 1, 0, 2)];

    // When the flow is laid out
    const layout = layoutFlow(columns, 900);

    // Then only the column with manual changes has a band to the "by you" outcome
    assert.deepEqual(bandsByColumn(layout), {
      diagnosis: ['ai', 'you', 'same'],
      sex: ['ai', 'same'],
    });
  });
});
