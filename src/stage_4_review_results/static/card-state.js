/**
 * Pure functions for determining value card display state.
 * No DOM manipulation - just state derivation from inputs.
 *
 * This module centralizes all state logic for Stage 4 value cards,
 * making it testable independently of the DOM rendering layer.
 */

import { isMissingValue } from '/assets/shared/value-presence.js';

/**
 * @typedef {Object} CardStateInput
 * @property {string} baselineValue - Model result, or source value when no result exists
 * @property {string} overrideValue - User's manual override (empty string = no override)
 * @property {boolean} hasPVs - Whether permissible values exist for this column
 * @property {Set<string>|null} pvSet - Set of valid PVs (null if hasPVs is false)
 */

/**
 * @typedef {Object} CardDisplayState
 * @property {string} activeValue - The currently active value
 * @property {boolean} isConformant - Whether active value is PV-conformant
 * @property {boolean} hasOverride - Whether user has an override that differs from AI
 * @property {boolean} showWarningIcon - Whether to show PV warning icon
 * @property {boolean} showConformantHeader - Whether to show the approved-value icon in the header
 */

/**
 * Check whether an override value represents a real change from the baseline.
 * @param {string} overrideValue - User's override (empty string = no override)
 * @param {string} baselineValue - Model result, or source value when no result exists
 * @returns {boolean}
 */
export const isEffectiveOverride = (overrideValue, baselineValue) =>
  overrideValue !== '' && overrideValue !== baselineValue;

/**
 * Determine the complete display state for a value card.
 * Pure function - no side effects, deterministic output for given input.
 * @param {CardStateInput} input
 * @returns {CardDisplayState}
 */
export const determineCardState = (input) => {
  const {
    baselineValue,
    overrideValue,
    hasPVs,
    pvSet,
  } = input;

  const hasOverride = isEffectiveOverride(overrideValue, baselineValue);

  // Derive: what value is currently "active"?
  const activeValue = hasOverride ? overrideValue : baselineValue;

  // Check the displayed value, not the server flag for a previously saved edit.
  // Missing data is neither an approved term nor an invalid review value.
  const hasValue = !isMissingValue(activeValue);
  const isConformant = hasPVs && hasValue && pvSet !== null && pvSet.has(activeValue);

  return {
    activeValue,
    isConformant,
    hasOverride,
    // Only show warning/conformant styling when PVs exist
    showWarningIcon: hasPVs && hasValue && !isConformant,
    showConformantHeader: hasPVs && isConformant,
  };
};
