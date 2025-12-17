/**
 * Combobox Widget - A searchable dropdown with specific UX requirements.
 *
 * REQUIREMENTS:
 * 1. FOCUS BEHAVIOR: When user clicks/focuses on input with existing value:
 *    - Clear the input text
 *    - Show existing value as grayed placeholder text
 *    - Display ALL options (unfiltered)
 *
 * 2. TYPING BEHAVIOR: As user types:
 *    - Filter options to match input text (case-insensitive)
 *    - Show "No matches" if no options match
 *
 * 3. SELECTION BEHAVIOR: When user selects an option:
 *    - Set input value to selected option
 *    - Close dropdown
 *    - Call onChange callback with new value
 *
 * 4. BLUR BEHAVIOR: When user clicks out without selecting:
 *    - If input is empty or invalid, restore previous value
 *    - If input matches a valid option, accept it
 *    - Close dropdown
 *
 * 5. TOGGLE BUTTON: Clicking dropdown arrow:
 *    - If closed: show ALL options, focus input
 *    - If open: close dropdown
 */

/** Delay before processing blur to allow mousedown on option to fire first. */
const BLUR_DELAY_MS = 150;

/**
 * Create a combobox widget element.
 * @param {Object} config - Configuration object
 * @param {string[]} config.options - Available options to choose from
 * @param {string} [config.initialValue] - Initial selected value
 * @param {string} config.placeholder - Placeholder text when no value selected
 * @param {function(string|null): void} config.onChange - Callback when value changes
 * @param {number} [config.separatorAfterIndex] - Insert separator after this option index
 * @param {number[]} [config.mutedIndices] - Indices of options to style as muted (gray, italic)
 * @returns {HTMLElement} The combobox wrapper element
 */
export const createCombobox = ({ options, initialValue, placeholder, onChange, separatorAfterIndex, mutedIndices }) => {
  const wrapper = document.createElement('div');
  wrapper.className = 'combobox';

  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'combobox-input';
  input.placeholder = placeholder;
  input.value = initialValue || '';

  const dropdownBtn = document.createElement('button');
  dropdownBtn.type = 'button';
  dropdownBtn.className = 'combobox-toggle';
  dropdownBtn.innerHTML = '&#9662;';
  dropdownBtn.tabIndex = -1;

  const dropdown = document.createElement('ul');
  dropdown.className = 'combobox-dropdown';

  /* Track the committed value (value before editing started) */
  let committedValue = initialValue || '';

  const mutedSet = new Set(mutedIndices ?? []);

  /** Render dropdown options, optionally filtered by search text. */
  const renderOptions = (filter = '') => {
    dropdown.innerHTML = '';
    const filterLower = filter.toLowerCase();

    /* Build filtered list with original indices preserved */
    const filteredWithIndices = options
      .map((opt, idx) => ({ option: opt, originalIndex: idx }))
      .filter(({ option }) => option.toLowerCase().includes(filterLower));

    if (filteredWithIndices.length === 0) {
      const noMatch = document.createElement('li');
      noMatch.className = 'combobox-option combobox-option--empty';
      noMatch.textContent = 'No matches';
      dropdown.appendChild(noMatch);
      return;
    }

    filteredWithIndices.forEach(({ option, originalIndex }) => {
      const li = document.createElement('li');
      li.className = 'combobox-option';
      if (mutedSet.has(originalIndex)) {
        li.classList.add('combobox-option--muted');
      }
      li.textContent = option;
      li.addEventListener('mousedown', (e) => {
        e.preventDefault(); /* Prevent blur from firing before selection */
        input.value = option;
        committedValue = option;
        dropdown.classList.remove('combobox-dropdown--open');
        onChange(option);
      });
      dropdown.appendChild(li);

      /* Add separator after specified index if not filtered */
      if (separatorAfterIndex === originalIndex && !filter) {
        const separator = document.createElement('li');
        separator.className = 'combobox-separator';
        separator.setAttribute('role', 'separator');
        dropdown.appendChild(separator);
      }
    });
  };

  /* Initialize dropdown options */
  renderOptions();

  /* REQ 1: On focus, clear input, show value as placeholder, show all options */
  input.addEventListener('focus', () => {
    if (committedValue) {
      input.placeholder = committedValue;
      input.value = '';
    }
    renderOptions(''); /* Show ALL options unfiltered */
    dropdown.classList.add('combobox-dropdown--open');
  });

  /* REQ 2: On input, filter options based on typed text */
  input.addEventListener('input', () => {
    renderOptions(input.value);
    dropdown.classList.add('combobox-dropdown--open');
  });

  /* REQ 4: On blur, restore or accept value */
  input.addEventListener('blur', () => {
    setTimeout(() => {
      dropdown.classList.remove('combobox-dropdown--open');
      const typedValue = input.value.trim();
      const isValidOption = options.includes(typedValue);

      if (isValidOption && typedValue !== committedValue) {
        /* User typed a valid option different from original */
        committedValue = typedValue;
        input.value = typedValue;
        input.placeholder = placeholder;
        onChange(typedValue);
      } else {
        /* Restore committed value - covers: empty input, invalid input, unchanged input */
        /* Note: To clear a selection, user must explicitly select a different option */
        input.value = committedValue;
        input.placeholder = placeholder;
      }
    }, BLUR_DELAY_MS);
  });

  /* REQ 5: Toggle button shows all options and focuses input */
  dropdownBtn.addEventListener('click', () => {
    if (dropdown.classList.contains('combobox-dropdown--open')) {
      dropdown.classList.remove('combobox-dropdown--open');
    } else {
      if (committedValue) {
        input.placeholder = committedValue;
        input.value = '';
      }
      renderOptions(''); /* Show ALL options */
      dropdown.classList.add('combobox-dropdown--open');
      input.focus();
    }
  });

  wrapper.appendChild(input);
  wrapper.appendChild(dropdownBtn);
  wrapper.appendChild(dropdown);

  return wrapper;
};
