const _nearestRank = (sortedValues, percentile) => {
  const index = Math.max(Math.ceil(percentile * sortedValues.length) - 1, 0);
  return sortedValues[index];
};

export const positiveIntegerFromEnv = (environment, name, fallback) => {
  const value = Number(environment[name]);
  return Number.isInteger(value) && value > 0 ? value : fallback;
};

export const summarizeDurations = (durations) => {
  const sorted = durations.map(Number).sort((left, right) => left - right);
  if (sorted.length === 0) {
    return { count: 0, min: null, median: null, p95: null, max: null };
  }

  const middle = Math.floor(sorted.length / 2);
  const median = sorted.length % 2 === 0
    ? (sorted[middle - 1] + sorted[middle]) / 2
    : sorted[middle];
  return {
    count: sorted.length,
    min: sorted[0],
    median,
    p95: _nearestRank(sorted, 0.95),
    max: sorted.at(-1),
  };
};

const _summarizeRuns = (runs) => ({
  stage4_button_to_usable_ms: summarizeDurations(
    runs.map((run) => run.stage4.button_to_usable_ms),
  ),
  stage5_button_to_usable_ms: summarizeDurations(
    runs.map((run) => run.stage5.button_to_usable_ms),
  ),
});

export const buildPerformanceReport = ({
  environment,
  dataset,
  runs,
  setup = undefined,
  generatedAt = new Date().toISOString(),
}) => {
  const report = {
    schema_version: 3,
    generated_at: generatedAt,
    environment,
    dataset,
    runs,
    summary: {
      cold: _summarizeRuns(runs.filter((run) => run.kind === 'cold')),
      warm: _summarizeRuns(runs.filter((run) => run.kind === 'warm')),
    },
  };
  if (setup !== undefined) report.setup = setup;
  return report;
};
