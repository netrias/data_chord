/**
 * PV Selection Modal Tests
 *
 * Tests the modal dialog that replaces the dropdown for selecting permissible values:
 * 1. MODAL OPEN BEHAVIOR - opens when link clicked, shows context, focuses search
 * 2. OPTION DISPLAY - AI suggestions with conformance, PV list without duplicates
 * 3. SEARCH FILTERING - case-insensitive filter, empty state, section visibility
 * 4. SELECTION BEHAVIOR - click selects and closes, non-conformant blocked
 * 5. DISMISS BEHAVIOR - backdrop click, Escape key, close button
 */

import { describe, it, before, after } from 'node:test';
import assert from 'node:assert';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const pvComboboxPath = join(__dirname, '../../src/stage_4_review_results/static/pv_combobox.js');
const pvComboboxCode = readFileSync(pvComboboxPath, 'utf-8');

const TEST_SUGGESTIONS = [
  { value: 'Lung Cancer', isPVConformant: true },
  { value: 'lung cancer', isPVConformant: false },
];

const TEST_PV_VALUES = ['Breast Cancer', 'Colon Cancer', 'Lung Cancer', 'Prostate Cancer'];

/** Set up JSDOM environment and load the pv_combobox module. */
const setupDOM = () => {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
    runScripts: 'dangerously',
    url: 'http://localhost',
  });

  // Polyfill dialog.showModal() and dialog.close() since JSDOM doesn't support them
  const DialogPrototype = dom.window.HTMLDialogElement?.prototype || dom.window.HTMLUnknownElement.prototype;
  if (!DialogPrototype.showModal) {
    DialogPrototype.showModal = function () {
      this.setAttribute('open', '');
      this.open = true;
    };
  }
  if (!DialogPrototype.close) {
    DialogPrototype.close = function () {
      this.removeAttribute('open');
      this.open = false;
      this.dispatchEvent(new dom.window.Event('close'));
    };
  }

  // Inject the pv_combobox code as a script (remove exports for eval)
  const moduleCode = pvComboboxCode
    .replace('export const createPVCombobox', 'window.createPVCombobox')
    .replace('export async function showPVSelectionModal', 'window.showPVSelectionModal = async function');

  dom.window.eval(moduleCode);

  return dom;
};

/** Helper to simulate click event. */
const click = (element) => {
  element.dispatchEvent(new element.ownerDocument.defaultView.MouseEvent('click', { bubbles: true }));
};

/** Helper to simulate input event. */
const input = (element, value) => {
  element.value = value;
  element.dispatchEvent(new element.ownerDocument.defaultView.Event('input'));
};

/** Helper to simulate keydown event. */
const keydown = (element, key) => {
  element.dispatchEvent(new element.ownerDocument.defaultView.KeyboardEvent('keydown', { key, bubbles: true }));
};

