/**
 * Card State Tests
 *
 * Tests for Stage 4 value card state determination covering:
 * - No override, conformant override, non-conformant override, revert to original
 * - PV conformance styling (showWarningIcon, showConformantHeader)
 * - overrideIsKnownConformant trust flag
 * - Whitespace/case sensitivity (domain-critical)
 */

import { describe, it } from 'node:test';
import assert from 'node:assert';
import { pathToFileURL } from 'node:url';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const cardStatePath = join(__dirname, '../../src/stage_4_review_results/static/card-state.js');

const { determineCardState, isEffectiveOverride } = await import(pathToFileURL(cardStatePath).href);

/* Test data constants */
const ORIGINAL = 'lung cancer';
const AI_SUGGESTION = 'Lung Cancer';
const CONFORMANT_OVERRIDE = 'Breast Cancer';
const NON_CONFORMANT_OVERRIDE = 'invalid value';

/* Helper to create PV set */
const createPVSet = (...values) => new Set(values);

/* Standard PV set for tests */
const STANDARD_PV_SET = createPVSet('Lung Cancer', 'Breast Cancer', 'Colon Cancer');

/* Factory for creating test inputs */
const createInput = (overrides = {}) => ({
  originalValue: ORIGINAL,
  aiSuggestedValue: AI_SUGGESTION,
  overrideValue: '',
  hasPVs: true,
  pvSet: STANDARD_PV_SET,
  aiIsConformant: true,
  ...overrides,
});

