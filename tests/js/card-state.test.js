/** Pure display-rule tests for Stage 4 value cards. */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { determineCardState } from '../../src/stage_4_review_results/static/card-state.js';

const AI_SUGGESTION = 'Lung Cancer';
const PV_SET = new Set(['Lung Cancer', 'Breast Cancer', 'Colon Cancer']);

const createInput = (overrides = {}) => ({
  originalValue: 'lung cancer',
  aiSuggestedValue: AI_SUGGESTION,
  overrideValue: '',
  hasPVs: true,
  pvSet: PV_SET,
  aiIsConformant: true,
  ...overrides,
});

describe('value card display state', () => {
  const scenarios = [
    {
      name: 'shows a conformant AI suggestion as approved',
      input: {},
      expected: {
        activeValue: AI_SUGGESTION,
        isConformant: true,
        hasOverride: false,
        showWarningIcon: false,
        showConformantHeader: true,
      },
    },
    {
      name: 'warns when the AI suggestion is not permissible',
      input: { aiIsConformant: false },
      expected: {
        activeValue: AI_SUGGESTION,
        isConformant: false,
        hasOverride: false,
        showWarningIcon: true,
        showConformantHeader: false,
      },
    },
    {
      name: 'shows a permissible manual value as approved',
      input: { overrideValue: 'Breast Cancer' },
      expected: {
        activeValue: 'Breast Cancer',
        isConformant: true,
        hasOverride: true,
        showWarningIcon: false,
        showConformantHeader: true,
      },
    },
    {
      name: 'trusts a value selected from the verified permissible-value modal',
      input: {
        overrideValue: 'Verified Value From Modal',
        overrideIsKnownConformant: true,
      },
      expected: {
        activeValue: 'Verified Value From Modal',
        isConformant: true,
        hasOverride: true,
        showWarningIcon: false,
        showConformantHeader: true,
      },
    },
    {
      name: 'warns when a typed manual value is not permissible',
      input: { overrideValue: 'invalid value' },
      expected: {
        activeValue: 'invalid value',
        isConformant: false,
        hasOverride: true,
        showWarningIcon: true,
        showConformantHeader: false,
      },
    },
    {
      name: 'treats selecting the AI value as clearing the override',
      input: { overrideValue: AI_SUGGESTION },
      expected: {
        activeValue: AI_SUGGESTION,
        isConformant: true,
        hasOverride: false,
        showWarningIcon: false,
        showConformantHeader: true,
      },
    },
    {
      name: 'keeps columns without permissible values visually neutral',
      input: { hasPVs: false, pvSet: null },
      expected: {
        activeValue: AI_SUGGESTION,
        isConformant: false,
        hasOverride: false,
        showWarningIcon: false,
        showConformantHeader: false,
      },
    },
    {
      name: 'keeps manual values neutral when permissible values do not apply',
      input: { hasPVs: false, pvSet: null, overrideValue: 'free text' },
      expected: {
        activeValue: 'free text',
        isConformant: false,
        hasOverride: true,
        showWarningIcon: false,
        showConformantHeader: false,
      },
    },
    {
      name: 'preserves case differences when checking permissible values',
      input: { overrideValue: 'lung cancer' },
      expected: {
        activeValue: 'lung cancer',
        isConformant: false,
        hasOverride: true,
        showWarningIcon: true,
        showConformantHeader: false,
      },
    },
    {
      name: 'preserves whitespace differences when checking permissible values',
      input: { overrideValue: 'Lung Cancer ' },
      expected: {
        activeValue: 'Lung Cancer ',
        isConformant: false,
        hasOverride: true,
        showWarningIcon: true,
        showConformantHeader: false,
      },
    },
  ];

  for (const scenario of scenarios) {
    it(scenario.name, () => {
      assert.deepEqual(determineCardState(createInput(scenario.input)), scenario.expected);
    });
  }
});
