/**
 * Shared utilities for Stage 4 review modes.
 * Contains common constants, helper functions, and rendering logic used by both column and row modes.
 */

/** @type {Record<string, string>} */
export const CONFIDENCE_SYMBOLS = {
  high: '✓',
  medium: '~',
  low: '!',
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
const _escapeHtml = (str) => {
  if (typeof str !== 'string') return str;
  const escapeMap = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return str.replace(/[&<>"']/g, (c) => escapeMap[c]);
};

/**
 * Check if a row has any cells where harmonizedValue differs from originalValue.
 * Rows where all recommendations match the original input are considered unchanged.
 * Note: whitespace is semantically significant in ontological harmonization.
 * @param {Object} row - Row object containing cells array
 * @returns {boolean}
 */
export const rowHasChanges = (row) => {
  if (!row?.cells) return false;
  for (const cell of row.cells) {
    const original = cell.originalValue ?? '';
    const harmonized = cell.harmonizedValue ?? '';
    if (original !== harmonized) {
      return true;
    }
  }
  return false;
};

/**
 * Get cells from a row that have changes (original !== harmonized).
 * Note: whitespace is semantically significant in ontological harmonization.
 * @param {Object} row - Row object containing cells array
 * @returns {Array} Array of cells with changes
 */
export const getChangedCells = (row) => {
  if (!row?.cells) return [];
  const changedCells = [];
  for (const cell of row.cells) {
    const original = cell.originalValue ?? '';
    const harmonized = cell.harmonizedValue ?? '';
    if (original && original !== harmonized) {
      changedCells.push(cell);
    }
  }
  return changedCells;
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
  if (entry.harmonizedValue === null) {
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
  const { columnLabel, labelText, confidenceSymbol, bucket, recommendedText, recommendedClass, originalValue, inputValue } = params;
  const safeColumnLabel = _escapeHtml(columnLabel);
  const safeLabelText = _escapeHtml(labelText);
  const safeRecommendedText = _escapeHtml(recommendedText);
  const safeOriginalValue = _escapeHtml(originalValue);
  const safeInputValue = _escapeHtml(inputValue);
  return `
    <div class="card-header-row">
      <span class="confidence-indicator confidence-${bucket}" aria-label="${bucket} confidence">${confidenceSymbol}</span>
      <div class="entry-row-label">${safeLabelText}</div>
    </div>
    <div class="value-pair" role="group" aria-label="${safeColumnLabel} comparison">
      <div class="value-group original">
        <p class="value-text original-text">${safeOriginalValue}</p>
      </div>
      <div class="transformation-arrow" aria-hidden="true">↓</div>
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
 * Attach input listener to card for override changes.
 * Updates the displayed harmonized value when override is entered.
 * @param {HTMLElement} card
 * @param {Object} entry
 * @param {Function} onOverrideChange
 */
const _attachInputListener = (card, entry, onOverrideChange) => {
  const input = card.querySelector('.value-input');
  const recommendedEl = card.querySelector('.recommended-text');
  const aiSuggestedValue = entry.harmonizedValue ?? entry.originalValue ?? '—';

  const updateDisplay = (overrideValue) => {
    if (overrideValue) {
      recommendedEl.innerHTML = `${_escapeHtml(overrideValue)} <span class="override-info-icon" data-tooltip="Original suggestion: ${_escapeHtml(aiSuggestedValue)}" data-copy-value="${_escapeHtml(aiSuggestedValue)}">ⓘ</span>`;
      recommendedEl.classList.add('has-override');

      // Add click-to-copy handler
      const infoIcon = recommendedEl.querySelector('.override-info-icon');
      if (infoIcon) {
        infoIcon.addEventListener('click', async (e) => {
          e.preventDefault();
          e.stopPropagation();
          const valueToCopy = infoIcon.dataset.copyValue;
          try {
            await navigator.clipboard.writeText(valueToCopy);
            // Brief visual feedback
            const originalText = infoIcon.textContent;
            infoIcon.textContent = '✓';
            setTimeout(() => { infoIcon.textContent = originalText; }, 800);
          } catch {
            // Fallback: just put it in the input
            input.value = valueToCopy;
            input.dispatchEvent(new Event('input', { bubbles: true }));
          }
        });
      }
    } else {
      recommendedEl.textContent = aiSuggestedValue;
      recommendedEl.classList.remove('has-override');
    }
  };

  input.addEventListener('input', (event) => {
    const overrideValue = event.target.value.trim();
    updateDisplay(overrideValue);

    onOverrideChange(
      entry.rowIndices,
      entry.columnKey,
      entry.harmonizedValue,
      event.target.value,
      entry.originalValue,
    );
  });

  // Initialize state if there's already an override value
  const initialOverride = input.value.trim();
  if (initialOverride) {
    updateDisplay(initialOverride);
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
    rowLabelEl.dataset.tooltip = _escapeHtml(tooltipText);
  }
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
 * @returns {HTMLElement}
 */
export const createValueCard = (config) => {
  const { entry, labelText, tooltipText, pendingOverrides, onOverrideChange } = config;

  const card = document.createElement('div');
  card.className = _buildCardClasses(entry);

  const columnLabel = entry.columnLabel ?? entry.columnKey ?? '';
  const inputValue = _getInputValue(entry, pendingOverrides);
  const recommendedText = entry.harmonizedValue ?? entry.originalValue ?? '—';
  const recommendedClass = entry.harmonizedValue === null ? ' missing' : '';
  const confidenceSymbol = CONFIDENCE_SYMBOLS[entry.bucket] ?? '?';

  card.innerHTML = _buildCardHTML({
    columnLabel,
    labelText,
    confidenceSymbol,
    bucket: entry.bucket,
    recommendedText,
    recommendedClass,
    originalValue: entry.originalValue ?? '—',
    inputValue,
  });

  _attachInputListener(card, entry, onOverrideChange);
  _addTooltip(card, tooltipText);

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
