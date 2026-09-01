import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  prepareSearchCandidate,
  scoreSearch,
} from '../../src/shared/static/fuzzy-search.js';


const _score = (query, fields) => scoreSearch(query, prepareSearchCandidate(fields));


describe('fuzzy search', () => {
  it('ranks exact phrases before prefixes and spelling mistakes', () => {
    // Given: candidates match the same search with different quality.
    const exact = prepareSearchCandidate(['Primary Diagnosis']);
    const prefix = prepareSearchCandidate(['Primary Diagnosis Category']);
    const typo = prepareSearchCandidate(['Primary Diagnozis']);

    // When: the complete target name is searched.
    const scores = [
      scoreSearch('primary diagnosis', exact),
      scoreSearch('primary diagnosis', prefix),
      scoreSearch('primary diagnosis', typo),
    ];

    // Then: every candidate matches in the expected quality order.
    assert.ok(scores.every((score) => score !== null));
    assert.ok(scores[0] < scores[1]);
    assert.ok(scores[1] < scores[2]);
  });

  it('matches one insertion, deletion, replacement, or adjacent letter swap', () => {
    // Given: one target contains a correctly spelled diagnosis field.
    const candidate = prepareSearchCandidate(['Primary Diagnosis']);

    // When: each query has one common spelling error.
    const scores = [
      scoreSearch('diagonsis', candidate),
      scoreSearch('diagnossis', candidate),
      scoreSearch('diagnosi', candidate),
      scoreSearch('diagnozis', candidate),
    ];

    // Then: every one-edit query still finds the target.
    assert.ok(scores.every((score) => score !== null));
  });

  it('requires every query token across all searchable fields', () => {
    // Given: the label and description together contain the complete concept.
    const candidate = prepareSearchCandidate([
      'Primary Diagnosis',
      'The standard records a cancer site',
    ]);

    // When: one query uses two present tokens and one uses a missing token.
    const completeScore = scoreSearch('primary site', candidate);
    const missingScore = scoreSearch('primary stage', candidate);

    // Then: only the query whose complete meaning is present matches.
    assert.notStrictEqual(completeScore, null);
    assert.strictEqual(missingScore, null);
  });

  it('does not apply typo matching to short or distant text', () => {
    // Given: a permissible value has one short and one long word.
    const candidate = prepareSearchCandidate(['Lung Cancer']);

    // When: the short word and long word are too different.
    const shortScore = scoreSearch('lng', candidate);
    const distantScore = scoreSearch('caxxyz', candidate);

    // Then: neither weak query produces a match.
    assert.strictEqual(shortScore, null);
    assert.strictEqual(distantScore, null);
  });

  it('keeps meaningful punctuation in the best-match tier', () => {
    // Given: two scientific values differ only by a meaningful symbol.
    const positive = prepareSearchCandidate(['HER2+']);
    const negative = prepareSearchCandidate(['HER2-']);

    // When: the positive value is searched.
    const positiveScore = scoreSearch('HER2+', positive);
    const negativeScore = scoreSearch('HER2+', negative);

    // Then: the literal punctuation match ranks first.
    assert.notStrictEqual(positiveScore, null);
    assert.notStrictEqual(negativeScore, null);
    assert.ok(positiveScore < negativeScore);
  });

  it('does not turn punctuation-only text into a match-all query', () => {
    // Given: only one candidate contains the searched symbol.
    const positive = prepareSearchCandidate(['HER2+']);
    const plain = prepareSearchCandidate(['HER2']);

    // When: the symbol is searched by itself.
    const positiveScore = scoreSearch('+', positive);
    const plainScore = scoreSearch('+', plain);

    // Then: only the literal symbol match remains.
    assert.notStrictEqual(positiveScore, null);
    assert.strictEqual(plainScore, null);
  });

  it('folds case, accents, punctuation separators, and repeated spaces', () => {
    // Given: target text contains accents, a hyphen, and mixed case.
    const candidate = prepareSearchCandidate(['Caf\u00e9 Tumor-Site']);

    // When: plain text with repeated spaces is searched.
    const score = scoreSearch('  cafe   tumor site ', candidate);

    // Then: the normalized text matches.
    assert.notStrictEqual(score, null);
  });

  it('matches every candidate for an empty query', () => {
    // Given: a prepared target exists.
    const candidate = prepareSearchCandidate(['Diagnosis']);

    // When: the query is empty.
    const score = scoreSearch('', candidate);

    // Then: the candidate remains visible with a neutral score.
    assert.strictEqual(score, 0);
  });

  it('searches the CDE key even when a display label is present', () => {
    // Given: the key is separate from the display label and description.
    const fields = ['Primary Diagnosis', 'primary_diagnosis', 'Patient diagnosis standard'];

    // When: the underscored key is searched.
    const score = _score('primary_diag', fields);

    // Then: the candidate matches through its key.
    assert.notStrictEqual(score, null);
  });
});
