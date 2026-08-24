import assert from 'node:assert/strict';
import { test } from 'node:test';

import { JSDOM } from 'jsdom';

test('demo mode replaces stale browser state and cannot open file selection', async () => {
  // Given: Stage 1 has a packaged demo upload and stale state from another workflow.
  const dom = new JSDOM(`<!doctype html><body>
    <nav class="progress-tracker"><span class="step" data-stage="upload"></span></nav>
    <div id="stepInstruction"><p class="step-instruction-text"></p><span class="step-instruction-tooltip"></span></div>
    <div id="dropzone">
      <div id="dropzoneCopy"></div>
      <div id="dropzoneUploading" class="hidden"></div>
      <span id="uploadingFileName"></span>
      <div id="dropzoneFile" class="hidden"></div>
      <span id="dropzoneFileName"></span>
      <span id="dropzoneFileSize"></span>
      <span id="dropzoneFileStatus"></span>
      <button id="changeFileButton" type="button"></button>
    </div>
    <input id="fileInput" type="file" disabled />
    <button id="analyzeButton" type="button" disabled></button>
    <p id="statusMessage"></p>
    <div id="analyzeOverlay" class="hidden"></div>
    <div id="sheetSelectorPanel" class="hidden"></div>
    <select id="sheetSelect"></select>
    <div id="sheetTabsList"></div>
    <span id="sheetCountBadge"></span>
    <div id="sheetPreviewPopover"></div>
    <div id="sheetPreviewPopoverTitle"></div>
    <div id="sheetPreviewPopoverBody"></div>
  </body>`, { url: 'http://localhost/stage-1' });
  const originalWindow = globalThis.window;
  const originalDocument = globalThis.document;
  const originalSessionStorage = globalThis.sessionStorage;
  const originalFetch = globalThis.fetch;
  const demoUpload = {
    file_id: '00000000000000000000000000000001',
    file_name: 'sample.csv',
    human_size: '123.0 B',
    sheet_names: [],
    sheet_previews: {},
    selected_sheet: null,
  };
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  globalThis.sessionStorage = dom.window.sessionStorage;
  globalThis.fetch = async () => ({ ok: true, json: async () => [] });
  dom.window.stageOneUploadConfig = {
    demoUpload,
    uploadEndpoint: '/stage-1/upload',
    analyzeEndpoint: '/stage-1/analyze',
  };
  sessionStorage.setItem('currentFileSession', JSON.stringify({
    file_id: 'ffffffffffffffffffffffffffffffff',
    original_name: 'stale.csv',
  }));
  sessionStorage.setItem('stage2Payload', JSON.stringify({ stale: true }));
  let pickerOpenCount = 0;
  document.getElementById('fileInput').click = () => { pickerOpenCount += 1; };

  try {
    // When: the real Stage 1 script starts and receives click, key, and drop input.
    await import('../../src/stage_1_upload/static/stage_1_upload.js?demo-lock-test');
    const dropzone = document.getElementById('dropzone');
    dropzone.click();
    dropzone.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    const drop = new dom.window.Event('drop', { bubbles: true, cancelable: true });
    dropzone.dispatchEvent(drop);
    await new Promise((resolve) => setImmediate(resolve));

    // Then: only the packaged file is ready and all replacement paths stay closed.
    const stored = JSON.parse(sessionStorage.getItem('currentFileSession'));
    assert.equal(stored.file_id, demoUpload.file_id);
    assert.equal(stored.original_name, 'sample.csv');
    assert.equal(sessionStorage.getItem('stage2Payload'), null);
    assert.equal(document.getElementById('dropzoneFileName').textContent, 'sample.csv');
    assert.equal(document.getElementById('dropzoneFileStatus').textContent, 'Ready for demo');
    assert.equal(document.getElementById('fileInput').disabled, true);
    assert.equal(document.getElementById('analyzeButton').disabled, false);
    assert.equal(pickerOpenCount, 0);
    assert.equal(drop.defaultPrevented, true);
  } finally {
    globalThis.window = originalWindow;
    globalThis.document = originalDocument;
    globalThis.sessionStorage = originalSessionStorage;
    globalThis.fetch = originalFetch;
    dom.window.close();
  }
});
