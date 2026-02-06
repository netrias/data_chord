/**
 * PV Combobox Widget - A link that opens a modal for selecting permissible values.
 *
 * Displays AI suggestions (with conformance indicators) followed by
 * an alphabetized list of all permissible values. Only PV-conformant
 * values can be selected.
 */

/**
 * Escape HTML special characters to prevent XSS.
 * @param {string} str
 * @returns {string}
 */
const _escapeHtml = (str) => {
  if (typeof str !== 'string') return String(str);
  const escapeMap = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return str.replace(/[&<>"']/g, (c) => escapeMap[c]);
};

/**
 * Build the HTML structure for the PV selection modal.
 * @param {Object} config
 * @param {string} config.originalValue - Original value from source data
 * @param {string} config.currentValue - Current effective value
 * @param {Array<{value: string, isPVConformant: boolean}>} config.suggestions
 * @param {string[]} config.pvValues - Alphabetized PV list
 * @returns {string}
 */
const _buildModalHTML = ({ originalValue, currentValue, suggestions, pvValues }) => {
  const safeOriginal = _escapeHtml(originalValue);
  const safeCurrent = _escapeHtml(currentValue);

  // Build suggestion options HTML
  const suggestionValuesSet = new Set(suggestions.map((s) => s.value));
  const nonConformantTooltip = 'This value is not in the permissible values list, but it may help point you in the right direction.';
  const suggestionOptionsHTML = suggestions.map((s) => {
    const disabledClass = s.isPVConformant ? '' : ' pv-selection-option--disabled';
    const tooltipAttr = s.isPVConformant ? '' : ` data-tooltip="${nonConformantTooltip}"`;
    const safeValue = _escapeHtml(s.value);
    return `<div class="pv-selection-option${disabledClass}" data-value="${safeValue}" data-type="suggestion" data-conformant="${s.isPVConformant}"${tooltipAttr}>${safeValue}</div>`;
  }).join('');

  // Build PV options HTML (excluding those already in suggestions)
  const pvOptionsHTML = pvValues
    .filter((pv) => !suggestionValuesSet.has(pv))
    .map((pv) => {
      const safeValue = _escapeHtml(pv);
      return `<div class="pv-selection-option" data-value="${safeValue}" data-type="pv" data-conformant="true">${safeValue}</div>`;
    }).join('');

  return `
    <div class="pv-selection-content">
      <div class="pv-selection-header">
        <h2 class="pv-selection-title" id="pv-modal-title">Select Value</h2>
        <button class="pv-selection-close-btn" type="button" aria-label="Close">&times;</button>
      </div>

      <div class="pv-selection-context">
        <div class="pv-selection-context-row">
          <span class="pv-selection-context-label">was:</span>
          <span class="pv-selection-context-value">${safeOriginal}</span>
        </div>
        <div class="pv-selection-context-row">
          <span class="pv-selection-context-label">now:</span>
          <span class="pv-selection-context-value pv-selection-current">${safeCurrent}</span>
        </div>
      </div>

      <div class="pv-selection-search">
        <input type="text" placeholder="Search values..." class="pv-selection-search-input" />
      </div>

      <div class="pv-selection-list">
        <div class="pv-selection-section" data-section="suggestions">
          <div class="pv-selection-section-title">AI Suggestions</div>
          ${suggestionOptionsHTML}
        </div>
        <div class="pv-selection-section" data-section="pvs">
          <div class="pv-selection-section-title">Permissible Values</div>
          ${pvOptionsHTML}
        </div>
        <div class="pv-selection-empty" style="display: none;">No matches</div>
      </div>
    </div>
  `;
};

/**
 * Modal provides more space than dropdown for reviewing long PV lists.
 * @param {Object} config
 * @param {string} config.originalValue - Original value from source data
 * @param {string} config.currentValue - Current effective value
 * @param {Array<{value: string, isPVConformant: boolean}>} config.suggestions
 * @param {string[]} config.pvValues - Alphabetized PV list
 * @returns {Promise<string|null>} Selected value, or null if dismissed
 */
export async function showPVSelectionModal(config) {
  return new Promise((resolve) => {
    let resolved = false;

    const cleanup = () => {
      if (dialog.parentNode) {
        dialog.remove();
      }
    };

    const resolveOnce = (value) => {
      if (resolved) return;
      resolved = true;
      cleanup();
      resolve(value);
    };

    // Create dialog element
    const dialog = document.createElement('dialog');
    dialog.className = 'pv-selection-dialog';
    dialog.setAttribute('aria-labelledby', 'pv-modal-title');
    dialog.innerHTML = _buildModalHTML(config);

    document.body.appendChild(dialog);
    dialog.showModal();

    // Get references to elements
    const searchInput = dialog.querySelector('.pv-selection-search-input');
    const closeBtn = dialog.querySelector('.pv-selection-close-btn');
    const suggestionsSection = dialog.querySelector('[data-section="suggestions"]');
    const pvSection = dialog.querySelector('[data-section="pvs"]');
    const emptyState = dialog.querySelector('.pv-selection-empty');
    const allOptions = dialog.querySelectorAll('.pv-selection-option');

    // Focus search input
    searchInput?.focus();

    // Filter options based on search text
    const filterOptions = (filterText) => {
      const filterLower = filterText.toLowerCase();
      let visibleSuggestions = 0;
      let visiblePVs = 0;

      for (const option of allOptions) {
        const value = option.dataset.value || '';
        const matches = filterLower === '' || value.toLowerCase().includes(filterLower);
        option.style.display = matches ? '' : 'none';

        if (matches) {
          if (option.dataset.type === 'suggestion') {
            visibleSuggestions++;
          } else {
            visiblePVs++;
          }
        }
      }

      // Show/hide section headers based on visible options
      if (suggestionsSection) {
        suggestionsSection.style.display = visibleSuggestions > 0 ? '' : 'none';
      }
      if (pvSection) {
        pvSection.style.display = visiblePVs > 0 ? '' : 'none';
      }

      // Show empty state when no matches
      const hasResults = visibleSuggestions > 0 || visiblePVs > 0;
      if (emptyState) {
        emptyState.style.display = hasResults ? 'none' : '';
      }
    };

    // Search input handler
    searchInput?.addEventListener('input', () => {
      filterOptions(searchInput.value);
    });

    // Option click handler
    for (const option of allOptions) {
      option.addEventListener('click', () => {
        // Don't select non-conformant suggestions
        if (option.dataset.conformant === 'false') {
          return;
        }
        resolveOnce(option.dataset.value);
      });
    }

    // Close button handler
    closeBtn?.addEventListener('click', () => {
      resolveOnce(null);
    });

    // Backdrop click handler (click on dialog itself = backdrop)
    dialog.addEventListener('click', (e) => {
      if (e.target === dialog) {
        resolveOnce(null);
      }
    });

    // Escape key handler (native dialog behavior, but we need to resolve)
    dialog.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        resolveOnce(null);
      }
    });

    // Handle native dialog close (e.g., via Escape)
    dialog.addEventListener('close', () => {
      resolveOnce(null);
    });
  });
}

