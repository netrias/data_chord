/** Contract tests for private Playwright authentication state. */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';

import { resolvePrivateStorageState } from '../e2e/performance-auth.mjs';

describe('remote performance authentication', () => {
  it('requires an explicit private storage-state path', () => {
    // Given: no private authentication file is configured
    const environment = {};

    // When: the remote benchmark resolves its authentication state
    // Then: it explains how to provide the required private file
    assert.throws(
      () => resolvePrivateStorageState(environment),
      /PERF_STORAGE_STATE_PATH is required/,
    );
  });

  it('names a configured storage-state file that does not exist', () => {
    // Given: the configured private authentication file is missing
    const environment = { PERF_STORAGE_STATE_PATH: '.auth/missing.json' };

    // When: the remote benchmark validates the file
    // Then: the failure names the resolved file without exposing file contents
    assert.throws(
      () => resolvePrivateStorageState(environment, { fileExists: () => false }),
      new RegExp(`Authentication state file does not exist: ${path.resolve('.auth/missing.json')}`),
    );
  });

  it('rejects a storage-state file without the Playwright state shape', () => {
    // Given: a file exists but does not contain Playwright authentication state
    const environment = { PERF_STORAGE_STATE_PATH: '.auth/invalid.json' };

    // When: the remote benchmark validates the private file
    // Then: it reports the invalid shape without printing the private content
    assert.throws(
      () => resolvePrivateStorageState(environment, {
        fileExists: () => true,
        readFile: () => '{"secret":"do-not-print"}',
      }),
      /Authentication state must contain cookies and origins arrays/,
    );
  });

  it('returns the resolved path for valid Playwright state', () => {
    // Given: the configured file contains Playwright cookies and origins arrays
    const environment = { PERF_STORAGE_STATE_PATH: '.auth/bdf-staging.json' };

    // When: the remote benchmark validates the private file
    const result = resolvePrivateStorageState(environment, {
      fileExists: () => true,
      readFile: () => '{"cookies":[],"origins":[]}',
    });

    // Then: Playwright receives one absolute file path
    assert.equal(result, path.resolve('.auth/bdf-staging.json'));
  });
});