describe('determineCardState', () => {

  describe('No override scenarios (using AI suggestion)', () => {

    it('AI suggestion conformant → conformant header, no warning', () => {
      const input = createInput({
        overrideValue: '',
        aiIsConformant: true,
      });

      const state = determineCardState(input);

      assert.strictEqual(state.activeValue, AI_SUGGESTION);
      assert.strictEqual(state.hasOverride, false);
      assert.strictEqual(state.isConformant, true);
      assert.strictEqual(state.showConformantHeader, true);
      assert.strictEqual(state.showWarningIcon, false);
    });

    it('AI suggestion non-conformant → warning icon, no green header', () => {
      const input = createInput({
        overrideValue: '',
        aiIsConformant: false,
      });

      const state = determineCardState(input);

      assert.strictEqual(state.hasOverride, false);
      assert.strictEqual(state.isConformant, false);
      assert.strictEqual(state.showConformantHeader, false);
      assert.strictEqual(state.showWarningIcon, true);
    });

    it('No PVs for column → no warning, no conformant header (neutral)', () => {
      const input = createInput({
        overrideValue: '',
        hasPVs: false,
        pvSet: null,
      });

      const state = determineCardState(input);

      assert.strictEqual(state.showConformantHeader, false);
      assert.strictEqual(state.showWarningIcon, false);
    });
  });

  describe('Override to conformant value', () => {

    it('override matches PV → conformant header, no warning', () => {
      const input = createInput({
        overrideValue: CONFORMANT_OVERRIDE,
      });

      const state = determineCardState(input);

      assert.strictEqual(state.activeValue, CONFORMANT_OVERRIDE);
      assert.strictEqual(state.hasOverride, true);
      assert.strictEqual(state.isConformant, true);
      assert.strictEqual(state.showConformantHeader, true);
      assert.strictEqual(state.showWarningIcon, false);
    });

    it('overrideIsKnownConformant=true skips pvSet check → conformant even if value not in pvSet', () => {
      const input = createInput({
        overrideValue: 'Value From Dropdown Selection',
        overrideIsKnownConformant: true,
      });

      const state = determineCardState(input);

      // Value is NOT in STANDARD_PV_SET, but overrideIsKnownConformant=true trusts it
      assert.strictEqual(state.isConformant, true);
      assert.strictEqual(state.showConformantHeader, true);
      assert.strictEqual(state.showWarningIcon, false);
    });

    it('overrideIsKnownConformant=true skips pvSet check → conformant even if value not in pvSet', () => {
      const input = createInput({
        overrideValue: 'Value From Dropdown Selection',
        overrideIsKnownConformant: true,
      });

      const state = determineCardState(input);

      // Value is NOT in STANDARD_PV_SET, but overrideIsKnownConformant=true trusts it
      assert.strictEqual(state.isConformant, true);
      assert.strictEqual(state.showConformantHeader, true);
      assert.strictEqual(state.showWarningIcon, false);
    });
  });

  describe('Override to non-conformant value', () => {

    it('override not in PV set → warning icon, no green header', () => {
      const input = createInput({
        overrideValue: NON_CONFORMANT_OVERRIDE,
      });

      const state = determineCardState(input);

      assert.strictEqual(state.activeValue, NON_CONFORMANT_OVERRIDE);
      assert.strictEqual(state.hasOverride, true);
      assert.strictEqual(state.isConformant, false);
      assert.strictEqual(state.showConformantHeader, false);
      assert.strictEqual(state.showWarningIcon, true);
    });
  });

  describe('Override to original value (revert to original)', () => {

    it('original is conformant → conformant header', () => {
      const conformantOriginal = 'Lung Cancer';
      const input = createInput({
        originalValue: conformantOriginal,
        aiSuggestedValue: 'Breast Cancer',
        overrideValue: conformantOriginal,
      });

      const state = determineCardState(input);

      assert.strictEqual(state.activeValue, conformantOriginal);
      assert.strictEqual(state.isConformant, true);
      assert.strictEqual(state.showConformantHeader, true);
      assert.strictEqual(state.showWarningIcon, false);
    });

    it('original is non-conformant → warning icon, no green header', () => {
      const nonConformantOriginal = 'lung cancer'; // lowercase not in PV set
      const input = createInput({
        originalValue: nonConformantOriginal,
        overrideValue: nonConformantOriginal,
      });

      const state = determineCardState(input);

      assert.strictEqual(state.activeValue, nonConformantOriginal);
      assert.strictEqual(state.isConformant, false);
      assert.strictEqual(state.showConformantHeader, false);
      assert.strictEqual(state.showWarningIcon, true);
    });
  });

  describe('Clear override (revert to AI)', () => {

    it('empty override → back to AI state', () => {
      const input = createInput({
        overrideValue: '',
      });

      const state = determineCardState(input);

      assert.strictEqual(state.activeValue, AI_SUGGESTION);
      assert.strictEqual(state.hasOverride, false);
    });
  });

  describe('Edge cases', () => {

    it('override equals AI suggestion → treated as no override', () => {
      const input = createInput({
        overrideValue: AI_SUGGESTION, // Same as AI
      });

      const state = determineCardState(input);

      assert.strictEqual(state.hasOverride, false);
    });

    it('whitespace differences are preserved (whitespace is semantically significant)', () => {
      const withTrailingSpace = 'Lung Cancer ';
      const input = createInput({
        aiSuggestedValue: 'Lung Cancer',
        overrideValue: withTrailingSpace,
      });

      const state = determineCardState(input);

      // Trailing space means it's different from AI and not in PV set
      assert.strictEqual(state.hasOverride, true);
      assert.strictEqual(state.activeValue, withTrailingSpace);
      assert.strictEqual(state.isConformant, false); // 'Lung Cancer ' !== 'Lung Cancer'
    });

    it('case differences are preserved (case is semantically significant)', () => {
      // PV set has 'Lung Cancer' (title case)
      // Original value is 'lung cancer' (lowercase) - NOT in PV set
      const lowercaseValue = 'lung cancer';
      const input = createInput({
        originalValue: lowercaseValue,
        aiSuggestedValue: 'Lung Cancer',  // AI corrected to title case
        overrideValue: lowercaseValue,     // User reverted to original (lowercase)
        pvSet: createPVSet('Lung Cancer', 'Breast Cancer'),  // PVs are title case
      });

      const state = determineCardState(input);

      // 'lung cancer' !== 'Lung Cancer' so it's NOT conformant
      assert.strictEqual(state.hasOverride, true);
      assert.strictEqual(state.isConformant, false, 'Case differences should make value non-conformant');
      assert.strictEqual(state.showWarningIcon, true, 'Warning should show for case-mismatched value');
    });

    it('null pvSet with hasPVs=false → no errors', () => {
      const input = createInput({
        hasPVs: false,
        pvSet: null,
        overrideValue: 'any value',
      });

      const state = determineCardState(input);

      assert.strictEqual(state.showWarningIcon, false);
      assert.strictEqual(state.showConformantHeader, false);
    });

    it('empty pvSet with hasPVs=true → all overrides non-conformant', () => {
      const input = createInput({
        hasPVs: true,
        pvSet: new Set(),
        overrideValue: 'anything',
        aiIsConformant: false,
      });

      const state = determineCardState(input);

      assert.strictEqual(state.isConformant, false);
      assert.strictEqual(state.showWarningIcon, true);
    });

    it('original equals AI suggestion with override → still has override', () => {
      const sameValue = 'Lung Cancer';
      const input = createInput({
        originalValue: sameValue,
        aiSuggestedValue: sameValue,
        overrideValue: 'some override',
      });

      const state = determineCardState(input);

      assert.strictEqual(state.hasOverride, true);
    });
  });

  describe('State transition scenarios (simulating user actions)', () => {

    it('user types override → AI conformant to override non-conformant', () => {
      // Initial state: using AI (conformant)
      const initial = determineCardState(createInput({ overrideValue: '' }));
      assert.strictEqual(initial.showConformantHeader, true);

      // After typing non-conformant override
      const afterOverride = determineCardState(createInput({ overrideValue: NON_CONFORMANT_OVERRIDE }));
      assert.strictEqual(afterOverride.showWarningIcon, true);
      assert.strictEqual(afterOverride.showConformantHeader, false);
    });

    it('user clicks original to revert → override to original (non-conformant)', () => {
      // Has an override
      const withOverride = determineCardState(createInput({ overrideValue: CONFORMANT_OVERRIDE }));
      assert.strictEqual(withOverride.isConformant, true);

      // User clicks original value to revert
      const afterRevert = determineCardState(createInput({ overrideValue: ORIGINAL }));
      assert.strictEqual(afterRevert.showWarningIcon, true); // original 'lung cancer' not in PV set
    });

    it('user clicks AI suggestion to revert → back to AI state', () => {
      // Has an override
      const withOverride = determineCardState(createInput({ overrideValue: ORIGINAL }));
      assert.strictEqual(withOverride.hasOverride, true);

      // User clicks AI suggestion (clears override)
      const afterRevert = determineCardState(createInput({ overrideValue: '' }));
      assert.strictEqual(afterRevert.hasOverride, false);
      assert.strictEqual(afterRevert.showConformantHeader, true);
    });

    it('conformant AI → non-conformant original → conformant AI', () => {
      // Start: AI suggestion (conformant)
      const step1 = determineCardState(createInput({ overrideValue: '' }));
      assert.strictEqual(step1.showConformantHeader, true);
      assert.strictEqual(step1.showWarningIcon, false);

      // User reverts to original (non-conformant)
      const step2 = determineCardState(createInput({ overrideValue: ORIGINAL }));
      assert.strictEqual(step2.showConformantHeader, false);
      assert.strictEqual(step2.showWarningIcon, true);

      // User clicks AI to revert back
      const step3 = determineCardState(createInput({ overrideValue: '' }));
      assert.strictEqual(step3.showConformantHeader, true);
      assert.strictEqual(step3.showWarningIcon, false);
    });
  });
});

