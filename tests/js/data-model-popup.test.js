import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { JSDOM } from 'jsdom';

describe('data model popup', () => {
  it('selects the first model and its newest version by default', async () => {
    const dom = new JSDOM('<!doctype html><body></body>', { url: 'http://localhost/stage-1' });
    const originalWindow = globalThis.window;
    const originalDocument = globalThis.document;
    const originalFetch = globalThis.fetch;

    globalThis.window = dom.window;
    globalThis.document = dom.window.document;
    globalThis.fetch = async () => ({
      ok: true,
      json: async () => [
        {
          data_model_key: 'first',
          label: 'First model',
          versions: [
            { external_version_number: '1.10.0' },
            { external_version_number: '2.0.0' },
            { external_version_number: '1.2.0' },
          ],
        },
        {
          data_model_key: 'second',
          label: 'Second model',
          versions: [{ external_version_number: '9.0.0' }],
        },
      ],
    });
    dom.window.HTMLDialogElement.prototype.showModal = function showModal() {
      this.setAttribute('open', '');
    };
    dom.window.HTMLDialogElement.prototype.close = function close() {
      this.removeAttribute('open');
      this.dispatchEvent(new dom.window.Event('close'));
    };

    try {
      const popup = await import('../../src/stage_1_upload/static/data_model_popup.js?first-model-test');
      popup.preloadDataModels();
      const selectionPromise = popup.showDataModelPopup();
      await new Promise((resolve) => setImmediate(resolve));

      const dialog = document.querySelector('.data-model-dialog');
      assert.ok(dialog);
      assert.equal(
        dialog.querySelector('#dataModelDropdownTrigger .data-model-dropdown-label')?.textContent,
        'First model',
      );
      assert.equal(
        dialog.querySelector('#versionDropdownTrigger .data-model-dropdown-label')?.textContent,
        '2.0.0',
      );

      dialog.querySelector('.data-model-confirm-btn')?.click();
      assert.deepEqual(await selectionPromise, {
        dataModelKey: 'first',
        externalVersionNumber: '2.0.0',
      });
    } finally {
      globalThis.window = originalWindow;
      globalThis.document = originalDocument;
      globalThis.fetch = originalFetch;
      dom.window.close();
    }
  });
});
