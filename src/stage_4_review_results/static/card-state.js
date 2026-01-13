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
 */

/**
 * @typedef {'ai'|'override_conformant'|'override_non_conformant'|'original'} ActiveValueType
 */

/**
 * @typedef {Object} CardDisplayState
 * @property {ActiveValueType} activeValueType - Type of value currently active
 * @property {string} activeValue - The currently active value
 * @property {boolean} isConformant - Whether active value is PV-conformant
 * @property {boolean} hasOverride - Whether user has an override that differs from AI
 * @property {boolean} showWarningIcon - Whether to show PV warning icon
 * @property {boolean} showConformantHeader - Whether to show green conformant header
 * @property {boolean} originalIsClickable - Whether original value should be clickable (revert link)
 * @property {boolean} aiIsClickable - Whether AI suggestion should be clickable (revert link)
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
    // Check override against PV set
    isConformant = pvSet !== null && pvSet.has(overrideValue);
  } else {
    // Using AI suggestion - use pre-computed conformance flag
    isConformant = aiIsConformant;
  }

  // Derive: active value type for debugging/testing
  /** @type {ActiveValueType} */
  let activeValueType;
  if (!hasOverride) {
    activeValueType = 'ai';
  } else if (overrideValue === originalValue) {
    activeValueType = 'original';
  } else if (isConformant) {
    activeValueType = 'override_conformant';
  } else {
    activeValueType = 'override_non_conformant';
  }

  return {
    activeValueType,
    activeValue,
    isConformant,
    hasOverride,
    // Only show warning/conformant styling when PVs exist
    showWarningIcon: hasPVs && !isConformant,
    showConformantHeader: hasPVs && isConformant,
    // Revert links only available when there's an override
    // Original is only clickable if it differs from AI (clicking when same would be a no-op)
    originalIsClickable: hasOverride && originalValue !== aiSuggestedValue,
    aiIsClickable: hasOverride,
  };
};

/**
 * Check if override value should be treated as "no override".
 * Override equals AI suggestion or is empty = effectively no override.
 * @param {string} overrideValue
 * @param {string} aiSuggestedValue
 * @returns {boolean}
 */
export const isEffectiveOverride = (overrideValue, aiSuggestedValue) => {
  return overrideValue !== '' && overrideValue !== aiSuggestedValue;
};
