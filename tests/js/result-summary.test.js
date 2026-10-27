/** Pure display-rule tests for the Stage 3 result summary. */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { summarizeResult } from '../../src/stage_3_harmonize/static/result-summary.js';

const groupView = (summary) => summary.groups.map(({ key, count, label, state }) => ({ key, count, label, state }));

describe('Stage 3 result summary', () => {
  const percentScenarios = [
    { name: 'rounds a mixed share down', counts: { harmonized: 37, matched: 9, unresolved: 6 }, percent: '88%' },
    { name: 'shows 100% when nothing is left', counts: { harmonized: 43, matched: 9, unresolved: 0 }, percent: '100%' },
    { name: 'does not round 99.9% up to 100%', counts: { harmonized: 999, matched: 0, unresolved: 1 }, percent: '99%' },
    { name: 'shows <1% for a tiny non-zero share', counts: { harmonized: 1, matched: 0, unresolved: 299 }, percent: '<1%' },
    { name: 'shows 0% when nothing matches', counts: { harmonized: 0, matched: 0, unresolved: 52 }, percent: '0%' },
    { name: 'keeps 29 of 100 at 29% despite float error', counts: { harmonized: 29, matched: 0, unresolved: 71 }, percent: '29%' },
    { name: 'keeps 57 of 100 at 57% despite float error', counts: { harmonized: 50, matched: 7, unresolved: 43 }, percent: '57%' },
  ];

  for (const { name, counts, percent } of percentScenarios) {
    it(name, () => {
      // Given checked counts that partition into the three groups
      const checked = counts.harmonized + counts.matched + counts.unresolved;
      assert.ok(checked > 0);

      // When the summary is derived
      const summary = summarizeResult({ checked, ...counts });

      // Then the dial shows the matching share, rounded down
      assert.equal(summary.percentLabel, percent);
    });
  }

  it('always returns three groups and highlights only present unresolved values', () => {
    // Given a run with no harmonized values and some unresolved values
    const counts = { checked: 52, harmonized: 0, matched: 46, unresolved: 6 };

    // When the summary is derived
    const summary = summarizeResult(counts);

    // Then all three groups exist, the zero group is muted, and only unresolved needs attention
    assert.deepEqual(groupView(summary), [
      { key: 'harmonized', count: 0, label: 'harmonized', state: 'zero' },
      { key: 'matched', count: 46, label: 'already matched', state: 'normal' },
      { key: 'unresolved', count: 6, label: 'not harmonized', state: 'attention' },
    ]);
    assert.equal(summary.caption, 'of 52 unique values now match');
    assert.equal(summary.ariaLabel, '88% of 52 unique values now match');
  });

  it('uses singular wording for one checked value', () => {
    // Given a run with a single checked value
    const oneChecked = { checked: 1, harmonized: 1, matched: 0, unresolved: 0 };

    // When the summary is derived
    const single = summarizeResult(oneChecked);

    // Then the caption and dial label say "unique value"
    assert.equal(single.caption, 'of 1 unique value now match');
    assert.equal(single.ariaLabel, '100% of 1 unique value now match');
  });
});
