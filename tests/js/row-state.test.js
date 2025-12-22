/**
 * Row State Determination Tests
 *
 * Property-based tests for determineRowState function covering all scenarios:
 *
 * Legend states:
 * - 'recommended' (✓): Using AI recommendation
 * - 'override' (✎): Manual override differs from AI
 * - 'no-mapping' (○): No mapping will be applied
 *
 * Rules:
 * 1. Override matches AI recommendation → 'recommended'
 * 2. No override and AI recommendation exists → 'recommended'
 * 3. Override differs from AI recommendation → 'override'
 * 4. Override is CDE field and no AI recommendation → 'override'
 * 5. No AI recommendation and (no override OR override === noMappingValue) → 'no-mapping'
 * 6. AI recommendation exists and override === noMappingValue → 'override'
 */

import { describe, it } from 'node:test';
import assert from 'node:assert';
import { pathToFileURL } from 'node:url';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const rowStatePath = join(__dirname, '../../src/shared/static/row-state.js');

const NO_MAPPING = 'No Mapping';
const AI_REC = 'primary_diagnosis';
const OTHER_CDE = 'morphology';

/* Load module via dynamic import */
const { determineRowState } = await import(pathToFileURL(rowStatePath).href);

describe('determineRowState', () => {

  describe('Property: Columns WITH AI recommendation', () => {
    it('no user selection → recommended (using AI suggestion)', () => {
      const result = determineRowState({
        aiRecommendation: AI_REC,
        userSelection: null,
        noMappingValue: NO_MAPPING,
      });
      assert.strictEqual(result.state, 'recommended');
      assert.strictEqual(result.icon, '✓');
    });

    it('user selects same as AI (exact match) → recommended', () => {
      const result = determineRowState({
        aiRecommendation: AI_REC,
        userSelection: AI_REC,
        noMappingValue: NO_MAPPING,
      });
      assert.strictEqual(result.state, 'recommended');
      assert.strictEqual(result.icon, '✓');
    });

    it('user selects same as AI (case-insensitive) → recommended', () => {
      const result = determineRowState({
        aiRecommendation: AI_REC,
        userSelection: AI_REC.toUpperCase(),
        noMappingValue: NO_MAPPING,
      });
      assert.strictEqual(result.state, 'recommended');
      assert.strictEqual(result.icon, '✓');
    });

    it('user selects different CDE field → override', () => {
      const result = determineRowState({
        aiRecommendation: AI_REC,
        userSelection: OTHER_CDE,
        noMappingValue: NO_MAPPING,
      });
      assert.strictEqual(result.state, 'override');
      assert.strictEqual(result.icon, '✎');
    });

    it('user selects "No Mapping" → override (rejecting AI suggestion)', () => {
      const result = determineRowState({
        aiRecommendation: AI_REC,
        userSelection: NO_MAPPING,
        noMappingValue: NO_MAPPING,
      });
      assert.strictEqual(result.state, 'override');
      assert.strictEqual(result.icon, '✎');
    });

    it('user selects "No Mapping" (case-insensitive) → override', () => {
      const result = determineRowState({
        aiRecommendation: AI_REC,
        userSelection: 'no mapping',
        noMappingValue: NO_MAPPING,
      });
      assert.strictEqual(result.state, 'override');
      assert.strictEqual(result.icon, '✎');
    });
  });

  describe('Property: Columns WITHOUT AI recommendation', () => {
    it('no user selection → no-mapping', () => {
      const result = determineRowState({
        aiRecommendation: null,
        userSelection: null,
        noMappingValue: NO_MAPPING,
      });
      assert.strictEqual(result.state, 'no-mapping');
      assert.strictEqual(result.icon, '○');
    });

    it('user selects "No Mapping" → no-mapping (same as no selection)', () => {
      const result = determineRowState({
        aiRecommendation: null,
        userSelection: NO_MAPPING,
        noMappingValue: NO_MAPPING,
      });
      assert.strictEqual(result.state, 'no-mapping');
      assert.strictEqual(result.icon, '○');
    });

    it('user selects "No Mapping" (case-insensitive) → no-mapping', () => {
      const result = determineRowState({
        aiRecommendation: null,
        userSelection: 'NO MAPPING',
        noMappingValue: NO_MAPPING,
      });
      assert.strictEqual(result.state, 'no-mapping');
      assert.strictEqual(result.icon, '○');
    });

    it('user selects a CDE field → override', () => {
      const result = determineRowState({
        aiRecommendation: null,
        userSelection: OTHER_CDE,
        noMappingValue: NO_MAPPING,
      });
      assert.strictEqual(result.state, 'override');
      assert.strictEqual(result.icon, '✎');
    });
  });

  describe('Property: Edge cases', () => {
    it('empty string userSelection treated as no selection', () => {
      const result = determineRowState({
        aiRecommendation: AI_REC,
        userSelection: '',
        noMappingValue: NO_MAPPING,
      });
      assert.strictEqual(result.state, 'recommended');
    });

    it('whitespace-only userSelection treated as no selection', () => {
      const result = determineRowState({
        aiRecommendation: AI_REC,
        userSelection: '   ',
        noMappingValue: NO_MAPPING,
      });
      assert.strictEqual(result.state, 'recommended');
    });

    it('undefined userSelection treated as no selection', () => {
      const result = determineRowState({
        aiRecommendation: AI_REC,
        userSelection: undefined,
        noMappingValue: NO_MAPPING,
      });
      assert.strictEqual(result.state, 'recommended');
    });

    it('empty string aiRecommendation treated as no recommendation', () => {
      const result = determineRowState({
        aiRecommendation: '',
        userSelection: null,
        noMappingValue: NO_MAPPING,
      });
      assert.strictEqual(result.state, 'no-mapping');
    });
  });

  describe('Property: State transitions (reversion scenarios)', () => {
    it('user overrides then reverts to AI recommendation → recommended', () => {
      /* Simulate: user first selected OTHER_CDE, then changed to AI_REC */
      const result = determineRowState({
        aiRecommendation: AI_REC,
        userSelection: AI_REC,
        noMappingValue: NO_MAPPING,
      });
      assert.strictEqual(result.state, 'recommended');
    });

    it('user overrides then selects No Mapping on column with AI rec → override', () => {
      const result = determineRowState({
        aiRecommendation: AI_REC,
        userSelection: NO_MAPPING,
        noMappingValue: NO_MAPPING,
      });
      assert.strictEqual(result.state, 'override');
    });

    it('user selects CDE then reverts to No Mapping on column without AI rec → no-mapping', () => {
      const result = determineRowState({
        aiRecommendation: null,
        userSelection: NO_MAPPING,
        noMappingValue: NO_MAPPING,
      });
      assert.strictEqual(result.state, 'no-mapping');
    });
  });

  describe('Property: Exhaustive truth table', () => {
    const testCases = [
      /* { aiRec, userSel, expected } */
      { aiRec: AI_REC, userSel: null, expected: 'recommended' },
      { aiRec: AI_REC, userSel: AI_REC, expected: 'recommended' },
      { aiRec: AI_REC, userSel: OTHER_CDE, expected: 'override' },
      { aiRec: AI_REC, userSel: NO_MAPPING, expected: 'override' },
      { aiRec: null, userSel: null, expected: 'no-mapping' },
      { aiRec: null, userSel: OTHER_CDE, expected: 'override' },
      { aiRec: null, userSel: NO_MAPPING, expected: 'no-mapping' },
    ];

    testCases.forEach(({ aiRec, userSel, expected }) => {
      const aiDesc = aiRec ? `AI="${aiRec}"` : 'no AI';
      const userDesc = userSel ? `user="${userSel}"` : 'no user selection';

      it(`${aiDesc}, ${userDesc} → ${expected}`, () => {
        const result = determineRowState({
          aiRecommendation: aiRec,
          userSelection: userSel,
          noMappingValue: NO_MAPPING,
        });
        assert.strictEqual(result.state, expected);
      });
    });
  });
});
