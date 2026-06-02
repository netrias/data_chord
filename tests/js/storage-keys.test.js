import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { isValidFileId } from '../../src/shared/static/storage-keys.js';

describe('storage key helpers', () => {
  it('accepts the canonical file id shape', () => {
    assert.equal(isValidFileId('a'.repeat(32)), true);
  });

  it('rejects malformed file ids', () => {
    assert.equal(isValidFileId(''), false);
    assert.equal(isValidFileId('abc123'), false);
    assert.equal(isValidFileId('A'.repeat(32)), false);
    assert.equal(isValidFileId('g'.repeat(32)), false);
    assert.equal(isValidFileId('../' + 'a'.repeat(29)), false);
    assert.equal(isValidFileId('a'.repeat(31)), false);
    assert.equal(isValidFileId('a'.repeat(33)), false);
  });
});
