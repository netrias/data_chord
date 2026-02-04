/**
 * PV Combobox Widget - A dropdown for selecting permissible values.
 *
 * Displays AI suggestions (with conformance indicators) followed by
 * an alphabetized list of all permissible values. Only PV-conformant
 * values can be selected.
 *
 * Performance: Options are lazily built on first open, not at creation time.
 * Filtering toggles visibility via CSS rather than rebuilding DOM.
 */

/** Delay before processing blur to allow mousedown on option to fire first. */
const BLUR_DELAY_MS = 150;

/** Duration to show feedback message before auto-dismiss. */
const FEEDBACK_DURATION_MS = 2000;

/** Tooltip text for AI suggestion options. */
const SUGGESTION_TOOLTIPS = {
  conformant: 'AI Suggested Option',
  nonConformant: 'This AI suggestion is not a permissible value, but it may be a helpful starting point.',
};

/**
 * Create a PV-restricted combobox for Stage 4 edit fields.
 * @param {Object} config
 * @param {Array<{value: string, isPVConformant: boolean}>} config.suggestions - AI suggestions with conformance flags
 * @param {string[]} config.pvValues - Alphabetized list of valid PVs
 * @param {string} [config.initialValue] - Current value
 * @param {function(string, boolean): void} config.onChange - Callback when value changes (value, isKnownConformant)
 * @returns {HTMLElement}
 */
