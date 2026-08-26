/** Contract tests for private, stable browser navigation timing. */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import {
  captureBrowserTiming,
  normalizeBrowserTiming,
} from '../e2e/performance-browser-timing.mjs';

describe('browser performance timing', () => {
  it('keeps a real zero distinct from an unavailable metric', () => {
    // Given: navigation timing has one real zero and one unavailable value
    const timing = {
      origin: 'https://staging.example.test',
      navigation: {
        type: 'navigate',
        responseStart: undefined,
        responseEnd: 10,
        domContentLoadedEventEnd: 20,
        loadEventEnd: 30,
        transferSize: 0,
        encodedBodySize: 100,
        decodedBodySize: 200,
      },
      resources: [],
    };

    // When: the benchmark normalizes the timing entry
    const result = normalizeBrowserTiming(timing);

    // Then: unavailable evidence is null while a measured zero stays zero
    assert.equal(result.navigation.response_start_ms, null);
    assert.equal(result.navigation.transfer_size_bytes, 0);
  });

  it('rejects a report when browser navigation timing is missing', () => {
    // Given: the browser did not provide a navigation entry
    const timing = {
      origin: 'https://staging.example.test',
      navigation: undefined,
      resources: [],
    };

    // When: the benchmark normalizes browser timing
    // Then: it rejects misleading zero-filled navigation evidence
    assert.throws(
      () => normalizeBrowserTiming(timing),
      /Browser navigation timing is unavailable/,
    );
  });

  it('rejects capture when the browser omits navigation timing', async () => {
    // Given: the browser adapter returns no navigation entry
    const page = {
      evaluate: async () => ({
        origin: 'https://staging.example.test',
        navigation: null,
        resources: [],
      }),
    };

    // When: the benchmark captures browser timing
    // Then: the adapter cannot hide the missing evidence
    await assert.rejects(
      () => captureBrowserTiming(page),
      /Browser navigation timing is unavailable/,
    );
  });

  it('keeps stable timing fields and removes private URL values', () => {
    // Given: browser entries include same-origin resources and private query values
    const timing = {
      origin: 'https://staging.example.test',
      navigation: {
        type: 'navigate',
        responseStart: 12.4,
        responseEnd: 20.6,
        domContentLoadedEventEnd: 31.2,
        loadEventEnd: 40.8,
        transferSize: 900,
        encodedBodySize: 600,
        decodedBodySize: 1200,
        deliveryType: '',
      },
      resources: [
        {
          name: 'https://staging.example.test/static/stage_4_review.js?v=private',
          initiatorType: 'script',
          startTime: 1.2,
          responseStart: 4.2,
          responseEnd: 8.8,
          transferSize: 0,
          encodedBodySize: 700,
          decodedBodySize: 1400,
          deliveryType: 'cache',
        },
        {
          name: 'https://staging.example.test/stage-4/non-conformant/private-file-id',
          initiatorType: 'fetch',
          startTime: 19,
          responseStart: 20,
          responseEnd: 21,
          transferSize: 30,
          encodedBodySize: 20,
          decodedBodySize: 40,
        },
        {
          name: 'https://cognito.example.test/private.js',
          initiatorType: 'script',
          startTime: 1,
          responseStart: 2,
          responseEnd: 3,
          transferSize: 10,
          encodedBodySize: 10,
          decodedBodySize: 10,
        },
      ],
    };

    // When: the benchmark normalizes browser timing for its JSON report
    const result = normalizeBrowserTiming(timing);

    // Then: it keeps useful numeric evidence but no query, file, or external URL values
    assert.deepEqual(result, {
      navigation: {
        type: 'navigate',
        response_start_ms: 12.4,
        response_end_ms: 20.6,
        dom_content_loaded_ms: 31.2,
        load_event_end_ms: 40.8,
        transfer_size_bytes: 900,
        encoded_body_size_bytes: 600,
        decoded_body_size_bytes: 1200,
        delivery_type: null,
      },
      resources: [
        {
          path: '/static/stage_4_review.js',
          initiator_type: 'script',
          start_ms: 1.2,
          response_start_ms: 4.2,
          response_end_ms: 8.8,
          transfer_size_bytes: 0,
          encoded_body_size_bytes: 700,
          decoded_body_size_bytes: 1400,
          delivery_type: 'cache',
        },
        {
          path: '/stage-4/non-conformant/:file_id',
          initiator_type: 'fetch',
          start_ms: 19,
          response_start_ms: 20,
          response_end_ms: 21,
          transfer_size_bytes: 30,
          encoded_body_size_bytes: 20,
          decoded_body_size_bytes: 40,
          delivery_type: null,
        },
      ],
    });
  });
});
