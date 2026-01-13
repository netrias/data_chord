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

/**
 * Create a PV-restricted combobox for Stage 4 edit fields.
 * @param {Object} config
 * @param {Array<{value: string, isPVConformant: boolean}>} config.suggestions - AI suggestions with conformance flags
 * @param {string[]} config.pvValues - Alphabetized list of valid PVs
 * @param {string} [config.initialValue] - Current value
 * @param {function(string): void} config.onChange - Callback when value changes
 * @returns {HTMLElement}
 */
export const createPVCombobox = ({ suggestions, pvValues, initialValue, onChange }) => {
  const wrapper = document.createElement('div');
  wrapper.className = 'pv-combobox';

  // Pencil icon (matches existing value-input-icon)
  const pencilIcon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  pencilIcon.setAttribute('class', 'value-input-icon');
  pencilIcon.setAttribute('viewBox', '0 0 20 20');
  pencilIcon.setAttribute('aria-hidden', 'true');
  const pencilPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  pencilPath.setAttribute('d', 'M2 14.5V18h3.5l8.4-8.4-3.5-3.5L2 14.5zm11.8-9.1a1 1 0 0 1 1.4 0l1.4 1.4a1 1 0 0 1 0 1.4l-1.2 1.2-3.5-3.5 1.2-1.2z');
  pencilIcon.appendChild(pencilPath);

  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'pv-combobox-input';
  // Input stays empty - card display shows the current value, input is just for searching/selecting

  const toggleBtn = document.createElement('button');
  toggleBtn.type = 'button';
  toggleBtn.className = 'pv-combobox-toggle';
  toggleBtn.textContent = '\u25BE';
  toggleBtn.tabIndex = -1;

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

  /** Helper to select a value. */
  const selectValue = (value) => {
    // Clear input after selection - card display shows the override value
    input.value = '';
    input.placeholder = '';
    committedValue = value;
    dropdown.classList.remove('pv-combobox-dropdown--open');
    onChange(value);
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
    // Input stays empty - card display shows the current override value
    input.value = '';
    input.placeholder = '';

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

      // Preserve whitespace - domain rule: whitespace is semantically significant
      const typedValue = input.value;
      const typedLower = typedValue.toLowerCase();

      // Case-insensitive match for UX (per CLAUDE.md exception for dropdown search/filtering).
      // This helps users who type "lung cancer" find "Lung Cancer" without exact case.
      // The matched canonical PV is committed, preserving domain conformance.
      const matchedPV = pvValues.find((pv) => pv.toLowerCase() === typedLower);

      if (matchedPV && matchedPV !== committedValue) {
        committedValue = matchedPV;
        onChange(matchedPV);
      }
      // Always clear input after blur - card display shows the override value
      input.value = '';
      input.placeholder = '';
    }, BLUR_DELAY_MS);
  });

  // Toggle button: open/close dropdown
  toggleBtn.addEventListener('click', () => {
    if (dropdown.classList.contains('pv-combobox-dropdown--open')) {
      dropdown.classList.remove('pv-combobox-dropdown--open');
    } else {
      openDropdown();
      input.focus();
    }
  });

  wrapper.appendChild(pencilIcon);
  wrapper.appendChild(input);
  wrapper.appendChild(toggleBtn);
  wrapper.appendChild(dropdown);

  /** Reset the combobox to empty state. */
  wrapper.reset = () => {
    committedValue = '';
    input.value = '';
    input.placeholder = '';
  };

  /** Cleanup function to clear pending timeouts. */
  wrapper.destroy = () => {
    if (blurTimeoutId !== null) {
      clearTimeout(blurTimeoutId);
      blurTimeoutId = null;
    }
    if (feedbackTimeoutId !== null) {
      clearTimeout(feedbackTimeoutId);
      feedbackTimeoutId = null;
    }
  };

  return wrapper;
};
