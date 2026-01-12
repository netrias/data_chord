/**
 * Shared utilities for Stage 4 review modes.
 * Contains common constants, helper functions, and rendering logic used by both column and row modes.
 */

import { createPVCombobox } from './pv_combobox.js';

/** @type {Record<string, string>} */
export const CONFIDENCE_SYMBOLS = {
  high: '✓',
  medium: '~',
  low: '!',
};

/** @type {Record<string, string>} */
export const RECOMMENDATION_TYPE = {
  AI_CHANGED: 'ai_changed',
  AI_UNCHANGED: 'ai_unchanged',
  NO_RECOMMENDATION: 'no_recommendation',
};


/**
 * Convert data row number to Excel row number.
 * Excel row 1 is the header, so data row N appears as Excel row N+1.
 * @param {number} dataRowNumber - 1-based data row number
 * @returns {number} Excel row number
 */
export const toExcelRowNumber = (dataRowNumber) => dataRowNumber + 1;

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
 * Check if a cell needs review (has changes or no AI recommendation).
 * @param {Object} cell - Cell object
 * @returns {boolean}
 */
export const cellNeedsReview = (cell) => {
  // Include cells with no AI recommendation
  if (cell.recommendationType === RECOMMENDATION_TYPE.NO_RECOMMENDATION) {
    return true;
  }
  const original = cell.originalValue ?? '';
  const harmonized = cell.harmonizedValue ?? '';
  return original !== harmonized;
};

/**
 * Check if a row has any cells that need review.
 * Includes cells where AI changed the value OR where no AI recommendation was provided.
 * Note: whitespace is semantically significant in ontological harmonization.
 * @param {Object} row - Row object containing cells array
 * @returns {boolean}
 */
