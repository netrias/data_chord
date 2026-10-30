/** Pure summary and layout rules for the Stage 5 change details chart. */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import {
  MAX_SCALE,
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
    // Then one-value bands, which scale below the floor, are still drawn at the minimum
    const oneValueBands = layout.bands.filter((band) => band.value === 1);
    assert.ok(oneValueBands.length > 0);
    assert.ok(layout.scale < MIN_BAND);
    for (const band of layout.bands) {
      assert.ok(band.t >= MIN_BAND, `${band.source.column.name} ${band.target.outcome.id} band is ${band.t}px`);
    }
  });

  it('draws bands above the floor in proportion to their unique values', () => {
    // Given a column whose outcomes all scale above the minimum band
    const columns = [column('diagnosis', 40, 20, 80), column('site', 10, 0, 30)];

    // When the flow is laid out
    const layout = layoutFlow(columns, 900);

    // Then every band is its value times the shared scale, and the scale fills the band budget
    assert.equal(layout.scale, 280 / 180);
    const thicknesses = layout.bands.map((band) => ({ value: band.value, t: band.t }));
    assert.deepEqual(thicknesses, thicknesses.map(({ value }) => ({ value, t: value * layout.scale })));
    assert.ok(thicknesses.every(({ t }) => t > MIN_BAND));
  });

  it('caps the per-value scale for tiny datasets', () => {
    // Given a dataset so small that filling the band budget would draw 40 px per value
    const columns = [column('sex', 2, 1, 4)];

    // When the flow is laid out
    const layout = layoutFlow(columns, 900);

    // Then each value is drawn at the capped scale
    assert.equal(layout.scale, MAX_SCALE);
    assert.deepEqual(layout.bands.map((band) => band.t), [2 * MAX_SCALE, 1 * MAX_SCALE, 4 * MAX_SCALE]);
  });

  it("stacks each node's bands from its top to its bottom without gaps or overlap", () => {
    // Given several columns that share all three outcomes
    const columns = [
      column('diagnosis', 40, 3, 70),
      column('site', 12, 1, 40),
      column('race', 1, 0, 3),
      column('stage', 0, 2, 9),
    ];

    // When the flow is laid out
    const layout = layoutFlow(columns, 900);

    // Then each node's bands start at its top, touch end to start, and end at its bottom
    const nodeEdges = [
      ...layout.left.map((node) => ({ node, starts: node.bands.map((band) => band.y0) })),
      ...layout.right.map((node) => ({ node, starts: node.bands.map((band) => band.y1) })),
    ];
    for (const { node, starts } of nodeEdges) {
      let expected = node.y;
      for (const [index, band] of node.bands.entries()) {
        assert.equal(starts[index], expected);
        expected += band.t;
      }
      // Summing in a different order can differ in the last float bit, far below a pixel.
      assert.ok(Math.abs(expected - (node.y + node.h)) < 1e-9, `bands end at ${expected}, node ends at ${node.y + node.h}`);
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
