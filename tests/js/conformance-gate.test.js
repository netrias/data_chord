/** Boundary tests for the Stage 4 conformance navigation gate. */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { fetchConformanceResult } from '../../src/stage_4_review_results/static/conformance-gate.js';

describe('Stage 4 conformance gate', () => {
  it('returns a known successful result', async () => {
    const result = await fetchConformanceResult('abc', async () => ({
      ok: true,
      json: async () => ({
        count: 1,
        items: [{ column: 'diagnosis', value: 'bad', original: 'source' }],
      }),
    }));

    assert.deepEqual(result, {
      count: 1,
      items: [{ column: 'diagnosis', value: 'bad', original: 'source' }],
    });
  });

  it('blocks on an HTTP failure', async () => {
    await assert.rejects(
      fetchConformanceResult('abc', async () => ({ ok: false, status: 409 })),
      /Conformance check failed: 409/,
    );
  });

  it('blocks on a malformed successful response', async () => {
    await assert.rejects(
      fetchConformanceResult('abc', async () => ({ ok: true, json: async () => ({ count: null }) })),
      /invalid response/,
    );
  });

  it('blocks on a transport failure', async () => {
    await assert.rejects(
      fetchConformanceResult('abc', async () => { throw new Error('offline'); }),
      /offline/,
    );
  });
});