export const rowHasChanges = (row) => {
  if (!row?.cells) return false;
  for (const cell of row.cells) {
    if (cellNeedsReview(cell)) {
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
 * @returns {Array} Array of cells needing review
 */
export const getChangedCells = (row) => {
  if (!row?.cells) return [];
  const reviewCells = [];
  for (const cell of row.cells) {
    // Skip cells with no original value (nothing to review)
    const original = cell.originalValue ?? '';
    if (!original) continue;

    if (cellNeedsReview(cell)) {
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
 * Build the HTML template for a value card.
 * @param {Object} params
 * @returns {string}
 */
const _buildCardHTML = (params) => {
  const { columnLabel, labelText, confidenceSymbol, bucket, recommendedText, recommendedClass, originalValue, inputValue, isPVNonConformant } = params;
  const safeColumnLabel = escapeHtml(columnLabel);
  const safeLabelText = escapeHtml(labelText);
  const safeRecommendedText = escapeHtml(recommendedText);
  const safeOriginalValue = escapeHtml(originalValue);
  const safeInputValue = escapeHtml(inputValue);

  // Warning icon shown next to arrow when value doesn't conform to permissible values
  const pvWarningIcon = isPVNonConformant
    ? '<span class="pv-warning-icon" data-tooltip="The AI suggestion is not in the permissible values list, but may help guide you to an appropriate value." aria-label="Warning: value not in permissible values">⚠</span>'
    : '';

  return `
    <div class="card-header-row">
      <span class="confidence-indicator confidence-${bucket}" aria-label="${bucket} confidence">${confidenceSymbol}</span>
      <div class="entry-row-label">${safeLabelText}</div>
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
        ${pvWarningIcon}
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
 * Update the override display for a value card.
 * Shows strikethrough AI suggestion with override value, or restores AI suggestion.
 * @param {HTMLElement} recommendedEl - Element displaying the harmonized value
 * @param {string} aiSuggestedValue - The AI's suggested value
 * @param {string} overrideValue - User's override value (empty string means no override)
 */
const _updateOverrideDisplay = (recommendedEl, aiSuggestedValue, overrideValue) => {
  const isMatchingAI = overrideValue === aiSuggestedValue;
  const hasOverride = overrideValue && !isMatchingAI;

  if (hasOverride) {
    recommendedEl.innerHTML = `<span class="ai-suggestion-struck">${escapeHtml(aiSuggestedValue)}</span><span class="override-value">${escapeHtml(overrideValue)}</span>`;
    recommendedEl.classList.add('has-override');
  } else {
    recommendedEl.textContent = aiSuggestedValue;
    recommendedEl.classList.remove('has-override');
  }
};

/**
 * Attach input listener to card for override changes.
 * Updates the displayed harmonized value when override is entered.
 * @param {HTMLElement} card
 * @param {Object} entry
 * @param {Function} onOverrideChange
 */
const _attachInputListener = (card, entry, onOverrideChange) => {
  const input = card.querySelector('.value-input');
  const recommendedEl = card.querySelector('.recommended-text');
  if (!input || !recommendedEl) return;

  const aiSuggestedValue = entry.harmonizedValue ?? entry.originalValue ?? '—';

  input.addEventListener('input', (event) => {
    const overrideValue = event.target.value;
    _updateOverrideDisplay(recommendedEl, aiSuggestedValue, overrideValue);

    // If matches AI suggestion, notify with empty override to clear it
    const effectiveOverride = overrideValue === aiSuggestedValue ? '' : overrideValue;
    onOverrideChange(
      entry.rowIndices,
      entry.columnKey,
      entry.harmonizedValue,
      effectiveOverride,
      entry.originalValue,
    );
  });

  // On blur, if value matches AI suggestion, clear the input
  input.addEventListener('blur', () => {
    if (input.value === aiSuggestedValue) {
      _updateOverrideDisplay(recommendedEl, aiSuggestedValue, '');
      input.value = '';
    }
  });

  // Initialize state if there's already an override value
  const initialOverride = input.value;
  if (initialOverride && initialOverride !== aiSuggestedValue) {
    _updateOverrideDisplay(recommendedEl, aiSuggestedValue, initialOverride);
  }
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

    // Position tooltip below the warning icon
    const iconRect = warningIcon.getBoundingClientRect();
    const tooltipWidth = 240;
    let left = iconRect.left + (iconRect.width / 2) - (tooltipWidth / 2);
    left = Math.max(10, Math.min(left, window.innerWidth - tooltipWidth - 10));

    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${iconRect.bottom + 8}px`;
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
  if (!inputWrapper || !recommendedEl) return null;

  const aiSuggestedValue = entry.harmonizedValue ?? entry.originalValue ?? '—';

  // Clear the wrapper and add PV combobox
  inputWrapper.innerHTML = '';

  // If initial value matches AI suggestion, don't pass it as override
  const effectiveInitialValue = initialValue === aiSuggestedValue ? '' : initialValue;

  const suggestions = entry.topSuggestions ?? [];
  const combobox = createPVCombobox({
    suggestions,
    pvValues,
    initialValue: effectiveInitialValue,
    onChange: (value) => {
      const isMatchingAI = value === aiSuggestedValue;

      // Update display using shared function
      _updateOverrideDisplay(recommendedEl, aiSuggestedValue, value);

      // Reset the combobox input if back to AI suggestion
      if (isMatchingAI && combobox.reset) {
        combobox.reset();
      }

      // Notify parent - if matches AI, send empty to clear override
      const effectiveOverride = isMatchingAI ? '' : value;
      onOverrideChange(
        entry.rowIndices,
        entry.columnKey,
        entry.harmonizedValue,
        effectiveOverride,
        entry.originalValue,
      );
    },
  });

  inputWrapper.appendChild(combobox);

  // Return cleanup function
  return () => {
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

  // Check if value is non-conformant (PVs available but value doesn't match)
  const isPVNonConformant = entry.pvSetAvailable && !entry.isPVConformant;

  card.innerHTML = _buildCardHTML({
    columnLabel,
    labelText,
    confidenceSymbol,
    bucket: entry.bucket,
    recommendedText,
    recommendedClass,
    originalValue: entry.originalValue ?? '—',
    inputValue,
    isPVNonConformant,
  });

  // Collect cleanup functions for proper resource management
  const cleanupFns = [];

  // Use PV combobox if PVs are available for this column, otherwise use text input
  const pvValues = columnPVs?.[entry.columnKey];
  if (entry.pvSetAvailable && pvValues?.length > 0) {
    const comboboxCleanup = _attachPVCombobox(card, entry, pvValues, inputValue, onOverrideChange);
    if (comboboxCleanup) cleanupFns.push(comboboxCleanup);
  } else {
    _attachInputListener(card, entry, onOverrideChange);
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