describe('isEffectiveOverride', () => {

  it('returns false for empty string', () => {
    assert.strictEqual(isEffectiveOverride('', AI_SUGGESTION), false);
  });

  it('returns false when override equals AI suggestion', () => {
    assert.strictEqual(isEffectiveOverride(AI_SUGGESTION, AI_SUGGESTION), false);
  });

  it('returns true when override differs from AI suggestion', () => {
    assert.strictEqual(isEffectiveOverride(ORIGINAL, AI_SUGGESTION), true);
  });

  it('returns true for any non-empty value different from AI', () => {
    assert.strictEqual(isEffectiveOverride('x', AI_SUGGESTION), true);
  });
});

describe('Exhaustive truth table', () => {
  const testCases = [
    // { hasPVs, aiConformant, override, expectedWarning, expectedConformant }
    { hasPVs: true,  aiConformant: true,  override: '',                      expectedWarning: false, expectedConformant: true },
    { hasPVs: true,  aiConformant: false, override: '',                      expectedWarning: true,  expectedConformant: false },
    { hasPVs: true,  aiConformant: true,  override: CONFORMANT_OVERRIDE,     expectedWarning: false, expectedConformant: true },
    { hasPVs: true,  aiConformant: true,  override: NON_CONFORMANT_OVERRIDE, expectedWarning: true,  expectedConformant: false },
    { hasPVs: true,  aiConformant: true,  override: ORIGINAL,                expectedWarning: true,  expectedConformant: false },
    { hasPVs: true,  aiConformant: true,  override: AI_SUGGESTION,           expectedWarning: false, expectedConformant: true }, // override=AI means no override
    { hasPVs: false, aiConformant: true,  override: '',                      expectedWarning: false, expectedConformant: false }, // no PVs = neutral
    { hasPVs: false, aiConformant: false, override: 'anything',              expectedWarning: false, expectedConformant: false }, // no PVs = no warning
  ];

  testCases.forEach(({ hasPVs, aiConformant, override, expectedWarning, expectedConformant }) => {
    const pvDesc = hasPVs ? 'has PVs' : 'no PVs';
    const aiDesc = aiConformant ? 'AI conformant' : 'AI non-conformant';
    const overrideDesc = override === '' ? 'no override' : `override="${override}"`;

    it(`${pvDesc}, ${aiDesc}, ${overrideDesc} → warning=${expectedWarning}, conformant=${expectedConformant}`, () => {
      const input = createInput({
        hasPVs,
        pvSet: hasPVs ? STANDARD_PV_SET : null,
        aiIsConformant: aiConformant,
        overrideValue: override,
      });

      const state = determineCardState(input);

      assert.strictEqual(state.showWarningIcon, expectedWarning, `Expected showWarningIcon=${expectedWarning}`);
      assert.strictEqual(state.isConformant, expectedConformant, `Expected isConformant=${expectedConformant}`);
    });
  });
});
