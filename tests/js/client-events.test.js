import { describe, it, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert';
import { JSDOM } from 'jsdom';

import { CLIENT_EVENT_ENDPOINT, reportFetchFailure } from '../../src/shared/static/client-events.js';

describe('Client Event Reporter', () => {
  let dom;
  let sendBeaconCalls;
  let fetchCalls;

  beforeEach(() => {
    dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', { url: 'https://app.example.test/stage-1' });
    sendBeaconCalls = [];
    fetchCalls = [];
    Object.defineProperty(globalThis, 'window', { value: dom.window, configurable: true });
    Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true });
    Object.defineProperty(globalThis.navigator, 'onLine', { value: true, configurable: true });
    Object.defineProperty(globalThis.navigator, 'sendBeacon', {
      value: (url, body) => {
        sendBeaconCalls.push({ url, body });
        return true;
      },
      configurable: true,
    });
    Object.defineProperty(globalThis, 'fetch', {
      value: (...args) => {
        fetchCalls.push(args);
        return Promise.resolve(new dom.window.Response(null, { status: 204 }));
      },
      configurable: true,
    });
  });

  afterEach(() => {
    dom.window.close();
    delete globalThis.window;
    delete globalThis.navigator;
    delete globalThis.fetch;
  });

  it('reports fetch failures with a safe path and workflow id', async () => {
    // Given: the browser saw a failed Stage 1 analyze fetch
    const error = new TypeError('Failed to fetch');

    // When: the client reports the failure
    reportFetchFailure({
      stage: 'stage_1',
      operation: 'analyze',
      endpoint: '/stage-1/analyze?token=not-for-logs',
      fileId: 'abcdef1234567890',
      error,
    });

    // Then: the beacon payload keeps only safe operational fields
    assert.strictEqual(sendBeaconCalls.length, 1);
    assert.strictEqual(fetchCalls.length, 0);
    assert.strictEqual(sendBeaconCalls[0].url, CLIENT_EVENT_ENDPOINT);
    const payload = JSON.parse(await sendBeaconCalls[0].body.text());
    assert.strictEqual(payload.event_name, 'client.fetch.failed');
    assert.strictEqual(payload.stage, 'stage_1');
    assert.strictEqual(payload.operation, 'analyze');
    assert.strictEqual(payload.path, '/stage-1/analyze');
    assert.strictEqual(payload.file_id, 'abcdef1234567890');
    assert.strictEqual(payload.error_name, 'TypeError');
    assert.strictEqual(payload.error_message, 'Failed to fetch');
    assert.strictEqual(payload.online, true);
    assert.ok(Number.isInteger(payload.timestamp_ms));
  });
});
