/**
 * Shared utilities for Stage 4 review modes.
 * Contains common constants, helper functions, and rendering logic used by both column and row modes.
 */

import { createPVCombobox } from './pv_combobox.js';
import { determineCardState } from './card-state.js';

/** @type {Record<string, string>} */
export const CONFIDENCE_SYMBOLS = {
  high: '▲',
  medium: '–',
  low: '▼',
};

/** @type {Record<string, string>} */
export const CONFIDENCE_TOOLTIPS = {
  high: 'High confidence – The AI estimates this transformation is likely correct, but verification is still recommended.',
  medium: 'Medium confidence – The AI found a reasonable match. Review suggested.',
  low: 'Low confidence – The AI is uncertain. Manual review recommended.',
};

/** @type {Record<string, string>} */
export const RECOMMENDATION_TYPE = {
  AI_CHANGED: 'ai_changed',
  AI_UNCHANGED: 'ai_unchanged',
  NO_RECOMMENDATION: 'no_recommendation',
};

/** @type {Record<string, string>} */
export const SORT_MODE = {
  ORIGINAL: 'original',
  CONFIDENCE_ASC: 'confidence-asc',
  CONFIDENCE_DESC: 'confidence-desc',
};

/**
 * Numeric sort key for confidence buckets.
 * Lower values = lower confidence (sorted first in ascending order).
 * @type {Record<string, number>}
 */
const CONFIDENCE_SORT_KEY = {
  low: 1,
  medium: 2,
  high: 3,
};

/**
 * Sort entries by confidence level.
 * @param {Array} entries - Array of entry objects with `confidence` (number) or `bucket` (string)
 * @param {string} sortMode - One of 'original', 'confidence-asc', 'confidence-desc'
 * @returns {Array} Sorted array (new array, original not mutated)
 */
export const sortEntriesByConfidence = (entries, sortMode) => {
  if (sortMode === SORT_MODE.ORIGINAL || !sortMode) {
    return entries;
  }

  const sorted = [...entries];
  const ascending = sortMode === SORT_MODE.CONFIDENCE_ASC;

  sorted.sort((a, b) => {
    const aKey = CONFIDENCE_SORT_KEY[a.bucket] ?? a.confidence ?? 0;
    const bKey = CONFIDENCE_SORT_KEY[b.bucket] ?? b.confidence ?? 0;
    return ascending ? aKey - bKey : bKey - aKey;
  });

  return sorted;
};

/**
 * Get the minimum confidence value from an array of cells.
 * Used for sorting rows by their lowest-confidence cell.
 * @param {Array} cells - Array of cell objects
 * @returns {number} Minimum confidence sort key
 */
export const getMinConfidence = (cells) => {
  if (!cells?.length) return Infinity;
  let min = Infinity;
  for (const cell of cells) {
    const key = CONFIDENCE_SORT_KEY[cell.bucket] ?? cell.confidence ?? 0;
    if (key < min) min = key;
  }
  return min;
};

/**
 * Convert data row number to Excel row number.
 * Excel row 1 is the header, so data row N appears as Excel row N+1.
 * @param {number} dataRowNumber - 1-based data row number
 * @returns {number} Excel row number
 */
export const toExcelRowNumber = (dataRowNumber) => dataRowNumber + 1;

/**
 * Extract file_id from URL query parameters.
 * @returns {string|null}
 */
export const getFileIdFromUrl = () => {
  const params = new URLSearchParams(window.location.search);
  return params.get('file_id');
};

/**
 * Escape HTML special characters to prevent XSS.
 * @param {string} str
 * @returns {string}
 */
