import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import {
  fingerprintFile, verificationMatches, UploadVerificationError,
} from '../../src/stage_1_upload/static/upload-integrity.js';

test('fingerprinting preserves binary file bytes including BOM and line endings', async () => {
  // Given a selected file whose bytes must not be normalized as text.
  const bytes = Buffer.from([0xef, 0xbb, 0xbf, 0x00, 0xff, 0x0d, 0x0a]);
  const file = new File([bytes], 'binary.xlsx');
  // When the browser's native SHA-256 API computes its fingerprint.
  const actual = await fingerprintFile(file);
  // Then it matches an independent SHA-256 implementation and the original byte count.
  assert.deepEqual(actual, { sha256: createHash('sha256').update(bytes).digest('hex'), size_bytes: bytes.length });
});

test('an incomplete file read cannot produce a fingerprint', async () => {
  // Given a file reader that returns fewer bytes than the selected file contains.
  const file = new File(['abc'], 'source.csv');
  file.arrayBuffer = async () => new ArrayBuffer(2);
  // When the browser prepares the file.
  const result = fingerprintFile(file);
  // Then it fails with fixed, safe retry guidance.
  await assert.rejects(result, new UploadVerificationError('We could not prepare this file for upload. Please select it again.'));
});

test('a file read failure does not expose the underlying exception', async () => {
  // Given a selected file that cannot be read.
  const file = new File(['abc'], 'source.csv');
  file.arrayBuffer = async () => { throw new Error('private operating-system detail'); };
  // When fingerprinting starts.
  const result = fingerprintFile(file);
  // Then only the fixed preparation message is returned.
  await assert.rejects(result, new UploadVerificationError('We could not prepare this file for upload. Please select it again.'));
});

test('missing Web Crypto fails instead of preparing an unverified upload', async () => {
  // Given an unsupported or insecure browser context.
  const original = Object.getOwnPropertyDescriptor(globalThis, 'crypto');
  Object.defineProperty(globalThis, 'crypto', { configurable: true, value: undefined });
  try {
    // When the browser tries to prepare a file.
    const result = fingerprintFile(new File(['abc'], 'source.csv'));
    // Then submission cannot proceed without verification support.
    await assert.rejects(result, new UploadVerificationError('File verification requires a supported browser using HTTPS or localhost.'));
  } finally {
    Object.defineProperty(globalThis, 'crypto', original);
  }
});

test('only a receipt matching both fingerprint fields is accepted', () => {
  // Given the browser's fingerprint and malformed or mismatching server receipts.
  const expected = { sha256: 'a'.repeat(64), size_bytes: 3 };
  const receipts = [null, undefined, {}, { ...expected, sha256: 'b'.repeat(64) }, { ...expected, size_bytes: 2 }, { ...expected, size_bytes: '3' }];
  // When receipts are compared with the selected file.
  const results = receipts.map((receipt) => verificationMatches(receipt, expected));
  // Then every incomplete/mismatching receipt fails and the exact receipt succeeds.
  assert.deepEqual(results, [false, false, false, false, false, false]);
  assert.equal(verificationMatches(expected, expected), true);
});
