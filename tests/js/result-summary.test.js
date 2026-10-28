/** Pure display-rule tests for the Stage 3 result summary. */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { summarizeResult } from '../../src/stage_3_harmonize/static/result-summary.js';

const groupView = (summary) => summary.groups.map(({ key, count, label, state }) => ({ key, count, label, state }));
const GDC = { label: 'GDC', version: null };

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
      const summary = summarizeResult({ checked, ...counts }, GDC);

      // Then the dial shows the matching share, rounded down
      assert.equal(summary.dialValue, percent);
    });
  }

  it('always returns three groups and highlights only present unresolved values', () => {
    // Given a run with no harmonized values and some unresolved values
    const counts = { checked: 52, harmonized: 0, matched: 46, unresolved: 6 };

    // When the summary is derived
    const summary = summarizeResult(counts, GDC);

    // Then all three groups exist, the zero group is muted, and only unresolved needs attention
    assert.deepEqual(groupView(summary), [
      { key: 'harmonized', count: 0, label: 'harmonized', state: 'zero' },
      { key: 'matched', count: 46, label: 'already matched', state: 'normal' },
      { key: 'unresolved', count: 6, label: 'not harmonized', state: 'attention' },
    ]);
  });

  const headlineScenarios = [
    {
      name: 'names a short standard',
      standard: { label: 'GDC', version: null },
      headline: { lead: 'of 52 unique values now match', standardName: 'GDC' },
    },
    {
      name: 'adds the version after the standard name',
      standard: { label: 'CCDI', version: 'v2.1' },
      headline: { lead: 'of 52 unique values now match', standardName: 'CCDI v2.1' },
    },
    {
      name: 'falls back to the approved list when no name was recorded',
      standard: { label: null, version: 'v2.1' },
      headline: { lead: 'of 52 unique values now match the approved list', standardName: null },
    },
    {
      name: 'treats a blank name as no name',
      standard: { label: '  ', version: undefined },
      headline: { lead: 'of 52 unique values now match the approved list', standardName: null },
    },
    {
      name: 'ignores a blank version',
      standard: { label: ' GDC ', version: ' ' },
      headline: { lead: 'of 52 unique values now match', standardName: 'GDC' },
    },
  ];

  for (const { name, standard, headline } of headlineScenarios) {
    it(`headline ${name}`, () => {
      // Given a mixed run and the standard the job recorded
      const counts = { checked: 52, harmonized: 37, matched: 9, unresolved: 6 };

      // When the summary is derived
      const summary = summarizeResult(counts, standard);

      // Then the dial holds only the percent and the headline finishes the sentence with the standard
      assert.equal(summary.dialValue, '88%');
      assert.deepEqual(summary.headline, headline);
    });
  }

  it('uses singular wording for one checked value', () => {
    // Given a run with a single checked value
    const oneChecked = { checked: 1, harmonized: 1, matched: 0, unresolved: 0 };

    // When the summary is derived
    const single = summarizeResult(oneChecked, GDC);

    // Then the headline says "unique value"
    assert.equal(single.dialValue, '100%');
    assert.deepEqual(single.headline, { lead: 'of 1 unique value now match', standardName: 'GDC' });
  });

  it('continues a zero dial with a neutral sentence and no groups when nothing was checked', () => {
    // Given a run where no unique value had an approved list
    const noneChecked = { checked: 0, harmonized: 0, matched: 0, unresolved: 0 };

    // When the summary is derived, even though a standard name was recorded
    const summary = summarizeResult(noneChecked, { label: 'CCDI', version: 'v2.1' });

    // Then the dial shows 0, the headline reads on from it without naming the standard, and no groups exist
    assert.deepEqual(summary, {
      dialValue: '0',
      headline: { lead: 'unique values were checked against an approved list', standardName: null },
      groups: [],
    });
  });
});
