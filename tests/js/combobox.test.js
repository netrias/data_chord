/**
 * Combobox Widget Tests
 *
 * Tests all 5 requirements of the combobox widget:
 * 1. FOCUS BEHAVIOR - clear input, show value as placeholder, display all options
 * 2. TYPING BEHAVIOR - filter options, show "No matches"
 * 3. SELECTION BEHAVIOR - update input, close dropdown, call onChange
 * 4. BLUR BEHAVIOR - restore value when empty/invalid, accept valid typed option
 * 5. TOGGLE BUTTON - open/close dropdown
 */

import { describe, it, before, after } from 'node:test';
import assert from 'node:assert';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const comboboxPath = join(__dirname, '../../src/shared/static/combobox.js');
const comboboxCode = readFileSync(comboboxPath, 'utf-8');

const OPTIONS = [
  'primary_diagnosis',
  'morphology',
  'sample_anatomic_site',
  'therapeutic_agents',
  'tissue_or_organ_of_origin',
];

/** Set up JSDOM environment and load the combobox module. */
const setupDOM = () => {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
    runScripts: 'dangerously',
    url: 'http://localhost',
  });

  /* Inject the combobox code as a script (remove export for eval) */
  const moduleCode = comboboxCode.replace('export const createCombobox', 'window.createCombobox');
  dom.window.eval(moduleCode);

  return dom;
};

/** Helper to simulate focus event. */
const focus = (element) => {
  element.dispatchEvent(new element.ownerDocument.defaultView.FocusEvent('focus'));
};

/** Helper to simulate blur event. */
const blur = (element) => {
  element.dispatchEvent(new element.ownerDocument.defaultView.FocusEvent('blur'));
};

/** Helper to simulate input event. */
const input = (element, value) => {
  element.value = value;
  element.dispatchEvent(new element.ownerDocument.defaultView.Event('input'));
};

/** Helper to simulate mousedown event. */
const mousedown = (element) => {
  const event = new element.ownerDocument.defaultView.MouseEvent('mousedown', { bubbles: true });
  element.dispatchEvent(event);
};

/** Helper to simulate click event. */
const click = (element) => {
  element.dispatchEvent(new element.ownerDocument.defaultView.MouseEvent('click', { bubbles: true }));
};

/** Wait for setTimeout callbacks to execute. */
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