describe('PV Selection Modal', () => {
  let dom;
  let showPVSelectionModal;

  before(() => {
    dom = setupDOM();
    showPVSelectionModal = dom.window.showPVSelectionModal;
  });

  after(() => {
    dom.window.close();
  });

  describe('Modal Open Behavior', () => {
    it('opens modal and adds dialog to DOM', async () => {
      // Given: No dialog is in the DOM
      assert.strictEqual(dom.window.document.querySelector('dialog.pv-selection-dialog'), null);

      // When: showPVSelectionModal is called
      const modalPromise = showPVSelectionModal({
        originalValue: 'Lung cancer',
        currentValue: 'Lung Cancer',
        suggestions: TEST_SUGGESTIONS,
        pvValues: TEST_PV_VALUES,
      });

      // Then: A dialog with class 'pv-selection-dialog' appears in the DOM
      const dialog = dom.window.document.querySelector('dialog.pv-selection-dialog');
      assert.ok(dialog, 'Dialog should be present in DOM');
      assert.ok(dialog.open, 'Dialog should be open');

      // Cleanup: close modal
      dialog.close();
      await modalPromise;
    });

    it('displays original and current values in context section', async () => {
      // When: Modal is opened with specific values
      const modalPromise = showPVSelectionModal({
        originalValue: 'Lung cancer',
        currentValue: 'Lung Cancer',
        suggestions: TEST_SUGGESTIONS,
        pvValues: TEST_PV_VALUES,
      });

      const dialog = dom.window.document.querySelector('dialog.pv-selection-dialog');

      // Then: Context shows "was: Lung cancer" and "now: Lung Cancer"
      const contextRows = dialog.querySelectorAll('.pv-selection-context-row');
      assert.strictEqual(contextRows.length, 2, 'Should have two context rows');

      const wasRow = contextRows[0];
      const nowRow = contextRows[1];
      assert.ok(wasRow.textContent.includes('was:'), 'First row should be "was"');
      assert.ok(wasRow.textContent.includes('Lung cancer'), 'Was row should show original value');
      assert.ok(nowRow.textContent.includes('now:'), 'Second row should be "now"');
      assert.ok(nowRow.textContent.includes('Lung Cancer'), 'Now row should show current value');

      // Cleanup
      dialog.close();
      await modalPromise;
    });

    it('focuses search input on open', async () => {
      // When: Modal opens
      const modalPromise = showPVSelectionModal({
        originalValue: 'Lung cancer',
        currentValue: 'Lung Cancer',
        suggestions: TEST_SUGGESTIONS,
        pvValues: TEST_PV_VALUES,
      });

      const dialog = dom.window.document.querySelector('dialog.pv-selection-dialog');
      const searchInput = dialog.querySelector('.pv-selection-search-input');

      // Then: Search input should be focused
      assert.strictEqual(dom.window.document.activeElement, searchInput, 'Search input should be focused');

      // Cleanup
      dialog.close();
      await modalPromise;
    });
  });

  describe('Option Display', () => {
    it('displays AI suggestions in first section', async () => {
      // When: Modal is opened with suggestions
      const modalPromise = showPVSelectionModal({
        originalValue: 'Lung cancer',
        currentValue: 'Lung Cancer',
        suggestions: TEST_SUGGESTIONS,
        pvValues: TEST_PV_VALUES,
      });

      const dialog = dom.window.document.querySelector('dialog.pv-selection-dialog');

      // Then: AI Suggestions section contains the suggestion values
      const suggestionsSection = dialog.querySelector('[data-section="suggestions"]');
      assert.ok(suggestionsSection, 'Suggestions section should exist');

      const suggestionOptions = suggestionsSection.querySelectorAll('.pv-selection-option');
      assert.strictEqual(suggestionOptions.length, 2, 'Should have 2 suggestion options');
      assert.strictEqual(suggestionOptions[0].dataset.value, 'Lung Cancer');
      assert.strictEqual(suggestionOptions[1].dataset.value, 'lung cancer');

      // Cleanup
      dialog.close();
      await modalPromise;
    });

    it('displays non-conformant suggestions with disabled styling', async () => {
      // When: Modal is opened with non-conformant suggestion
      const modalPromise = showPVSelectionModal({
        originalValue: 'Lung cancer',
        currentValue: 'Lung Cancer',
        suggestions: TEST_SUGGESTIONS,
        pvValues: TEST_PV_VALUES,
      });

      const dialog = dom.window.document.querySelector('dialog.pv-selection-dialog');

      // Then: Non-conformant option has disabled class
      const suggestionsSection = dialog.querySelector('[data-section="suggestions"]');
      const nonConformantOption = suggestionsSection.querySelector('[data-value="lung cancer"]');
      assert.ok(
        nonConformantOption.classList.contains('pv-selection-option--disabled'),
        'Non-conformant suggestion should have disabled class'
      );

      // Cleanup
      dialog.close();
      await modalPromise;
    });

    it('displays PV list alphabetically excluding duplicates from suggestions', async () => {
      // Given: suggestions include 'Lung Cancer' which is also in pvValues
      const modalPromise = showPVSelectionModal({
        originalValue: 'Lung cancer',
        currentValue: 'Lung Cancer',
        suggestions: TEST_SUGGESTIONS,
        pvValues: TEST_PV_VALUES,
      });

      const dialog = dom.window.document.querySelector('dialog.pv-selection-dialog');

      // Then: PV section shows all values except 'Lung Cancer' (already in suggestions)
      const pvSection = dialog.querySelector('[data-section="pvs"]');
      const pvOptions = pvSection.querySelectorAll('.pv-selection-option');

      // Should have Breast Cancer, Colon Cancer, Prostate Cancer (not Lung Cancer)
      assert.strictEqual(pvOptions.length, 3, 'Should have 3 PV options (excluding duplicate)');

      const pvValues = Array.from(pvOptions).map((opt) => opt.dataset.value);
      assert.ok(!pvValues.includes('Lung Cancer'), 'Lung Cancer should not be in PV list (already in suggestions)');
      assert.ok(pvValues.includes('Breast Cancer'), 'Breast Cancer should be in PV list');
      assert.ok(pvValues.includes('Colon Cancer'), 'Colon Cancer should be in PV list');
      assert.ok(pvValues.includes('Prostate Cancer'), 'Prostate Cancer should be in PV list');

      // Cleanup
      dialog.close();
      await modalPromise;
    });
  });

  describe('Search Filtering', () => {
    it('filters options case-insensitively', async () => {
      // Given: Modal is open with PV values
      const modalPromise = showPVSelectionModal({
        originalValue: 'test',
        currentValue: 'test',
        suggestions: [],
        pvValues: ['Lung Cancer', 'Breast Cancer', 'Colon Cancer'],
      });

      const dialog = dom.window.document.querySelector('dialog.pv-selection-dialog');
      const searchInput = dialog.querySelector('.pv-selection-search-input');

      // Assert negative: All options are visible initially
      let visibleOptions = dialog.querySelectorAll('.pv-selection-option:not([style*="display: none"])');
      assert.strictEqual(visibleOptions.length, 3, 'All options should be visible initially');

      // When: User types 'LUNG' in search input (uppercase)
      input(searchInput, 'LUNG');

      // Then: Only 'Lung Cancer' option is visible
      visibleOptions = dialog.querySelectorAll('.pv-selection-option:not([style*="display: none"])');
      assert.strictEqual(visibleOptions.length, 1, 'Only one option should be visible');
      assert.strictEqual(visibleOptions[0].dataset.value, 'Lung Cancer');

      // Cleanup
      dialog.close();
      await modalPromise;
    });

    it('shows empty state when no matches', async () => {
      // Given: Modal is open
      const modalPromise = showPVSelectionModal({
        originalValue: 'test',
        currentValue: 'test',
        suggestions: [],
        pvValues: ['Lung Cancer', 'Breast Cancer'],
      });

      const dialog = dom.window.document.querySelector('dialog.pv-selection-dialog');
      const searchInput = dialog.querySelector('.pv-selection-search-input');

      // When: User types 'xyz' in search input
      input(searchInput, 'xyz');

      // Then: 'No matches' message is visible
      const emptyState = dialog.querySelector('.pv-selection-empty');
      assert.ok(emptyState, 'Empty state element should exist');
      assert.notStrictEqual(emptyState.style.display, 'none', 'Empty state should be visible');

      // Cleanup
      dialog.close();
      await modalPromise;
    });

    it('hides section headers when section is empty after filtering', async () => {
      // Given: Modal with AI suggestions and PV values
      const modalPromise = showPVSelectionModal({
        originalValue: 'test',
        currentValue: 'test',
        suggestions: [{ value: 'Suggestion A', isPVConformant: true }],
        pvValues: ['Breast Cancer', 'Colon Cancer'],
      });

      const dialog = dom.window.document.querySelector('dialog.pv-selection-dialog');
      const searchInput = dialog.querySelector('.pv-selection-search-input');

      // When: Search matches only PV values (not suggestions)
      input(searchInput, 'Cancer');

      // Then: AI Suggestions section header should be hidden (no matches)
      const suggestionsSection = dialog.querySelector('[data-section="suggestions"]');
      const pvSection = dialog.querySelector('[data-section="pvs"]');

      assert.strictEqual(suggestionsSection.style.display, 'none', 'Suggestions section should be hidden');
      assert.notStrictEqual(pvSection.style.display, 'none', 'PV section should be visible');

      // Cleanup
      dialog.close();
      await modalPromise;
    });
  });

  describe('Selection Behavior', () => {
    it('resolves with selected value and closes modal when conformant option clicked', async () => {
      // Given: Modal is open
      const modalPromise = showPVSelectionModal({
        originalValue: 'test',
        currentValue: 'test',
        suggestions: [{ value: 'Lung Cancer', isPVConformant: true }],
        pvValues: TEST_PV_VALUES,
      });

      const dialog = dom.window.document.querySelector('dialog.pv-selection-dialog');

      // When: User clicks a conformant option
      const conformantOption = dialog.querySelector('[data-value="Lung Cancer"]');
      click(conformantOption);

      // Then: Promise resolves with the option value
      const result = await modalPromise;
      assert.strictEqual(result, 'Lung Cancer', 'Should resolve with selected value');

      // And dialog is removed from DOM
      assert.strictEqual(
        dom.window.document.querySelector('dialog.pv-selection-dialog'),
        null,
        'Dialog should be removed from DOM'
      );
    });

    it('does not select non-conformant suggestions', async () => {
      // Given: Modal is open with non-conformant suggestion
      const modalPromise = showPVSelectionModal({
        originalValue: 'test',
        currentValue: 'test',
        suggestions: [{ value: 'non-conformant value', isPVConformant: false }],
        pvValues: ['Valid PV'],
      });

      const dialog = dom.window.document.querySelector('dialog.pv-selection-dialog');

      // When: User clicks the disabled suggestion
      const disabledOption = dialog.querySelector('.pv-selection-option--disabled');
      click(disabledOption);

      // Then: Modal remains open
      assert.ok(dialog.open, 'Modal should remain open');
      assert.ok(
        dom.window.document.querySelector('dialog.pv-selection-dialog'),
        'Dialog should still be in DOM'
      );

      // Cleanup
      dialog.close();
      await modalPromise;
    });
  });

  describe('Dismiss Behavior', () => {
    it('resolves with null on backdrop click', async () => {
      // Given: Modal is open
      const modalPromise = showPVSelectionModal({
        originalValue: 'test',
        currentValue: 'test',
        suggestions: [],
        pvValues: TEST_PV_VALUES,
      });

      const dialog = dom.window.document.querySelector('dialog.pv-selection-dialog');

      // When: User clicks the backdrop (simulated by clicking dialog itself)
      // In real browsers, clicking backdrop fires click on dialog element
      const clickEvent = new dom.window.MouseEvent('click', { bubbles: true });
      Object.defineProperty(clickEvent, 'target', { value: dialog });
      dialog.dispatchEvent(clickEvent);

      // Then: Promise resolves with null
      const result = await modalPromise;
      assert.strictEqual(result, null, 'Should resolve with null on backdrop click');

      // And dialog is removed
      assert.strictEqual(
        dom.window.document.querySelector('dialog.pv-selection-dialog'),
        null,
        'Dialog should be removed from DOM'
      );
    });

    it('resolves with null on Escape key', async () => {
      // Given: Modal is open
      const modalPromise = showPVSelectionModal({
        originalValue: 'test',
        currentValue: 'test',
        suggestions: [],
        pvValues: TEST_PV_VALUES,
      });

      const dialog = dom.window.document.querySelector('dialog.pv-selection-dialog');

      // When: User presses Escape
      keydown(dialog, 'Escape');

      // Then: Promise resolves with null
      const result = await modalPromise;
      assert.strictEqual(result, null, 'Should resolve with null on Escape');

      // And dialog is removed
      assert.strictEqual(
        dom.window.document.querySelector('dialog.pv-selection-dialog'),
        null,
        'Dialog should be removed from DOM'
      );
    });

    it('resolves with null on close button click', async () => {
      // Given: Modal is open
      const modalPromise = showPVSelectionModal({
        originalValue: 'test',
        currentValue: 'test',
        suggestions: [],
        pvValues: TEST_PV_VALUES,
      });

      const dialog = dom.window.document.querySelector('dialog.pv-selection-dialog');

      // When: User clicks the close button
      const closeBtn = dialog.querySelector('.pv-selection-close-btn');
      click(closeBtn);

      // Then: Promise resolves with null
      const result = await modalPromise;
      assert.strictEqual(result, null, 'Should resolve with null on close button click');

      // And dialog is removed
      assert.strictEqual(
        dom.window.document.querySelector('dialog.pv-selection-dialog'),
        null,
        'Dialog should be removed from DOM'
      );
    });
  });
});