export const createPVCombobox = ({ suggestions, pvValues, initialValue, onChange }) => {
  const wrapper = document.createElement('div');
  wrapper.className = 'pv-combobox';

  // Link element - displays committed value when closed
  const link = document.createElement('span');
  link.className = 'pv-combobox-link';

  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'pv-combobox-input';

  const dropdown = document.createElement('ul');
  dropdown.className = 'pv-combobox-dropdown';

  let committedValue = initialValue || '';
  let blurTimeoutId = null;
  let feedbackTimeoutId = null;

  // Lazy initialization state
  let optionsBuilt = false;
  let allOptionEls = [];
  let separatorEl = null;
  let emptyStateEl = null;

  // Suggestion tooltip state
  let suggestionTooltip = null;

  /** Show tooltip for a suggestion option. */
  const showSuggestionTooltip = (li) => {
    if (suggestionTooltip) return;

    const isConformant = !li.classList.contains('pv-combobox-option--disabled');
    const tooltipText = isConformant ? SUGGESTION_TOOLTIPS.conformant : SUGGESTION_TOOLTIPS.nonConformant;

    suggestionTooltip = document.createElement('div');
    suggestionTooltip.className = 'pv-suggestion-tooltip';
    suggestionTooltip.textContent = tooltipText;
    document.body.appendChild(suggestionTooltip);

    // Position tooltip to the right of the option
    const optionRect = li.getBoundingClientRect();
    const tooltipRect = suggestionTooltip.getBoundingClientRect();
    const padding = 8;

    let left = optionRect.right + padding;
    let top = optionRect.top + (optionRect.height / 2) - (tooltipRect.height / 2);

    // Constrain to viewport bounds
    if (left + tooltipRect.width > window.innerWidth - 10) {
      // Position to the left if not enough space on the right
      left = optionRect.left - tooltipRect.width - padding;
      suggestionTooltip.classList.add('pv-suggestion-tooltip--left');
    }
    top = Math.max(10, Math.min(top, window.innerHeight - tooltipRect.height - 10));

    suggestionTooltip.style.left = `${left}px`;
    suggestionTooltip.style.top = `${top}px`;
  };

  /** Hide suggestion tooltip. */
  const hideSuggestionTooltip = () => {
    if (suggestionTooltip) {
      suggestionTooltip.remove();
      suggestionTooltip = null;
    }
  };

  /** Show "Not in permissible values" feedback message. */
  const showFeedback = () => {
    const existing = wrapper.querySelector('.pv-feedback-message');
    if (existing) {
      existing.remove();
    }
    if (feedbackTimeoutId) {
      clearTimeout(feedbackTimeoutId);
    }

    const feedback = document.createElement('div');
    feedback.className = 'pv-feedback-message';
    feedback.textContent = 'Not in permissible values';
    wrapper.appendChild(feedback);

    feedbackTimeoutId = setTimeout(() => {
      feedback.remove();
      feedbackTimeoutId = null;
    }, FEEDBACK_DURATION_MS);
  };

  /** Helper to select a value from dropdown (always conformant). */
  const selectValue = (value) => {
    committedValue = value;
    link.textContent = value;
    dropdown.classList.remove('pv-combobox-dropdown--open');
    wrapper.classList.remove('pv-combobox--open');
    onChange(value, true);
  };

  /** Build all option elements (called once on first open). */
  const buildOptions = () => {
    if (optionsBuilt) return;
    optionsBuilt = true;

    const suggestionValuesSet = new Set(suggestions.map((s) => s.value));

    // Build suggestion options
    for (const suggestion of suggestions) {
      const li = document.createElement('li');
      li.className = 'pv-combobox-option';
      li.dataset.value = suggestion.value;
      li.dataset.valueLower = suggestion.value.toLowerCase();
      li.dataset.type = 'suggestion';

      if (!suggestion.isPVConformant) {
        li.classList.add('pv-combobox-option--disabled');
      }

      li.textContent = suggestion.value;

      li.addEventListener('mousedown', (e) => {
        e.preventDefault();
        if (!suggestion.isPVConformant) {
          showFeedback();
          return;
        }
        selectValue(suggestion.value);
      });

      // Attach tooltip handlers for suggestions
      li.addEventListener('mouseenter', () => showSuggestionTooltip(li));
      li.addEventListener('mouseleave', hideSuggestionTooltip);

      allOptionEls.push(li);
      dropdown.appendChild(li);
    }

    // Build separator
    if (suggestions.length > 0 && pvValues.length > suggestionValuesSet.size) {
      separatorEl = document.createElement('li');
      separatorEl.className = 'pv-combobox-separator';
      separatorEl.setAttribute('role', 'separator');
      dropdown.appendChild(separatorEl);
    }

    // Build PV options (excluding those already in suggestions)
    for (const pv of pvValues) {
      if (suggestionValuesSet.has(pv)) continue;

      const li = document.createElement('li');
      li.className = 'pv-combobox-option';
      li.dataset.value = pv;
      li.dataset.valueLower = pv.toLowerCase();
      li.dataset.type = 'pv';
      li.textContent = pv;

      li.addEventListener('mousedown', (e) => {
        e.preventDefault();
        selectValue(pv);
      });

      allOptionEls.push(li);
      dropdown.appendChild(li);
    }

    // Append empty state (hidden by default)
    emptyStateEl = document.createElement('li');
    emptyStateEl.className = 'pv-combobox-option pv-combobox-option--empty';
    emptyStateEl.textContent = 'No matches';
    emptyStateEl.style.display = 'none';
    dropdown.appendChild(emptyStateEl);
  };

  /** Filter options by showing/hiding based on filter text. */
  const filterOptions = (filter = '') => {
    const filterLower = filter.toLowerCase();
    let visibleSuggestions = 0;
    let visiblePVs = 0;

    for (const li of allOptionEls) {
      const matches = filterLower === '' || li.dataset.valueLower.includes(filterLower);
      li.style.display = matches ? '' : 'none';

      if (matches) {
        if (li.dataset.type === 'suggestion') {
          visibleSuggestions++;
        } else {
          visiblePVs++;
        }
      }
    }

    // Show/hide separator based on whether both sections have visible items
    if (separatorEl) {
      separatorEl.style.display = (visibleSuggestions > 0 && visiblePVs > 0) ? '' : 'none';
    }

    // Show/hide empty state
    const hasResults = visibleSuggestions > 0 || visiblePVs > 0;
    if (emptyStateEl) {
      emptyStateEl.style.display = hasResults ? 'none' : '';
    }
  };

  /** Position the dropdown using fixed positioning relative to viewport. */
  const positionDropdown = () => {
    if (!wrapper.isConnected) return;
    const wrapperRect = wrapper.getBoundingClientRect();
    const dropdownMaxHeight = 400;
    const viewportHeight = window.innerHeight;
    const padding = 10;

    // Calculate horizontal position (centered on wrapper, but constrained to viewport)
    const dropdownWidth = Math.max(380, wrapperRect.width + 64);
    let left = wrapperRect.left + (wrapperRect.width / 2) - (dropdownWidth / 2);
    left = Math.max(padding, Math.min(left, window.innerWidth - dropdownWidth - padding));

    // Always position below the input
    const top = wrapperRect.bottom + padding;

    // Calculate available height below (constrain dropdown height if needed)
    const availableHeight = viewportHeight - top - padding;
    const actualHeight = Math.min(dropdownMaxHeight, Math.max(150, availableHeight));

    dropdown.style.left = `${left}px`;
    dropdown.style.top = `${top}px`;
    dropdown.style.width = `${dropdownWidth}px`;
    dropdown.style.maxHeight = `${actualHeight}px`;
  };

  /** Open the dropdown (builds options lazily on first open). */
  const openDropdown = () => {
    // Show input, hide link
    wrapper.classList.add('pv-combobox--open');

    // Clear input for searching, show current value as placeholder hint
    input.value = '';
    input.placeholder = committedValue || 'Type to search...';

    // Position dropdown in viewport
    positionDropdown();

    // If options already built, open immediately
    if (optionsBuilt) {
      filterOptions('');
      dropdown.classList.add('pv-combobox-dropdown--open');
      return;
    }

    // First open: show dropdown with min-height, then build options after paint
    dropdown.style.minHeight = '100px';
    dropdown.classList.add('pv-combobox-dropdown--open');

    // Use double-rAF to ensure dropdown background is painted before building options
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        // Guard against operating on orphaned DOM nodes
        if (!wrapper.isConnected) return;
        buildOptions();
        dropdown.style.minHeight = '';
        filterOptions('');
      });
    });
  };

  // Focus: clear input, show value as placeholder, show all options
  input.addEventListener('focus', () => {
    if (blurTimeoutId !== null) {
      clearTimeout(blurTimeoutId);
      blurTimeoutId = null;
    }
    openDropdown();
  });

  // Click: also open dropdown (handles case where input already has focus)
  input.addEventListener('click', () => {
    if (!dropdown.classList.contains('pv-combobox-dropdown--open')) {
      openDropdown();
    }
  });

  // Input: filter options
  input.addEventListener('input', () => {
    filterOptions(input.value);
    dropdown.classList.add('pv-combobox-dropdown--open');
  });

  // Blur: validate and restore/accept value
  input.addEventListener('blur', () => {
    blurTimeoutId = setTimeout(() => {
      blurTimeoutId = null;

      // Guard against operating on orphaned DOM nodes
      if (!wrapper.isConnected) return;

      dropdown.classList.remove('pv-combobox-dropdown--open');
      wrapper.classList.remove('pv-combobox--open');
      hideSuggestionTooltip();

      // Preserve whitespace - domain rule: whitespace is semantically significant
      const typedValue = input.value;
      const typedLower = typedValue.toLowerCase();

      // Case-insensitive match for UX (per CLAUDE.md exception for dropdown search/filtering).
      // This helps users who type "lung cancer" find "Lung Cancer" without exact case.
      // The matched canonical PV is committed, preserving domain conformance.
      const matchedPV = pvValues.find((pv) => pv.toLowerCase() === typedLower);

      if (matchedPV && matchedPV !== committedValue) {
        committedValue = matchedPV;
        link.textContent = matchedPV;
        onChange(matchedPV, true);
      }
    }, BLUR_DELAY_MS);
  });

  // Link click: open dropdown and focus input
  link.addEventListener('click', () => {
    openDropdown();
    input.focus();
  });

  wrapper.appendChild(link);
  wrapper.appendChild(input);
  wrapper.appendChild(dropdown);

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
    // Close dropdown if open (ensures UI consistency when called programmatically)
    dropdown.classList.remove('pv-combobox-dropdown--open');
    wrapper.classList.remove('pv-combobox--open');
  };

  /** Cleanup function to clear pending timeouts and tooltips. */
  wrapper.destroy = () => {
    if (blurTimeoutId !== null) {
      clearTimeout(blurTimeoutId);
      blurTimeoutId = null;
    }
    if (feedbackTimeoutId !== null) {
      clearTimeout(feedbackTimeoutId);
      feedbackTimeoutId = null;
    }
    hideSuggestionTooltip();
  };

  return wrapper;
};
