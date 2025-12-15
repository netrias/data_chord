// """Translate harmonization manifest metadata into dashboard datasets."""

// "why: confidence buckets use a single color for visual simplicity."
const CONFIDENCE_COLOR_VAR = '--azure-500';

const CONFIDENCE_BUCKETS = [
  { id: 'high', label: 'High', min: 0.8 },
  { id: 'medium', label: 'Medium', min: 0.45 },
  { id: 'low', label: 'Low', min: 0 },
];

const _resolveColor = (colorVar) => {
  // "why: read CSS custom property value at runtime for theme consistency."
  return getComputedStyle(document.documentElement).getPropertyValue(colorVar).trim() || colorVar;
};

const _toSafeNumber = (value, fallback = 0) => {
  // "why: convert stringified metrics into predictable numeric inputs."
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

const _safeArray = (candidate) => {
  // "why: extract row arrays from various manifest response shapes."
  if (Array.isArray(candidate)) {
    return candidate;
  }
  if (candidate && Array.isArray(candidate.preview_rows)) {
    return candidate.preview_rows;
  }
  if (candidate && Array.isArray(candidate.rows)) {
    return candidate.rows;
  }
  if (candidate && Array.isArray(candidate.entries)) {
    return candidate.entries;
  }
  if (candidate && Array.isArray(candidate.preview)) {
    return candidate.preview;
  }
  return [];
};

const _looksLikeManifestRow = (entry) => {
  if (!entry || typeof entry !== 'object') {
    return false;
  }
  const keys = Object.keys(entry);
  return keys.includes('to_harmonize') || keys.includes('top_harmonization') || keys.includes('confidence_score');
};

const _normalizeValue = (value) => (value ?? '').toString().trim().toLowerCase();

const _isChangedRow = (row) => {
  const original = _normalizeValue(row?.to_harmonize);
  const harmonized = _normalizeValue(row?.top_harmonization);
  if (!harmonized) {
    return false;
  }
  return original !== harmonized;
};

const _getRowCount = (row) => {
  // "why: count actual CSV row occurrences, not just unique terms."
  const indices = row?.row_indices;
  if (Array.isArray(indices) && indices.length > 0) {
    return indices.length;
  }
  return 1;
};

const _formatColumnLabel = (columnName) => {
  // "why: convert snake_case column names to readable labels."
  if (!columnName) {
    return 'Unknown';
  }
  return columnName
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase());
};

const _getConfidenceBucket = (score) => {
  const safeScore = _toSafeNumber(score, -1);
  return (
    CONFIDENCE_BUCKETS.find((bucket) => safeScore >= bucket.min) ??
    CONFIDENCE_BUCKETS[CONFIDENCE_BUCKETS.length - 1]
  );
};

const _buildColumnBreakdown = (rows) => {
  // "why: aggregate per-column statistics using row_indices for actual row counts."
  const columnMap = new Map();

  rows.forEach((row) => {
    const columnName = row?.column_name ?? 'unknown';
    if (!columnMap.has(columnName)) {
      columnMap.set(columnName, {
        columnName,
        label: _formatColumnLabel(columnName),
        rows: [],
      });
    }
    columnMap.get(columnName).rows.push(row);
  });

  const columns = Array.from(columnMap.values()).map((column) => {
    let totalRows = 0;
    let changedRows = 0;
    let uniqueTermsChanged = 0;
    const confidenceTermCounts = { high: 0, medium: 0, low: 0 };

    column.rows.forEach((row) => {
      const rowCount = _getRowCount(row);
      const isChanged = _isChangedRow(row);
      const bucket = _getConfidenceBucket(row?.confidence_score);

      totalRows += rowCount;
      if (isChanged) {
        changedRows += rowCount;
        uniqueTermsChanged += 1;
        // "why: count unique terms (not row occurrences) per confidence bucket."
        confidenceTermCounts[bucket.id] += 1;
      }
    });

    const uniqueTerms = column.rows.length;
    const unchangedRows = totalRows - changedRows;

    return {
      columnName: column.columnName,
      label: column.label,
      totalRows,
      changedRows,
      unchangedRows,
      uniqueTerms,
      uniqueTermsChanged,
      confidenceBuckets: CONFIDENCE_BUCKETS.map((bucket) => ({
        id: bucket.id,
        label: bucket.label,
        termCount: confidenceTermCounts[bucket.id],
        color: _resolveColor(CONFIDENCE_COLOR_VAR),
      })),
    };
  });

  // "why: sort columns with changes first (by row count), then zero-change columns at bottom."
  return columns.sort((a, b) => {
    if (a.changedRows === 0 && b.changedRows > 0) return 1;
    if (a.changedRows > 0 && b.changedRows === 0) return -1;
    return b.totalRows - a.totalRows;
  });
};

const _buildDatasetFromManifest = (manifest) => {
  const rows = _safeArray(manifest).filter(_looksLikeManifestRow);
  if (!rows.length) {
    return null;
  }

  const columnBreakdown = _buildColumnBreakdown(rows);

  return {
    columnBreakdown,
  };
};

export const buildDashboardDataset = ({ job, payload }) => {
  // "why: derive widget-friendly metrics from the manifest; returns null if no manifest data."
  const manifest =
    job?.manifest_summary ??
    job?.manifest_preview ??
    job?.manifest ??
    payload?.manifest ??
    null;
  return _buildDatasetFromManifest(manifest);
};

export default buildDashboardDataset;