/**
 * Create a PV-restricted combobox for Stage 4 edit fields.
 * Displays as a link that opens a modal for value selection.
 * @param {Object} config
 * @param {Array<{value: string, isPVConformant: boolean}>} config.suggestions - AI suggestions with conformance flags
 * @param {string[]} config.pvValues - Alphabetized list of valid PVs
 * @param {string} [config.initialValue] - Current value
 * @param {string} [config.originalValue] - Original value from source data (for "was: X" display)
 * @param {function(string, boolean): void} config.onChange - Callback when value changes (value, isKnownConformant)
 * @returns {HTMLElement}
 */
export const createPVCombobox = ({ suggestions, pvValues, initialValue, originalValue, onChange }) => {
  const wrapper = document.createElement('div');
  wrapper.className = 'pv-combobox';

  // Link element - displays committed value, click opens modal
  const link = document.createElement('span');
  link.className = 'pv-combobox-link';

  let committedValue = initialValue || '';

  /** Helper to select a value (always conformant when called from modal). */
  const selectValue = (value) => {
    committedValue = value;
    link.textContent = value;
    onChange(value, true);
  };

  // Link click: open modal for selection
  link.addEventListener('click', async () => {
    const selected = await showPVSelectionModal({
      originalValue: originalValue ?? '',
      currentValue: committedValue,
      suggestions,
      pvValues,
    });

    if (selected !== null) {
      selectValue(selected);
    }
  });

  wrapper.appendChild(link);

  // Initialize link with committed value
  link.textContent = committedValue;

  /** Reset the combobox to empty state. */
  wrapper.reset = () => {
    committedValue = '';
    link.textContent = '';
  };

  /** Set the combobox to a specific value. */
  wrapper.setValue = (value) => {
    committedValue = value;
    link.textContent = value;
  };

  /** Cleanup function (no-op now that dropdown is removed). */
  wrapper.destroy = () => {};

  return wrapper;
};