export const escapeHtml = (str) => {
  if (typeof str !== 'string') return String(str);
  const escapeMap = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return str.replace(/[&<>"']/g, (c) => escapeMap[c]);
};

/**
 * Check if a cell's change is case-only (same text, different letter casing).
 * @param {Object} cell - Cell object with originalValue and harmonizedValue
 * @returns {boolean}
 */
export const isCaseChangeOnly = (cell) => {
  const original = cell.originalValue ?? '';
  const harmonized = cell.harmonizedValue ?? '';
  // Not a case-only change if values are identical
  if (original === harmonized) return false;
  // Case-only if lowercase versions match
  return original.toLowerCase() === harmonized.toLowerCase();
};

/**
 * Check if a cell needs review (has changes or no AI recommendation).
 * @param {Object} cell - Cell object
 * @param {Object} [options] - Filter options
 * @param {boolean} [options.hideCaseOnlyChanges=false] - If true, excludes case-only changes
 * @returns {boolean}
 */
export const cellNeedsReview = (cell, options = {}) => {
  const { hideCaseOnlyChanges = false } = options;

  const original = cell.originalValue ?? '';
  const harmonized = cell.harmonizedValue ?? '';

  // Nothing to review if no original value
  if (!original) return false;

  // Include cells with no AI recommendation (but only if there's an original value)
  if (cell.recommendationType === RECOMMENDATION_TYPE.NO_RECOMMENDATION) {
    return true;
  }

  // No change means no review needed
  if (original === harmonized) return false;

  // If filtering case-only changes and this is one, skip it
  if (hideCaseOnlyChanges && isCaseChangeOnly(cell)) {
    return false;
  }

  return true;
};

/**
 * Check if a row has any cells that need review.
 * Includes cells where AI changed the value OR where no AI recommendation was provided.
 * Note: whitespace is semantically significant in ontological harmonization.
 * @param {Object} row - Row object containing cells array
 * @param {Object} [options] - Filter options passed to cellNeedsReview
 * @returns {boolean}
 */
export const rowHasChanges = (row, options = {}) => {
  if (!row?.cells) return false;
  for (const cell of row.cells) {
    if (cellNeedsReview(cell, options)) {
      return true;
    }
  }
  return false;
};

/**
 * Get cells from a row that need review.
 * Includes cells where AI changed value OR no AI recommendation was provided.
 * Note: whitespace is semantically significant in ontological harmonization.
 * @param {Object} row - Row object containing cells array
 * @param {Object} [options] - Filter options passed to cellNeedsReview
 * @returns {Array} Array of cells needing review
 */
export const getChangedCells = (row, options = {}) => {
  if (!row?.cells) return [];
  const reviewCells = [];
  for (const cell of row.cells) {
    if (cellNeedsReview(cell, options)) {
      reviewCells.push(cell);
    }
  }
  return reviewCells;
};

/**
 * Clean up all value cards in a container before removing them.
 * Calls destroy() on each card to prevent memory leaks from event listeners.
 * @param {HTMLElement} container - Container with value cards
 */
export const cleanupCards = (container) => {
  const cards = container.querySelectorAll('.row-cell');
  for (const card of cards) {
    if (typeof card.destroy === 'function') {
      card.destroy();
    }
  }
};

/**
 * Create the empty state element shown when no entries are available.
 * @returns {HTMLElement}
 */
export const createEmptyState = () => {
  const empty = document.createElement('div');
  empty.className = 'review-empty';

  const primaryMsg = document.createElement('p');
  primaryMsg.textContent = 'No harmonized changes to review.';
  empty.appendChild(primaryMsg);

  const secondaryMsg = document.createElement('p');
  secondaryMsg.textContent = 'All recommendations match the original input values.';
  empty.appendChild(secondaryMsg);

  return empty;
};

/**
 * Build CSS classes for a value card based on entry state.
 * @param {Object} entry
 * @returns {string}
 */
const _buildCardClasses = (entry) => {
  const classes = ['row-cell', `confidence-${entry.bucket}`];

  // Add recommendation type class for special states
  if (entry.recommendationType === RECOMMENDATION_TYPE.NO_RECOMMENDATION) {
    classes.push('no-recommendation');
  } else if (entry.harmonizedValue === null) {
    classes.push('needs-review');
  }

  return classes.join(' ');
};

/**
 * Get the existing override value for an entry.
 * @param {Object} entry - Entry with rowIndices array (must have at least one element)
 * @param {Object} pendingOverrides
 * @returns {string}
 */
const _getInputValue = (entry, pendingOverrides) => {
  if (!entry.rowIndices?.length) {
    return entry.manualOverride ?? '';
  }
  const firstRowIndex = entry.rowIndices[0];
  const existingOverride = pendingOverrides[String(firstRowIndex)]?.[entry.columnKey];
  return existingOverride?.human_value ?? entry.manualOverride ?? '';
};

/**
 * Pre-render both warning and conformant icons, toggling visibility dynamically.
 * Creating icons upfront avoids DOM thrashing when users type overrides.
 * @param {Object} params
 * @returns {string}
 */
const _buildCardHTML = (params) => {
  const { columnLabel, labelText, confidenceSymbol, confidenceTooltip, bucket, recommendedText, recommendedClass, originalValue, inputValue, isPVConformant, hasPVs } = params;
  const safeColumnLabel = escapeHtml(columnLabel);
  const safeLabelText = escapeHtml(labelText);
  const safeRecommendedText = escapeHtml(recommendedText);
  const safeOriginalValue = escapeHtml(originalValue);
  const safeInputValue = escapeHtml(inputValue);

  // Both icons always present when PVs exist - toggle visibility based on conformance
  // This allows dynamic show/hide when user changes override values
  const warningHidden = isPVConformant ? ' style="display: none;"' : '';
  const checkHidden = isPVConformant ? '' : ' style="display: none;"';
  const pvStatusIcons = hasPVs
    ? `<span class="pv-warning-icon" data-tooltip="This current suggestion isn't an approved value, but it might point you in the right direction." aria-label="Warning: value not in permissible values"${warningHidden}>⚠</span><span class="pv-conformant-icon" aria-label="Value is in permissible values"${checkHidden}>✓</span>`
    : '';

  // Add conformant class to header when value is in PV list
  const headerClasses = ['card-header-row'];
  if (isPVConformant) {
    headerClasses.push('pv-conformant');
  }

  return `
    <div class="${headerClasses.join(' ')}">
      <span class="confidence-indicator confidence-${bucket}" data-tooltip="${confidenceTooltip}" aria-label="${bucket} confidence">${confidenceSymbol}</span>
      <div class="entry-row-label">${safeLabelText}</div>
      ${pvStatusIcons}
    </div>
    <div class="value-pair" role="group" aria-label="${safeColumnLabel} comparison">
      <div class="value-group original">
        <p class="value-text original-text">${safeOriginalValue}</p>
      </div>
      <div class="transformation-arrow" aria-hidden="true">
        <svg class="arrow-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <line x1="12" y1="5" x2="12" y2="19"></line>
          <polyline points="6 13 12 19 18 13"></polyline>
        </svg>
      </div>
      <div class="value-group recommended">
        <p class="value-text recommended-text${recommendedClass}">${safeRecommendedText}</p>
      </div>
      <label class="value-group value-override">
        <span class="value-label sr-only">Override ${safeColumnLabel}</span>
        <span class="value-input-wrapper">
          <svg class="value-input-icon" viewBox="0 0 20 20" aria-hidden="true">
            <path d="M2 14.5V18h3.5l8.4-8.4-3.5-3.5L2 14.5zm11.8-9.1a1 1 0 0 1 1.4 0l1.4 1.4a1 1 0 0 1 0 1.4l-1.2 1.2-3.5-3.5 1.2-1.2z"/>
          </svg>
          <input
            class="value-input"
            type="text"
            value="${safeInputValue}"
            aria-label="Manual override for ${safeColumnLabel}"
          />
        </span>
      </label>
    </div>
  `;
};

/**
 * Apply card display state to DOM elements.
 * Uses determineCardState for all state logic, then applies visual updates.
 * @param {Object} params
 * @param {HTMLElement} params.card - The card element
 * @param {HTMLElement} params.recommendedEl - Element displaying the harmonized value
 * @param {HTMLElement|null} params.originalEl - Element displaying the original value
 * @param {string} params.originalValue - Original value from source data
 * @param {string} params.aiSuggestedValue - AI harmonized value
 * @param {string} params.overrideValue - User's override (empty string = no override)
 * @param {boolean} params.hasPVs - Whether PVs exist for this column
 * @param {Set<string>|null} params.pvSet - Set of valid PVs
 * @param {boolean} params.aiIsConformant - Whether AI suggestion is PV-conformant
 */
const _applyCardState = (params) => {
  const {
    card,
    recommendedEl,
    originalEl,
    originalValue,
    aiSuggestedValue,
    overrideValue,
    hasPVs,
    pvSet,
    aiIsConformant,
  } = params;

  // Get derived state from pure function
  const state = determineCardState({
    originalValue,
    aiSuggestedValue,
    overrideValue,
    hasPVs,
    pvSet,
    aiIsConformant,
  });

  // Apply override display
  if (state.hasOverride) {
    recommendedEl.innerHTML = `<span class="ai-suggestion-struck revert-link">${escapeHtml(aiSuggestedValue)}</span><span class="override-value">${escapeHtml(overrideValue)}</span>`;
    recommendedEl.classList.add('has-override');
  } else {
    recommendedEl.textContent = aiSuggestedValue;
    recommendedEl.classList.remove('has-override');
  }

  // Apply original value clickable state
  if (originalEl) {
    originalEl.classList.toggle('revert-link', state.originalIsClickable);
  }

  // Apply PV conformance styling (only when PVs exist)
  const headerRow = card.querySelector('.card-header-row');
  const warningIcon = card.querySelector('.pv-warning-icon');
  const conformantIcon = card.querySelector('.pv-conformant-icon');

  // Use data-hidden attribute for CSS selector reliability (avoids fragile style matching)
  if (warningIcon) {
    warningIcon.style.display = state.showWarningIcon ? '' : 'none';
    warningIcon.dataset.hidden = state.showWarningIcon ? 'false' : 'true';
  }

  if (conformantIcon) {
    conformantIcon.style.display = state.showConformantHeader ? '' : 'none';
  }

  if (headerRow) {
    headerRow.classList.toggle('pv-conformant', state.showConformantHeader);
  }

  return state;
};

/**
 * Attach input listener to card for override changes.
 * Used for cards without PVs (plain text input).
 * Also attaches revert click handlers for original and AI suggestion values.
 * @param {HTMLElement} card
 * @param {Object} entry
 * @param {Function} onOverrideChange
 * @returns {Function} Cleanup function to remove event listeners
 */
const _attachInputListener = (card, entry, onOverrideChange) => {
  const input = card.querySelector('.value-input');
  const recommendedEl = card.querySelector('.recommended-text');
  const originalEl = card.querySelector('.original-text');
  if (!input || !recommendedEl) return () => {};

  const originalValue = entry.originalValue ?? '';
  const aiSuggestedValue = entry.harmonizedValue ?? entry.originalValue ?? '—';
  // No PVs for this card (text input mode)
  const hasPVs = false;
  const pvSet = null;
  const aiIsConformant = false;

  // Helper to apply state changes
  const applyState = (overrideValue) => {
    _applyCardState({
      card,
      recommendedEl,
      originalEl,
      originalValue,
      aiSuggestedValue,
      overrideValue,
      hasPVs,
      pvSet,
      aiIsConformant,
    });
  };

  // Shared function to apply a value change (from input or revert click)
  const applyValueChange = (value) => {
    const effectiveOverride = value === aiSuggestedValue ? '' : value;
    applyState(effectiveOverride);

    // Clear input after revert clicks - card display shows the value, input is just for entering
    input.value = '';

    onOverrideChange(
      entry.rowIndices,
      entry.columnKey,
      entry.harmonizedValue,
      effectiveOverride,
      entry.originalValue,
    );
  };

  input.addEventListener('input', (event) => {
    const overrideValue = event.target.value;
    const effectiveOverride = overrideValue === aiSuggestedValue ? '' : overrideValue;
    applyState(effectiveOverride);

    onOverrideChange(
      entry.rowIndices,
      entry.columnKey,
      entry.harmonizedValue,
      effectiveOverride,
      entry.originalValue,
    );
  });

  // On blur, clear the input - the card display shows the override value
  // Input is just for entering new values, not displaying current state
  input.addEventListener('blur', () => {
    const currentValue = input.value;
    if (currentValue === aiSuggestedValue) {
      // Typing AI suggestion = no override
      applyState('');
    }
    // Always clear input after blur - card shows the effective value
    input.value = '';
  });

  // Initialize state if there's already an override value
  const initialOverride = input.value;
  if (initialOverride && initialOverride !== aiSuggestedValue) {
    applyState(initialOverride);
  }

  // Attach revert click handlers
  // Pass resetInput to clear text input after clicking revert links
  const revertCleanup = _attachRevertClickHandlers(card, entry, aiSuggestedValue, applyValueChange, {
    resetInput: () => { input.value = ''; },
  });

  return revertCleanup;
};

/**
 * Add tooltip to row label element if provided.
 * @param {HTMLElement} card
 * @param {string|null} tooltipText
 */
const _addTooltip = (card, tooltipText) => {
  if (!tooltipText) return;

  const rowLabelEl = card.querySelector('.entry-row-label');
  if (rowLabelEl) {
    rowLabelEl.classList.add('has-tooltip');
    rowLabelEl.dataset.tooltip = escapeHtml(tooltipText);
  }
};

/**
 * Attach JS-based tooltip to warning icon for fixed positioning.
 * Returns cleanup function to remove listeners and orphaned tooltips.
 * @param {HTMLElement} card
 * @returns {Function|null} Cleanup function, or null if no warning icon
 */
const _attachWarningTooltip = (card) => {
  const warningIcon = card.querySelector('.pv-warning-icon');
  if (!warningIcon) return null;

  let tooltip = null;

  const showTooltip = () => {
    if (tooltip) return;

    tooltip = document.createElement('div');
    tooltip.className = 'pv-warning-tooltip';
    tooltip.textContent = warningIcon.dataset.tooltip;
    document.body.appendChild(tooltip);

    // Position tooltip below the warning icon, centered
    const iconRect = warningIcon.getBoundingClientRect();
    const tooltipRect = tooltip.getBoundingClientRect();
    let left = iconRect.left + (iconRect.width / 2) - (tooltipRect.width / 2);
    left = Math.max(10, Math.min(left, window.innerWidth - tooltipRect.width - 10));

    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${iconRect.bottom + 6}px`;
  };

  const hideTooltip = () => {
    if (tooltip) {
      tooltip.remove();
      tooltip = null;
    }
  };

  warningIcon.addEventListener('mouseenter', showTooltip);
  warningIcon.addEventListener('mouseleave', hideTooltip);

  // Return cleanup function
  return () => {
    hideTooltip();
    warningIcon.removeEventListener('mouseenter', showTooltip);
    warningIcon.removeEventListener('mouseleave', hideTooltip);
  };
};

/**
 * Attach revert click handlers to original and AI suggestion elements.
 * Allows users to click to revert to original value or clear override (revert to AI).
 * @param {HTMLElement} card - The card element
 * @param {Object} entry - Entry with values
 * @param {string} aiSuggestedValue - The AI's suggested value
 * @param {Function} triggerChange - Function to call when revert is clicked
 * @param {Object} [options] - Additional options
 * @param {Function} [options.resetInput] - Function to clear the input/combobox after revert
 * @returns {Function} Cleanup function to remove event listeners
 */
const _attachRevertClickHandlers = (card, entry, aiSuggestedValue, triggerChange, options = {}) => {
  const { resetInput } = options;
  const recommendedEl = card.querySelector('.recommended-text');
  const originalEl = card.querySelector('.original-text');
  const originalValue = entry.originalValue ?? '';

  // Click on AI suggestion (struck through) -> revert to AI (clear override)
  const handleAIClick = (event) => {
    // Only respond to clicks on the AI suggestion span
    if (!event.target.classList.contains('ai-suggestion-struck')) return;
    if (!event.target.classList.contains('revert-link')) return;
    triggerChange('');  // Empty string clears override
    if (resetInput) resetInput();
  };

  // Click on original value -> set override to original value
  const handleOriginalClick = () => {
    if (!originalEl?.classList.contains('revert-link')) return;
    // Only trigger if original differs from current AI suggestion
    if (originalValue !== aiSuggestedValue) {
      triggerChange(originalValue);
      if (resetInput) resetInput();
    }
  };

  if (recommendedEl) {
    recommendedEl.addEventListener('click', handleAIClick);
  }
  if (originalEl) {
    originalEl.addEventListener('click', handleOriginalClick);
  }

  // Return cleanup function
  return () => {
    if (recommendedEl) {
      recommendedEl.removeEventListener('click', handleAIClick);
    }
    if (originalEl) {
      originalEl.removeEventListener('click', handleOriginalClick);
    }
  };
};

/**
 * Attach PV combobox to card's override area.
 * Returns cleanup function to destroy combobox.
 * @param {HTMLElement} card - The card element
 * @param {Object} entry - Entry with topSuggestions and columnKey
 * @param {string[]} pvValues - Alphabetized list of valid PVs
 * @param {string} initialValue - Current override value
 * @param {Function} onOverrideChange - Callback for override changes
 * @returns {Function|null} Cleanup function, or null if no input wrapper
 */
const _attachPVCombobox = (card, entry, pvValues, initialValue, onOverrideChange) => {
  const inputWrapper = card.querySelector('.value-input-wrapper');
  const recommendedEl = card.querySelector('.recommended-text');
  const originalEl = card.querySelector('.original-text');
  if (!inputWrapper || !recommendedEl) return null;

  const originalValue = entry.originalValue ?? '';
  const aiSuggestedValue = entry.harmonizedValue ?? entry.originalValue ?? '—';
  const aiIsConformant = entry.isPVConformant;
  const hasPVs = entry.pvSetAvailable;
  // Build Set for O(1) conformance checks
  const pvSet = new Set(pvValues);

  // Clear the wrapper and add PV combobox
  inputWrapper.innerHTML = '';

  // If initial value matches AI suggestion, don't pass it as override
  const effectiveInitialValue = initialValue === aiSuggestedValue ? '' : initialValue;

  // Apply initial card state using centralized state logic
  _applyCardState({
    card,
    recommendedEl,
    originalEl,
    originalValue,
    aiSuggestedValue,
    overrideValue: effectiveInitialValue,
    hasPVs,
    pvSet,
    aiIsConformant,
  });

  // Shared function to apply a value change (from combobox or revert click)
  const applyValueChange = (value) => {
    const effectiveOverride = value === aiSuggestedValue ? '' : value;

    // Apply all state updates through centralized function
    _applyCardState({
      card,
      recommendedEl,
      originalEl,
      originalValue,
      aiSuggestedValue,
      overrideValue: effectiveOverride,
      hasPVs,
      pvSet,
      aiIsConformant,
    });

    // Reset the combobox input if back to AI suggestion
    if (effectiveOverride === '' && combobox.reset) {
      combobox.reset();
    }

    // Notify parent
    onOverrideChange(
      entry.rowIndices,
      entry.columnKey,
      entry.harmonizedValue,
      effectiveOverride,
      entry.originalValue,
    );
  };

  const suggestions = entry.topSuggestions ?? [];
  const combobox = createPVCombobox({
    suggestions,
    pvValues,
    initialValue: effectiveInitialValue,
    onChange: applyValueChange,
  });

  inputWrapper.appendChild(combobox);

  // Attach revert click handlers for original and AI suggestion
  // Pass resetInput to clear combobox after clicking revert links
  const revertCleanup = _attachRevertClickHandlers(card, entry, aiSuggestedValue, applyValueChange, {
    resetInput: () => combobox.reset?.(),
  });

  // Return cleanup function
  return () => {
    revertCleanup();
    if (combobox.destroy) {
      combobox.destroy();
    }
  };
};

/**
 * Create a value card for displaying an entry's original, recommended, and override values.
 * Used by both column and row modes.
 * @param {Object} config
 * @param {Object} config.entry - Entry object with rowIndices (always an array), columnKey, values, etc.
 * @param {string} config.labelText - Text to display in the card header
 * @param {string|null} config.tooltipText - Optional tooltip for the label
 * @param {Object} config.pendingOverrides - Map of pending overrides by row index
 * @param {Function} config.onOverrideChange - Callback when override value changes
 * @param {Object} [config.columnPVs] - Optional map of column_key -> PV list
 * @returns {HTMLElement}
 */
export const createValueCard = (config) => {
  const { entry, labelText, tooltipText, pendingOverrides, onOverrideChange, columnPVs } = config;

  const card = document.createElement('div');
  card.className = _buildCardClasses(entry);

  const columnLabel = entry.columnLabel ?? entry.columnKey ?? '';
  const inputValue = _getInputValue(entry, pendingOverrides);

  // Determine display text based on recommendation type
  const isNoRecommendation = entry.recommendationType === RECOMMENDATION_TYPE.NO_RECOMMENDATION;
  const recommendedText = isNoRecommendation
    ? 'No AI recommendation'
    : (entry.harmonizedValue ?? entry.originalValue ?? '—');
  const recommendedClass = isNoRecommendation
    ? ' no-recommendation-text'
    : (entry.harmonizedValue === null ? ' missing' : '');
  const confidenceSymbol = CONFIDENCE_SYMBOLS[entry.bucket] ?? '?';
  const confidenceTooltip = CONFIDENCE_TOOLTIPS[entry.bucket] ?? '';

  // Check if value is conformant (PVs available AND value matches)
  const isPVConformant = entry.pvSetAvailable && entry.isPVConformant;

  card.innerHTML = _buildCardHTML({
    columnLabel,
    labelText,
    confidenceSymbol,
    confidenceTooltip,
    bucket: entry.bucket,
    recommendedText,
    recommendedClass,
    originalValue: entry.originalValue ?? '—',
    inputValue: '',  // Input stays empty - card display shows the value, input is just for entering
    isPVConformant,
    hasPVs: entry.pvSetAvailable,
  });

  // Collect cleanup functions for proper resource management
  const cleanupFns = [];

  // Use PV combobox if PVs are available for this column, otherwise use text input
  const pvValues = columnPVs?.[entry.columnKey];
  if (entry.pvSetAvailable && pvValues?.length > 0) {
    const comboboxCleanup = _attachPVCombobox(card, entry, pvValues, inputValue, onOverrideChange);
    if (comboboxCleanup) cleanupFns.push(comboboxCleanup);
  } else {
    const inputCleanup = _attachInputListener(card, entry, onOverrideChange);
    if (inputCleanup) cleanupFns.push(inputCleanup);
  }

  _addTooltip(card, tooltipText);
  const tooltipCleanup = _attachWarningTooltip(card);
  if (tooltipCleanup) cleanupFns.push(tooltipCleanup);

  // Attach cleanup method to card for resource management when cards are removed
  card.destroy = () => {
    for (const cleanup of cleanupFns) {
      cleanup();
    }
  };

  return card;
};

/**
 * Create a progress pill button element.
 * @param {Object} params
 * @returns {HTMLButtonElement}
 */
const _createProgressPill = (params) => {
  const { summary, status, isCurrent, isColumnMode, getLabelForSummary, getAriaLabelForSummary, currentUnit, onUnitClick } = params;

  const pill = document.createElement('button');
  pill.type = 'button';

  const pillClasses = ['batch-progress-item', status];
  if (isColumnMode) {
    pillClasses.push('column-pill');
  }
  if (isCurrent) {
    pillClasses.push('current');
  }
  pill.className = pillClasses.join(' ');

  pill.textContent = getLabelForSummary(summary);
  pill.setAttribute('aria-label', getAriaLabelForSummary(summary, status));

  pill.addEventListener('click', () => {
    if (summary.unitIndex !== currentUnit) {
      onUnitClick(summary.unitIndex);
    }
  });

  return pill;
};

/**
 * Create placeholder pill shown when no entries exist.
 * @returns {HTMLButtonElement}
 */
const _createPlaceholderPill = () => {
  const placeholder = document.createElement('button');
  placeholder.type = 'button';
  placeholder.className = 'batch-progress-item pending';
  placeholder.textContent = '—';
  placeholder.disabled = true;
  placeholder.setAttribute('aria-label', 'No harmonized entries yet');
  return placeholder;
};

/**
 * Render progress pills into a container.
 * Used by both column and row modes with different label formatters.
 * @param {Object} config
 * @param {HTMLElement} config.container - Container element for pills
 * @param {Array} config.summaries - Array of unit summaries
 * @param {number} config.currentUnit - Currently selected unit index
 * @param {Function} config.onUnitClick - Callback when a unit is clicked
 * @param {Function} config.getLabelForSummary - Returns display label for a summary
 * @param {Function} config.getAriaLabelForSummary - Returns aria label for a summary
 * @param {boolean} config.isColumnMode - Whether we're in column mode (affects pill styling)
 */
export const renderProgressPills = (config) => {
  const {
    container,
    summaries,
    currentUnit,
    onUnitClick,
    getLabelForSummary,
    getAriaLabelForSummary,
    isColumnMode,
  } = config;

  container.innerHTML = '';

  const meaningful = summaries.filter((s) => (s.entryCount ?? s.rowCount ?? 0) > 0);

  if (!meaningful.length) {
    container.append(_createPlaceholderPill());
    return;
  }

  for (const summary of meaningful) {
    const isCurrent = summary.unitIndex === currentUnit;

    const pill = _createProgressPill({
      summary,
      status: 'pending',
      isCurrent,
      isColumnMode,
      getLabelForSummary,
      getAriaLabelForSummary,
      currentUnit,
      onUnitClick,
    });

    container.append(pill);
  }
};
