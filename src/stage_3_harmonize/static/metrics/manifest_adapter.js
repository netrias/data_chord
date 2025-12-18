/**
 * Translate server-computed manifest summary into dashboard datasets.
 * The server now provides pre-computed column breakdowns, so this adapter
 * simply normalizes the shape for frontend consumption.
 */

const _toSafeNumber = (value, fallback = 0) => {
  /* "why: ensure numeric values for defensive rendering." */
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

const _normalizeConfidenceBuckets = (buckets) => {
  /* "why: transform server bucket array to frontend shape." */
  return (buckets ?? []).map((bucket) => ({
    id: bucket.id ?? '',
    label: bucket.label ?? '',
    termCount: _toSafeNumber(bucket.term_count),
  }));
};

const _normalizeColumnBreakdown = (serverColumn) => {
  /* "why: map snake_case server fields to camelCase frontend convention." */
  return {
    columnName: serverColumn.column_name ?? '',
    label: serverColumn.label ?? '',
    totalRows: _toSafeNumber(serverColumn.total_rows),
    changedRows: _toSafeNumber(serverColumn.changed_rows),
    unchangedRows: _toSafeNumber(serverColumn.unchanged_rows),
    uniqueTerms: _toSafeNumber(serverColumn.unique_terms),
    uniqueTermsChanged: _toSafeNumber(serverColumn.unique_terms_changed),
    confidenceBuckets: _normalizeConfidenceBuckets(serverColumn.confidence_buckets),
  };
};

const _extractManifestSummary = (job, payload) => {
  /* "why: locate manifest_summary from various response shapes." */
  return job?.manifest_summary ?? payload?.manifest_summary ?? null;
};

export const buildDashboardDataset = ({ job, payload }) => {
  /* "why: transform server-computed summaries into widget-ready format." */
  const summary = _extractManifestSummary(job, payload);
  if (!summary) {
    return null;
  }

  const serverBreakdowns = summary.column_breakdowns ?? [];
  if (!serverBreakdowns.length) {
    return null;
  }

  const columnBreakdown = serverBreakdowns.map(_normalizeColumnBreakdown);

  return {
    columnBreakdown,
  };
};

export default buildDashboardDataset;
