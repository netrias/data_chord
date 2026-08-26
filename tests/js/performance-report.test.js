/** Contract tests for stable performance benchmark reports. */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import {
  buildPerformanceReport,
  positiveIntegerFromEnv,
  summarizeDurations,
} from '../e2e/performance-report.mjs';

const makeRun = (id, kind, stage4Ms, stage5Ms) => ({
  id,
  kind,
  warning_shown: false,
  mapping_count: 24,
  stage4: { button_to_usable_ms: stage4Ms },
  stage5: { button_to_usable_ms: stage5Ms },
});

describe('performance benchmark report', () => {
  it('summarizes ordered and unordered durations with nearest-rank p95', () => {
    // Given: ten user-operation durations in an arbitrary order
    const durations = [90, 10, 50, 20, 100, 60, 30, 80, 40, 70];

    // When: the benchmark calculates its stable summary
    const summary = summarizeDurations(durations);

    // Then: the report contains exact count, range, median, and p95 values
    assert.deepEqual(summary, {
      count: 10,
      min: 10,
      median: 55,
      p95: 100,
      max: 100,
    });
  });

  it('separates cold and warm user-operation summaries', () => {
    // Given: cold and warm runs for one completed workflow
    const runs = [
      makeRun('cold-1', 'cold', 3000, 5000),
      makeRun('cold-2', 'cold', 2000, 4000),
      makeRun('warm-1', 'warm', 1000, 2000),
      makeRun('warm-2', 'warm', 1200, 2200),
    ];

    // When: the benchmark builds its JSON contract
    const report = buildPerformanceReport({
      environment: {
        target: 'bdf',
        stage: 'staging',
        base_url: 'https://staging.example.test',
        commit: 'abc123',
      },
      dataset: { rows: 3, columns: 8, source: 'sample.csv' },
      runs,
      setup: { harmonize_to_review_ready_ms: 9000 },
      generatedAt: '2026-08-26T12:00:00.000Z',
    });

    // Then: raw evidence and separate cold and warm summaries remain available
    assert.deepEqual(report, {
      schema_version: 1,
      generated_at: '2026-08-26T12:00:00.000Z',
      environment: {
        target: 'bdf',
        stage: 'staging',
        base_url: 'https://staging.example.test',
        commit: 'abc123',
      },
      dataset: { rows: 3, columns: 8, source: 'sample.csv' },
      runs,
      setup: { harmonize_to_review_ready_ms: 9000 },
      summary: {
        cold: {
          stage4_button_to_usable_ms: {
            count: 2, min: 2000, median: 2500, p95: 3000, max: 3000,
          },
          stage5_button_to_usable_ms: {
            count: 2, min: 4000, median: 4500, p95: 5000, max: 5000,
          },
        },
        warm: {
          stage4_button_to_usable_ms: {
            count: 2, min: 1000, median: 1100, p95: 1200, max: 1200,
          },
          stage5_button_to_usable_ms: {
            count: 2, min: 2000, median: 2100, p95: 2200, max: 2200,
          },
        },
      },
    });
  });

  it('uses the fallback for missing or invalid positive integer settings', () => {
    // Given: missing, zero, negative, fractional, and valid run-count settings
    const environment = {
      ZERO: '0',
      NEGATIVE: '-2',
      FRACTIONAL: '2.5',
      VALID: '7',
    };

    // When: the benchmark reads each setting
    // Then: only the valid positive integer replaces the documented fallback
    assert.equal(positiveIntegerFromEnv(environment, 'MISSING', 10), 10);
    assert.equal(positiveIntegerFromEnv(environment, 'ZERO', 10), 10);
    assert.equal(positiveIntegerFromEnv(environment, 'NEGATIVE', 10), 10);
    assert.equal(positiveIntegerFromEnv(environment, 'FRACTIONAL', 10), 10);
    assert.equal(positiveIntegerFromEnv(environment, 'VALID', 10), 7);
  });
});
