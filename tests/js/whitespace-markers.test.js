/**
 * Tests for whitespace marker formatting in the "was:" display.
 *
 * Validates that leading/trailing whitespace is rendered as visible
 * middle-dot markers while preserving XSS safety.
 */

import { describe, it } from 'node:test';
import assert from 'node:assert';
import { pathToFileURL } from 'node:url';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const utilsPath = join(__dirname, '../../src/stage_4_review_results/static/shared_review_utils.js');

const { formatWhitespaceMarkers, escapeHtml } = await import(pathToFileURL(utilsPath).href);

const MARKER = '<span class="ws-marker">\u00B7</span>';

describe('formatWhitespaceMarkers', () => {

  it('returns escaped string when no leading/trailing whitespace', () => {
    // Given: string with no edge whitespace
    const str = 'Lung Cancer';
    assert.strictEqual(str, str.trim()); // negative: no whitespace to strip

    // When/Then: returns same as escapeHtml
    assert.strictEqual(formatWhitespaceMarkers(str), escapeHtml(str));
  });

  it('shows leading spaces as dot markers', () => {
    // Given: string with 2 leading spaces
    const str = '  Lung Cancer';
    assert.notStrictEqual(str, str.trimStart()); // negative: has leading whitespace

    // When/Then: leading spaces become dot markers
    assert.strictEqual(
      formatWhitespaceMarkers(str),
      MARKER + MARKER + escapeHtml('Lung Cancer'),
    );
  });

  it('shows trailing spaces as dot markers', () => {
    // Given: string with 2 trailing spaces
    const str = 'Lung Cancer  ';
    assert.notStrictEqual(str, str.trimEnd()); // negative: has trailing whitespace

    // When/Then: trailing spaces become dot markers
    assert.strictEqual(
      formatWhitespaceMarkers(str),
      escapeHtml('Lung Cancer') + MARKER + MARKER,
    );
  });

  it('shows both leading and trailing spaces as dot markers', () => {
    // Given: string with 1 leading and 1 trailing space
    const str = ' Lung Cancer ';
    assert.notStrictEqual(str, str.trim()); // negative: has edge whitespace

    // When/Then: both edges get markers
    assert.strictEqual(
      formatWhitespaceMarkers(str),
      MARKER + escapeHtml('Lung Cancer') + MARKER,
    );
  });

  it('preserves internal spaces unchanged', () => {
    // Given: string with double internal space and edge whitespace
    const str = ' Lung  Cancer ';

    // When/Then: internal double space preserved, only edges get markers
    assert.strictEqual(
      formatWhitespaceMarkers(str),
      MARKER + escapeHtml('Lung  Cancer') + MARKER,
    );
  });

  it('escapes HTML special characters in middle content', () => {
    // Given: XSS attempt with leading/trailing whitespace
    const str = '  <script>alert(1)</script>  ';
    assert.notStrictEqual(str, str.trim()); // negative: has edge whitespace

    // When: formatted
    const result = formatWhitespaceMarkers(str);

    // Then: middle content is escaped, no raw script tags
    assert.ok(!result.includes('<script>'), 'Should not contain raw <script> tag');
    assert.ok(result.includes('&lt;script&gt;'), 'Should contain escaped script tag');
    // Markers present on both sides
    assert.ok(result.startsWith(MARKER + MARKER), 'Should start with 2 markers');
    assert.ok(result.endsWith(MARKER + MARKER), 'Should end with 2 markers');
  });

  it('handles all-whitespace string', () => {
    // Given: string of only spaces
    const str = '   ';

    // When/Then: all spaces become markers, no middle content
    assert.strictEqual(
      formatWhitespaceMarkers(str),
      MARKER + MARKER + MARKER,
    );
  });

  it('handles non-string input gracefully', () => {
    // Given: non-string input
    // When/Then: converts and escapes
    assert.strictEqual(formatWhitespaceMarkers(42), escapeHtml('42'));
  });
});