describe('Combobox Widget', () => {
  let dom;
  let createCombobox;

  before(() => {
    dom = setupDOM();
    createCombobox = dom.window.createCombobox;
  });

  after(() => {
    dom.window.close();
  });

  describe('REQ 1: Focus Behavior', () => {
    it('1a. Focus clears input text when value exists', () => {
      // Given: A combobox with an existing value selected
      let onChangeCalled = false;
      const combobox = createCombobox({
        options: OPTIONS,
        initialValue: 'morphology',
        placeholder: 'Select ontology',
        onChange: () => { onChangeCalled = true; },
      });
      dom.window.document.body.appendChild(combobox);
      const inputEl = combobox.querySelector('.combobox-input');
      assert.strictEqual(inputEl.value, 'morphology');

      // When: User focuses the input
      focus(inputEl);

      // Then: Input is cleared to allow typing, onChange is not triggered
      assert.strictEqual(inputEl.value, '', 'Input should be cleared on focus');
      assert.strictEqual(onChangeCalled, false, 'onChange should not be called on focus');

      combobox.remove();
    });

    it('1b. Focus shows existing value as placeholder', () => {
      // Given: A combobox with an existing value selected
      const combobox = createCombobox({
        options: OPTIONS,
        initialValue: 'morphology',
        placeholder: 'Select ontology',
        onChange: () => {},
      });
      dom.window.document.body.appendChild(combobox);
      const inputEl = combobox.querySelector('.combobox-input');

      // When: User focuses the input
      focus(inputEl);

      // Then: Previous value appears as grayed placeholder hint
      assert.strictEqual(inputEl.placeholder, 'morphology', 'Placeholder should show previous value');

      combobox.remove();
    });

    it('1c. Focus displays all options unfiltered', () => {
      // Given: A combobox with an existing value selected
      const combobox = createCombobox({
        options: OPTIONS,
        initialValue: 'morphology',
        placeholder: 'Select ontology',
        onChange: () => {},
      });
      dom.window.document.body.appendChild(combobox);
      const inputEl = combobox.querySelector('.combobox-input');
      const dropdown = combobox.querySelector('.combobox-dropdown');

      // When: User focuses the input
      focus(inputEl);

      // Then: Dropdown opens with all options visible (not filtered)
      assert.ok(dropdown.classList.contains('combobox-dropdown--open'), 'Dropdown should be open');
      const options = dropdown.querySelectorAll('.combobox-option:not(.combobox-option--empty)');
      assert.strictEqual(options.length, OPTIONS.length, `Should show all ${OPTIONS.length} options`);

      combobox.remove();
    });
  });

  describe('REQ 2: Typing Behavior', () => {
    it('2a. Typing filters options case-insensitively', () => {
      // Given: A combobox with dropdown open and all options visible
      const combobox = createCombobox({
        options: OPTIONS,
        initialValue: '',
        placeholder: 'Select ontology',
        onChange: () => {},
      });
      dom.window.document.body.appendChild(combobox);
      const inputEl = combobox.querySelector('.combobox-input');
      const dropdown = combobox.querySelector('.combobox-dropdown');
      focus(inputEl);

      // When: User types uppercase search text
      input(inputEl, 'MORPH');

      // Then: Options are filtered case-insensitively to matching items
      const options = dropdown.querySelectorAll('.combobox-option:not(.combobox-option--empty)');
      assert.strictEqual(options.length, 1, 'Should filter to 1 option');
      assert.strictEqual(options[0].textContent, 'morphology', 'Should match morphology');

      combobox.remove();
    });

    it('2b. Shows "No matches" when no options match', () => {
      // Given: A combobox with dropdown open
      const combobox = createCombobox({
        options: OPTIONS,
        initialValue: '',
        placeholder: 'Select ontology',
        onChange: () => {},
      });
      dom.window.document.body.appendChild(combobox);
      const inputEl = combobox.querySelector('.combobox-input');
      const dropdown = combobox.querySelector('.combobox-dropdown');
      focus(inputEl);

      // When: User types text that matches no options
      input(inputEl, 'xyz_no_match');

      // Then: "No matches" message is displayed
      const emptyOption = dropdown.querySelector('.combobox-option--empty');
      assert.ok(emptyOption, 'Should show empty option element');
      assert.strictEqual(emptyOption.textContent, 'No matches', 'Should show "No matches" message');

      combobox.remove();
    });
  });

  describe('REQ 3: Selection Behavior', () => {
    it('3a. Selection updates input value', () => {
      // Given: A combobox with dropdown open showing options
      const combobox = createCombobox({
        options: OPTIONS,
        initialValue: '',
        placeholder: 'Select ontology',
        onChange: () => {},
      });
      dom.window.document.body.appendChild(combobox);
      const inputEl = combobox.querySelector('.combobox-input');
      const dropdown = combobox.querySelector('.combobox-dropdown');
      focus(inputEl);
      const firstOption = dropdown.querySelector('.combobox-option');

      // When: User clicks an option
      mousedown(firstOption);

      // Then: Input displays the selected option's text
      assert.strictEqual(inputEl.value, firstOption.textContent, 'Input should have selected value');

      combobox.remove();
    });

    it('3b. Selection closes dropdown', () => {
      // Given: A combobox with dropdown open
      const combobox = createCombobox({
        options: OPTIONS,
        initialValue: '',
        placeholder: 'Select ontology',
        onChange: () => {},
      });
      dom.window.document.body.appendChild(combobox);
      const inputEl = combobox.querySelector('.combobox-input');
      const dropdown = combobox.querySelector('.combobox-dropdown');
      focus(inputEl);
      assert.ok(dropdown.classList.contains('combobox-dropdown--open'));
      const firstOption = dropdown.querySelector('.combobox-option');

      // When: User clicks an option
      mousedown(firstOption);

      // Then: Dropdown closes automatically
      assert.ok(!dropdown.classList.contains('combobox-dropdown--open'), 'Dropdown should be closed');

      combobox.remove();
    });

    it('3c. Selection triggers onChange callback', () => {
      // Given: A combobox with an onChange handler attached
      let receivedValue = null;
      const combobox = createCombobox({
        options: OPTIONS,
        initialValue: '',
        placeholder: 'Select ontology',
        onChange: (value) => { receivedValue = value; },
      });
      dom.window.document.body.appendChild(combobox);
      const inputEl = combobox.querySelector('.combobox-input');
      const dropdown = combobox.querySelector('.combobox-dropdown');
      focus(inputEl);
      const firstOption = dropdown.querySelector('.combobox-option');

      // When: User clicks an option
      mousedown(firstOption);

      // Then: onChange callback receives the selected value
      assert.strictEqual(receivedValue, firstOption.textContent, 'onChange should receive selected value');

      combobox.remove();
    });
  });

  describe('REQ 4: Blur Behavior', () => {
    it('4a. Blur restores value when input is empty', async () => {
      // Given: A combobox with existing value, focused (input cleared)
      const combobox = createCombobox({
        options: OPTIONS,
        initialValue: 'morphology',
        placeholder: 'Select ontology',
        onChange: () => {},
      });
      dom.window.document.body.appendChild(combobox);
      const inputEl = combobox.querySelector('.combobox-input');
      focus(inputEl);
      assert.strictEqual(inputEl.value, '');

      // When: User clicks away without typing or selecting
      blur(inputEl);
      await wait(200);

      // Then: Original value is restored
      assert.strictEqual(inputEl.value, 'morphology', 'Value should be restored on blur');

      combobox.remove();
    });

    it('4b. Blur restores value when input is invalid', async () => {
      // Given: A combobox with existing value where user typed invalid text
      const combobox = createCombobox({
        options: OPTIONS,
        initialValue: 'morphology',
        placeholder: 'Select ontology',
        onChange: () => {},
      });
      dom.window.document.body.appendChild(combobox);
      const inputEl = combobox.querySelector('.combobox-input');
      focus(inputEl);
      input(inputEl, 'invalid_garbage');

      // When: User clicks away with invalid text in input
      blur(inputEl);
      await wait(200);

      // Then: Original value is restored, invalid text discarded
      assert.strictEqual(inputEl.value, 'morphology', 'Value should be restored after invalid input');

      combobox.remove();
    });

    it('4c. Blur accepts valid typed option', async () => {
      // Given: A combobox where user typed a valid option name
      let receivedValue = null;
      const combobox = createCombobox({
        options: OPTIONS,
        initialValue: 'morphology',
        placeholder: 'Select ontology',
        onChange: (value) => { receivedValue = value; },
      });
      dom.window.document.body.appendChild(combobox);
      const inputEl = combobox.querySelector('.combobox-input');
      focus(inputEl);
      input(inputEl, 'primary_diagnosis');

      // When: User clicks away with valid option typed
      blur(inputEl);
      await wait(200);

      // Then: Typed value is accepted and onChange is triggered
      assert.strictEqual(inputEl.value, 'primary_diagnosis', 'Valid typed option should be accepted');
      assert.strictEqual(receivedValue, 'primary_diagnosis', 'onChange should be called with typed value');

      combobox.remove();
    });

    it('4d. Blur closes dropdown', async () => {
      // Given: A combobox with dropdown open
      const combobox = createCombobox({
        options: OPTIONS,
        initialValue: '',
        placeholder: 'Select ontology',
        onChange: () => {},
      });
      dom.window.document.body.appendChild(combobox);
      const inputEl = combobox.querySelector('.combobox-input');
      const dropdown = combobox.querySelector('.combobox-dropdown');
      focus(inputEl);
      assert.ok(dropdown.classList.contains('combobox-dropdown--open'));

      // When: User clicks away from the combobox
      blur(inputEl);
      await wait(200);

      // Then: Dropdown closes
      assert.ok(!dropdown.classList.contains('combobox-dropdown--open'), 'Dropdown should be closed');

      combobox.remove();
    });
  });

  describe('Edge Cases', () => {
    it('Empty options array shows "No matches"', () => {
      // Given: A combobox initialized with no options available
      const combobox = createCombobox({
        options: [],
        initialValue: '',
        placeholder: 'Select ontology',
        onChange: () => {},
      });
      dom.window.document.body.appendChild(combobox);
      const inputEl = combobox.querySelector('.combobox-input');
      const dropdown = combobox.querySelector('.combobox-dropdown');

      // When: User focuses to open dropdown
      focus(inputEl);

      // Then: "No matches" message is shown instead of options
      const emptyOption = dropdown.querySelector('.combobox-option--empty');
      assert.ok(emptyOption, 'Should show empty option element');
      assert.strictEqual(emptyOption.textContent, 'No matches', 'Should show "No matches"');

      combobox.remove();
    });

    it('Partial string matching filters correctly', () => {
      // Given: A combobox with dropdown open
      const combobox = createCombobox({
        options: OPTIONS,
        initialValue: '',
        placeholder: 'Select ontology',
        onChange: () => {},
      });
      dom.window.document.body.appendChild(combobox);
      const inputEl = combobox.querySelector('.combobox-input');
      const dropdown = combobox.querySelector('.combobox-dropdown');
      focus(inputEl);

      // When: User types a substring that appears in the middle of an option
      input(inputEl, 'diag');

      // Then: Options containing the substring are shown
      const options = dropdown.querySelectorAll('.combobox-option:not(.combobox-option--empty)');
      assert.strictEqual(options.length, 1, 'Should match one option');
      assert.strictEqual(options[0].textContent, 'primary_diagnosis', 'Should match primary_diagnosis');

      combobox.remove();
    });

    it('No initial value starts with empty input and default placeholder', () => {
      // Given: A combobox created without an initial value
      const combobox = createCombobox({
        options: OPTIONS,
        initialValue: '',
        placeholder: 'Select ontology',
        onChange: () => {},
      });
      dom.window.document.body.appendChild(combobox);
      const inputEl = combobox.querySelector('.combobox-input');

      // When: Combobox is rendered (no user interaction)

      // Then: Input is empty with default placeholder text
      assert.strictEqual(inputEl.value, '', 'Input should be empty');
      assert.strictEqual(inputEl.placeholder, 'Select ontology', 'Placeholder should be default');

      combobox.remove();
    });

    it('Focus on empty combobox keeps default placeholder', () => {
      // Given: A combobox with no value selected
      const combobox = createCombobox({
        options: OPTIONS,
        initialValue: '',
        placeholder: 'Select ontology',
        onChange: () => {},
      });
      dom.window.document.body.appendChild(combobox);
      const inputEl = combobox.querySelector('.combobox-input');

      // When: User focuses the empty combobox
      focus(inputEl);

      // Then: Default placeholder remains (no previous value to show)
      assert.strictEqual(inputEl.placeholder, 'Select ontology', 'Placeholder should remain default when no value');

      combobox.remove();
    });
  });

  describe('Separator and Muted Options', () => {
    it('Separator appears after specified index when unfiltered', () => {
      // Given: A combobox with separatorAfterIndex set to 0
      const combobox = createCombobox({
        options: OPTIONS,
        initialValue: '',
        placeholder: 'Select ontology',
        separatorAfterIndex: 0,
        onChange: () => {},
      });
      dom.window.document.body.appendChild(combobox);
      const inputEl = combobox.querySelector('.combobox-input');
      const dropdown = combobox.querySelector('.combobox-dropdown');

      // When: User focuses to open dropdown (unfiltered)
      focus(inputEl);

      // Then: A separator element appears after the first option
      const separator = dropdown.querySelector('.combobox-separator');
      assert.ok(separator, 'Separator should exist');
      assert.strictEqual(separator.getAttribute('role'), 'separator', 'Should have separator role');

      const children = Array.from(dropdown.children);
      const separatorIndex = children.indexOf(separator);
      assert.strictEqual(separatorIndex, 1, 'Separator should appear after first option (index 1)');

      combobox.remove();
    });

    it('Separator is hidden when filtering', () => {
      // Given: A combobox with separatorAfterIndex set
      const combobox = createCombobox({
        options: OPTIONS,
        initialValue: '',
        placeholder: 'Select ontology',
        separatorAfterIndex: 0,
        onChange: () => {},
      });
      dom.window.document.body.appendChild(combobox);
      const inputEl = combobox.querySelector('.combobox-input');
      const dropdown = combobox.querySelector('.combobox-dropdown');
      focus(inputEl);

      // When: User types to filter options
      input(inputEl, 'morph');

      // Then: Separator is not shown during filtering
      const separator = dropdown.querySelector('.combobox-separator');
      assert.ok(!separator, 'Separator should not appear when filtering');

      combobox.remove();
    });

    it('Muted option has muted class applied', () => {
      // Given: A combobox with mutedIndices including index 0
      const combobox = createCombobox({
        options: OPTIONS,
        initialValue: '',
        placeholder: 'Select ontology',
        mutedIndices: [0],
        onChange: () => {},
      });
      dom.window.document.body.appendChild(combobox);
      const inputEl = combobox.querySelector('.combobox-input');
      const dropdown = combobox.querySelector('.combobox-dropdown');

      // When: User focuses to open dropdown
      focus(inputEl);

      // Then: First option has muted class, others do not
      const options = dropdown.querySelectorAll('.combobox-option:not(.combobox-option--empty)');
      assert.ok(options[0].classList.contains('combobox-option--muted'), 'First option should be muted');
      assert.ok(!options[1].classList.contains('combobox-option--muted'), 'Second option should not be muted');

      combobox.remove();
    });

    it('Multiple muted indices are applied correctly', () => {
      // Given: A combobox with multiple mutedIndices
      const combobox = createCombobox({
        options: OPTIONS,
        initialValue: '',
        placeholder: 'Select ontology',
        mutedIndices: [0, 2],
        onChange: () => {},
      });
      dom.window.document.body.appendChild(combobox);
      const inputEl = combobox.querySelector('.combobox-input');
      const dropdown = combobox.querySelector('.combobox-dropdown');

      // When: User focuses to open dropdown
      focus(inputEl);

      // Then: Options at indices 0 and 2 have muted class
      const options = dropdown.querySelectorAll('.combobox-option:not(.combobox-option--empty)');
      assert.ok(options[0].classList.contains('combobox-option--muted'), 'Option at index 0 should be muted');
      assert.ok(!options[1].classList.contains('combobox-option--muted'), 'Option at index 1 should not be muted');
      assert.ok(options[2].classList.contains('combobox-option--muted'), 'Option at index 2 should be muted');

      combobox.remove();
    });

    it('Muted option preserves original index when filtering', () => {
      // Given: A combobox with first option muted
      const testOptions = ['No mapping', 'primary_diagnosis', 'morphology'];
      const combobox = createCombobox({
        options: testOptions,
        initialValue: '',
        placeholder: 'Select ontology',
        mutedIndices: [0],
        onChange: () => {},
      });
      dom.window.document.body.appendChild(combobox);
      const inputEl = combobox.querySelector('.combobox-input');
      const dropdown = combobox.querySelector('.combobox-dropdown');
      focus(inputEl);

      // When: User filters to show only "No mapping"
      input(inputEl, 'No map');

      // Then: The filtered "No mapping" option still has muted class
      const options = dropdown.querySelectorAll('.combobox-option:not(.combobox-option--empty)');
      assert.strictEqual(options.length, 1, 'Should show one filtered option');
      assert.ok(options[0].classList.contains('combobox-option--muted'), 'Filtered option should retain muted class');

      combobox.remove();
    });

    it('Selecting muted option works normally', () => {
      // Given: A combobox with muted first option
      let receivedValue = null;
      const combobox = createCombobox({
        options: OPTIONS,
        initialValue: '',
        placeholder: 'Select ontology',
        mutedIndices: [0],
        onChange: (value) => { receivedValue = value; },
      });
      dom.window.document.body.appendChild(combobox);
      const inputEl = combobox.querySelector('.combobox-input');
      const dropdown = combobox.querySelector('.combobox-dropdown');
      focus(inputEl);
      const firstOption = dropdown.querySelector('.combobox-option');

      // When: User clicks the muted option
      mousedown(firstOption);

      // Then: Selection works normally despite muted styling
      assert.strictEqual(receivedValue, OPTIONS[0], 'onChange should receive muted option value');
      assert.strictEqual(inputEl.value, OPTIONS[0], 'Input should have muted option value');

      combobox.remove();
    });
  });

  describe('REQ 5: Toggle Button', () => {
    it('5a. Toggle opens dropdown when closed', () => {
      // Given: A combobox with dropdown closed
      const combobox = createCombobox({
        options: OPTIONS,
        initialValue: 'morphology',
        placeholder: 'Select ontology',
        onChange: () => {},
      });
      dom.window.document.body.appendChild(combobox);
      const dropdown = combobox.querySelector('.combobox-dropdown');
      const toggleBtn = combobox.querySelector('.combobox-toggle');
      assert.ok(!dropdown.classList.contains('combobox-dropdown--open'));

      // When: User clicks the toggle button
      click(toggleBtn);

      // Then: Dropdown opens
      assert.ok(dropdown.classList.contains('combobox-dropdown--open'), 'Dropdown should be open after toggle');

      combobox.remove();
    });

    it('5b. Toggle closes dropdown when open', () => {
      // Given: A combobox with dropdown already open
      const combobox = createCombobox({
        options: OPTIONS,
        initialValue: 'morphology',
        placeholder: 'Select ontology',
        onChange: () => {},
      });
      dom.window.document.body.appendChild(combobox);
      const dropdown = combobox.querySelector('.combobox-dropdown');
      const toggleBtn = combobox.querySelector('.combobox-toggle');
      click(toggleBtn);
      assert.ok(dropdown.classList.contains('combobox-dropdown--open'));

      // When: User clicks the toggle button again
      click(toggleBtn);

      // Then: Dropdown closes
      assert.ok(!dropdown.classList.contains('combobox-dropdown--open'), 'Dropdown should be closed');

      combobox.remove();
    });

    it('5c. Toggle shows all options and clears input', () => {
      // Given: A combobox with an existing value selected
      const combobox = createCombobox({
        options: OPTIONS,
        initialValue: 'morphology',
        placeholder: 'Select ontology',
        onChange: () => {},
      });
      dom.window.document.body.appendChild(combobox);
      const inputEl = combobox.querySelector('.combobox-input');
      const dropdown = combobox.querySelector('.combobox-dropdown');
      const toggleBtn = combobox.querySelector('.combobox-toggle');

      // When: User clicks the toggle button to open dropdown
      click(toggleBtn);

      // Then: Input is cleared, value shown as placeholder, all options visible
      assert.strictEqual(inputEl.value, '', 'Input should be cleared');
      assert.strictEqual(inputEl.placeholder, 'morphology', 'Placeholder should show value');
      const options = dropdown.querySelectorAll('.combobox-option:not(.combobox-option--empty)');
      assert.strictEqual(options.length, OPTIONS.length, 'Should show all options');

      combobox.remove();
    });
  });
});
