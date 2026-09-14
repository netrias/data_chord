import { it } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

import { createValueCard } from '../../src/stage_4_review_results/static/shared_review_utils.js';

it('renders a saved space-only edit as neutral after reload', () => {
  // Given: a present source has a persisted space-only manual edit.
  const dom = new JSDOM('<!doctype html><body></body>');
  const previousWindow = globalThis.window;
  const previousDocument = globalThis.document;
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  let card;
  try {
    // When: the saved review response creates the card again.
    card = createValueCard({
      entry: {
        originalValue: 'Unapproved original', harmonizedValue: null,
        recommendationType: 'no_recommendation', matchFidelity: 'none',
        pvSetAvailable: true, rowIndices: [1], columnKey: 'col_0000',
        columnLabel: 'diagnosis', topSuggestions: [],
      },
      labelText: '1 row', tooltipText: null,
      pendingOverrides: {
        '1': { col_0000: { original_value: 'Unapproved original', human_value: '   ' } },
      },
      columnPVs: { col_0000: ['Diabetes'] },
      onOverrideChange: () => {},
    });
    // Then: it retains the saved text and shows neither approval nor a warning.
    assert.equal(card.querySelector('.pv-combobox-link').textContent, '   ');
    assert.equal(card.querySelector('.pv-warning-icon').style.display, 'none');
    assert.equal(card.querySelector('.pv-conformant-icon').style.display, 'none');
  } finally {
    card?.destroy();
    dom.window.close();
    globalThis.window = previousWindow;
    globalThis.document = previousDocument;
  }
});
