/**
 * Pure functions for determining value card display state.
 * No DOM manipulation - just state derivation from inputs.
 *
 * This module centralizes all state logic for Stage 4 value cards,
 * making it testable independently of the DOM rendering layer.
 */

/**
 * @typedef {Object} CardStateInput
 * @property {string} originalValue - Original value from source data
 * @property {string} aiSuggestedValue - AI harmonized value
 * @property {string} overrideValue - User's manual override (empty string = no override)
 * @property {boolean} hasPVs - Whether permissible values exist for this column
 * @property {Set<string>|null} pvSet - Set of valid PVs (null if hasPVs is false)
 * @property {boolean} aiIsConformant - Whether AI suggestion is PV-conformant
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
 * Determine the complete display state for a value card.
 * Pure function - no side effects, deterministic output for given input.
 * @param {CardStateInput} input
 * @returns {CardDisplayState}
 */
export const determineCardState = (input) => {
  const {
    originalValue,
    aiSuggestedValue,
    overrideValue,
    hasPVs,
    pvSet,
    aiIsConformant,
    overrideIsKnownConformant,
  } = input;

  // Derive: is there an override that differs from AI?
  // Empty string or matching AI suggestion = no effective override
  const hasOverride = overrideValue !== '' && overrideValue !== aiSuggestedValue;

  // Derive: what value is currently "active"?
  const activeValue = hasOverride ? overrideValue : aiSuggestedValue;

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
    // Using AI suggestion - use pre-computed conformance flag
    isConformant = aiIsConformant;
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
