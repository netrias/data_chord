// """Translate harmonization manifest metadata into dashboard datasets."""

const MANIFEST_PILL = 'Manifest snapshot';
const ESTIMATE_PILL = 'Estimated snapshot';

const CONFIDENCE_BUCKETS = [
  { id: 'high', label: 'High confidence', color: '#16a34a', min: 0.8 },
  { id: 'medium', label: 'Medium confidence', color: '#f97316', min: 0.45 },
  { id: 'low', label: 'Low confidence', color: '#dc2626', min: 0 },
];

const toSafeNumber = (value, fallback = 0) => {
  // "why: convert stringified metrics into predictable numeric inputs."
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

const safeArray = (candidate) => {
  if (Array.isArray(candidate)) {
    return candidate;
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

const looksLikeManifestRow = (entry) => {
  if (!entry || typeof entry !== 'object') {
    return false;
  }
  const keys = Object.keys(entry);
  return keys.includes('to_harmonize') || keys.includes('top_harmonization') || keys.includes('confidence_score');
};

const normalizeValue = (value) => (value ?? '').toString().trim().toLowerCase();

const isChangedRow = (row) => {
  const original = normalizeValue(row?.to_harmonize);
  const harmonized = normalizeValue(row?.top_harmonization);
  if (!harmonized) {
    return false;
  }
  return original !== harmonized;
};

const selectChangeLabel = (row) => {
  // "why: map manifest change types into user-friendly labels."
  const raw = normalizeValue(row?.change_type ?? row?.harmonization_type ?? row?.action);
  if (raw.includes('merge')) {
    return 'Attribute merges';
  }
  if (raw.includes('conflict') || raw.includes('override')) {
    return 'Conflict resolutions';
  }
  if (raw.includes('rename') || raw.includes('map') || raw.includes('standardize')) {
    return 'Entity renames';
  }
  if (Array.isArray(row?.top_harmonizations) && row.top_harmonizations.length > 1) {
    return 'Attribute merges';
  }
  if (toSafeNumber(row?.confidence_score, 0) < 0.45) {
    return 'Conflict resolutions';
  }
  return 'Entity renames';
};

const buildChangeTypeCounts = (rows) => {
  const counts = new Map();
  rows.forEach((row) => {
    if (!isChangedRow(row)) {
      return;
    }
    const label = selectChangeLabel(row);
    counts.set(label, (counts.get(label) ?? 0) + 1);
  });
  return Array.from(counts.entries()).map(([label, count]) => ({ label, count }));
};

const bucketConfidence = (rows) => {
  const counts = CONFIDENCE_BUCKETS.reduce((acc, bucket) => ({ ...acc, [bucket.id]: 0 }), {});
  rows.forEach((row) => {
    const score = toSafeNumber(row?.confidence_score, -1);
    const bucket =
      CONFIDENCE_BUCKETS.find((candidate) => score >= candidate.min) ??
      CONFIDENCE_BUCKETS[CONFIDENCE_BUCKETS.length - 1];
    counts[bucket.id] += 1;
  });
  return {
    buckets: CONFIDENCE_BUCKETS.map((bucket) => ({
      id: bucket.id,
      label: bucket.label,
      count: counts[bucket.id],
      color: bucket.color,
    })),
    isMocked: false,
    note: 'Confidence scores bucketed from the harmonization manifest.',
  };
};

const mockConfidenceBuckets = (fallbackTotal) => {
  // "why: keep the widget visible until real manifest data is wired up."
  const base = Math.max(fallbackTotal, 120);
  const high = Math.round(base * 0.68);
  const medium = Math.round(base * 0.22);
  const low = Math.max(base - high - medium, 0);
  return {
    buckets: [
      { id: 'high', label: 'High confidence', count: high, color: '#16a34a' },
      { id: 'medium', label: 'Medium confidence', count: medium, color: '#f97316' },
      { id: 'low', label: 'Low confidence', count: low, color: '#dc2626' },
    ],
    isMocked: true,
    note: 'Confidence scores are mocked until the parquet manifest is exposed.',
  };
};

const fallbackChangeTypes = (total) => {
  const safeTotal = Math.max(total, 0);
  const renameCount = Math.round(safeTotal * 0.4);
  const mergeCount = Math.round(safeTotal * 0.32);
  const conflictCount = Math.max(safeTotal - renameCount - mergeCount, 0);
  return [
    { label: 'Entity renames', count: renameCount },
    { label: 'Attribute merges', count: mergeCount },
    { label: 'Conflict resolutions', count: conflictCount },
  ];
};

const buildDatasetFromManifest = (manifest, context) => {
  const rows = safeArray(manifest).filter(looksLikeManifestRow);
  if (!rows.length) {
    return null;
  }
  const totalItems = rows.length;
  const changedItems = rows.reduce((total, row) => total + (isChangedRow(row) ? 1 : 0), 0);
  const changeTypes = buildChangeTypeCounts(rows);
  const confidence = bucketConfidence(rows);
  return {
    totalItems,
    totalItemsPill: MANIFEST_PILL,
    totalItemsNote: context?.fileName
      ? `Counts for ${context.fileName} pulled directly from the manifest.`
      : 'Counts pulled directly from the harmonization manifest.',
    changedItems,
    unchangedItems: Math.max(totalItems - changedItems, 0),
    changeSplitNote: 'Changed counts compare manifest inputs with harmonized values.',
    changeTypes,
    changeTypesNote: 'Manifest change types aggregated per harmonized cell.',
    confidenceBuckets: confidence.buckets,
    isConfidenceMocked: confidence.isMocked,
    confidenceNote: confidence.note,
  };
};

const buildDatasetFromMetrics = (metrics = {}, context) => {
  const totalItems = Math.max(
    0,
    toSafeNumber(metrics.total_items ?? metrics.totalItems ?? context?.totalRows ?? 0),
  );
  const changedFallback = Math.round(totalItems * 0.65);
  const changedItems = Math.min(
    totalItems,
    toSafeNumber(metrics.changed_items ?? metrics.changedItems, changedFallback),
  );
  const changeTypes =
    (metrics.change_types ?? metrics.changeTypes)?.map((entry, index) => ({
      label: entry.label ?? entry.id ?? `Type ${index + 1}`,
      count: toSafeNumber(entry.count, 0),
    })) ?? fallbackChangeTypes(changedItems);
  const confidence = mockConfidenceBuckets(Math.max(changedItems, totalItems));
  return {
    totalItems,
    totalItemsPill: ESTIMATE_PILL,
    totalItemsNote: context?.fileName
      ? `Counts estimated from job payload for ${context.fileName}.`
      : 'Counts estimated from job payload while manifest access is pending.',
    changedItems,
    unchangedItems: Math.max(totalItems - changedItems, 0),
    changeSplitNote: 'Changed counts are estimated until the parquet manifest arrives.',
    changeTypes,
    changeTypesNote: 'Breakdown relies on heuristics until manifest telemetry is wired up.',
    confidenceBuckets: confidence.buckets,
    isConfidenceMocked: confidence.isMocked,
    confidenceNote: confidence.note,
  };
};

export const buildDashboardDataset = ({ job, payload }) => {
  // "why: derive widget-friendly metrics from either manifest or fallback telemetry."
  const manifest =
    job?.manifest_summary ??
    job?.manifest_preview ??
    job?.manifest ??
    payload?.manifest ??
    null;
  const context = payload?.context ?? {};
  const manifestDataset = buildDatasetFromManifest(manifest, context);
  if (manifestDataset) {
    return manifestDataset;
  }
  return buildDatasetFromMetrics(job?.metrics ?? {}, context);
};

export default buildDashboardDataset;
