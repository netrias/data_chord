/**
 * Pure functions for determining value card display state.
 * No DOM manipulation - just state derivation from inputs.
 *
 * This module centralizes all state logic for Stage 4 value cards,
 * making it testable independently of the DOM rendering layer.
 */

/**
 * @typedef {Object} CardStateInput
 * @property {string} baselineValue - Model result, or source value when no result exists
 * @property {string} overrideValue - User's manual override (empty string = no override)
 * @property {boolean} hasPVs - Whether permissible values exist for this column
 * @property {Set<string>|null} pvSet - Set of valid PVs (null if hasPVs is false)
 * @property {boolean} baselineIsConformant - Whether the baseline value is PV-conformant
 * @property {boolean} [overrideIsKnownConformant] - If true, skip pvSet check for override (value came from verified dropdown selection)
 */

/**
 * @typedef {Object} CardDisplayState
 * @property {string} activeValue - The currently active value
 * @property {boolean} isConformant - Whether active value is PV-conformant
 * @property {boolean} hasOverride - Whether user has an override that differs from AI
 * @property {boolean} showWarningIcon - Whether to show PV warning icon
 * @property {boolean} showConformantHeader - Whether to show green conformant header
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
    baselineIsConformant,
    overrideIsKnownConformant,
  } = input;

  const hasOverride = isEffectiveOverride(overrideValue, baselineValue);

  // Derive: what value is currently "active"?
  const activeValue = hasOverride ? overrideValue : baselineValue;

  // Derive: is the active value conformant?
  // If no PVs exist for this column, treat as neutral (not conformant, not non-conformant)
  let isConformant;
  if (!hasPVs) {
    // No PVs = conformance doesn't apply
    isConformant = false;
  } else if (hasOverride) {
    // Trust the flag when value came from a verified dropdown selection
    isConformant = overrideIsKnownConformant === true
      ? true
      : pvSet !== null && pvSet.has(overrideValue);
  } else {
    // The server has already checked the baseline value.
    isConformant = baselineIsConformant;
  }

  return {
    activeValue,
    isConformant,
    hasOverride,
    // Only show warning/conformant styling when PVs exist
    showWarningIcon: hasPVs && !isConformant,
    showConformantHeader: hasPVs && isConformant,
  };
};
