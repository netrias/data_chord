const config = window.stageFourConfig ?? {};
const stageThreePayloadKey = config.stageThreePayloadKey ?? 'stage3HarmonizePayload';
const stageThreeJobKey = config.stageThreeJobKey ?? 'stage3HarmonizeJob';

const progressSteps = document.querySelectorAll('.progress-tracker [data-stage]');
const sortModeSelect = document.getElementById('sortModeSelect');
const batchSizeSelect = document.getElementById('batchSizeSelect');
const reviewAlerts = document.getElementById('reviewAlerts');
const previousBatchButton = document.getElementById('previousBatchButton');
const nextBatchButton = document.getElementById('nextBatchButton');
const completeBatchButton = document.getElementById('completeBatchButton');
const reviewTable = document.getElementById('reviewTable');
const batchProgressList = document.getElementById('batchProgressList');
const batchProgressHint = document.getElementById('batchProgressHint');
const currentBatchIndicator = document.getElementById('currentBatchIndicator');
const confidenceGuide = document.getElementById('confidenceGuide');

const COLUMN_CONFIG = [
  { key: 'therapeutic_agents', label: 'Therapeutic Agents' },
  { key: 'primary_diagnosis', label: 'Primary Diagnosis' },
  { key: 'morphology', label: 'Morphology' },
  { key: 'tissue_origin', label: 'Tissue / Organ Origin' },
  { key: 'sample_site', label: 'Sample Anatomic Site' },
];

const toAlternatives = (entries) =>
  entries.map(([value, confidence, model]) => ({
    value,
    confidence,
    model,
  }));

const SAMPLE_RECORDS = [
  {
    recordId: 'PT-001',
    cells: [
      {
        columnKey: 'therapeutic_agents',
        originalValue: 'Keytruda',
        harmonizedValue: 'Pembrolizumab',
        confidence: 0.41,
        topModel: 'Therapeutic Agents v2',
        changeType: 'ai_adjustment',
        alternatives: toAlternatives([
          ['Pembrolizumab', 0.41, 'Therapeutic Agents v2'],
          ['Nivolumab', 0.26, 'Therapeutic Agents v2'],
          ['Atezolizumab', 0.19, 'Therapeutic Agents v2'],
        ]),
      },
      {
        columnKey: 'primary_diagnosis',
        originalValue: 'Adenocarcinoma, lung',
        harmonizedValue: 'Lung adenocarcinoma',
        confidence: 0.67,
        topModel: 'Primary Diagnosis v3',
        changeType: 'ai_adjustment',
        alternatives: toAlternatives([
          ['Lung adenocarcinoma', 0.67, 'Primary Diagnosis v3'],
          ['NSCLC', 0.51, 'Primary Diagnosis v3'],
          ['Pulmonary carcinoma', 0.32, 'Primary Diagnosis v3'],
        ]),
      },
      {
        columnKey: 'morphology',
        originalValue: 'Acinar',
        harmonizedValue: 'Acinar adenocarcinoma',
        confidence: 0.53,
        topModel: 'Morphology v2',
        changeType: 'ai_adjustment',
        alternatives: toAlternatives([
          ['Acinar adenocarcinoma', 0.53, 'Morphology v2'],
          ['Solid adenocarcinoma', 0.22, 'Morphology v2'],
        ]),
      },
      {
        columnKey: 'tissue_origin',
        originalValue: 'Lung',
        harmonizedValue: 'Lung',
        confidence: 0.91,
        topModel: 'Tissue Origin v1',
        changeType: 'no_change',
        alternatives: toAlternatives([
          ['Lung', 0.91, 'Tissue Origin v1'],
          ['Left lung', 0.4, 'Tissue Origin v1'],
        ]),
      },
      {
        columnKey: 'sample_site',
        originalValue: 'Left lower lobe',
        harmonizedValue: 'Left lower lobe lung',
        confidence: 0.78,
        topModel: 'Sample Site v2',
        changeType: 'ai_adjustment',
        alternatives: toAlternatives([
          ['Left lower lobe lung', 0.78, 'Sample Site v2'],
          ['Lower lobe', 0.49, 'Sample Site v2'],
        ]),
      },
    ],
  },
  {
    recordId: 'PT-002',
    cells: [
      {
        columnKey: 'therapeutic_agents',
        originalValue: 'Opdivo',
        harmonizedValue: 'Nivolumab',
        confidence: 0.86,
        topModel: 'Therapeutic Agents v2',
        changeType: 'no_change',
        alternatives: toAlternatives([
          ['Nivolumab', 0.86, 'Therapeutic Agents v2'],
          ['Pembrolizumab', 0.33, 'Therapeutic Agents v2'],
        ]),
      },
      {
        columnKey: 'primary_diagnosis',
        originalValue: 'Breast ca',
        harmonizedValue: 'Invasive ductal carcinoma of breast',
        confidence: 0.38,
        topModel: 'Primary Diagnosis v3',
        changeType: 'ai_adjustment',
        alternatives: toAlternatives([
          ['Invasive ductal carcinoma of breast', 0.38, 'Primary Diagnosis v3'],
          ['Triple negative breast cancer', 0.27, 'Primary Diagnosis v3'],
        ]),
      },
      {
        columnKey: 'morphology',
        originalValue: 'IDC',
        harmonizedValue: 'Infiltrating duct carcinoma',
        confidence: 0.35,
        topModel: 'Morphology v2',
        changeType: 'ai_adjustment',
        needsRerun: true,
        alternatives: toAlternatives([
          ['Infiltrating duct carcinoma', 0.35, 'Morphology v2'],
          ['Medullary carcinoma', 0.22, 'Morphology v2'],
        ]),
      },
      {
        columnKey: 'tissue_origin',
        originalValue: 'Breast',
        harmonizedValue: 'Left breast',
        confidence: 0.58,
        topModel: 'Tissue Origin v1',
        changeType: 'ai_adjustment',
        alternatives: toAlternatives([
          ['Left breast', 0.58, 'Tissue Origin v1'],
          ['Breast', 0.49, 'Tissue Origin v1'],
        ]),
      },
      {
        columnKey: 'sample_site',
        originalValue: 'Left breast',
        harmonizedValue: 'Left breast',
        confidence: 0.52,
        manualOverride: 'Left breast quadrant',
        changeType: 'manual_override',
        topModel: 'Sample Site v2',
        alternatives: toAlternatives([
          ['Upper outer quadrant breast', 0.36, 'Sample Site v2'],
          ['Left breast quadrant', 0.31, 'Sample Site v2'],
        ]),
      },
    ],
  },
  {
    recordId: 'PT-003',
    cells: [
      {
        columnKey: 'therapeutic_agents',
        originalValue: 'Atezo',
        harmonizedValue: 'Atezolizumab',
        confidence: 0.72,
        topModel: 'Therapeutic Agents v2',
        changeType: 'ai_adjustment',
        alternatives: toAlternatives([
          ['Atezolizumab', 0.72, 'Therapeutic Agents v2'],
          ['Durvalumab', 0.43, 'Therapeutic Agents v2'],
        ]),
      },
      {
        columnKey: 'primary_diagnosis',
        originalValue: 'Melanoma',
        harmonizedValue: 'Cutaneous melanoma',
        confidence: 0.81,
        topModel: 'Primary Diagnosis v3',
        changeType: 'no_change',
        alternatives: toAlternatives([
          ['Cutaneous melanoma', 0.81, 'Primary Diagnosis v3'],
          ['Melanoma', 0.74, 'Primary Diagnosis v3'],
        ]),
      },
      {
        columnKey: 'morphology',
        originalValue: 'Superficial spreading',
        harmonizedValue: 'Superficial spreading melanoma',
        confidence: 0.64,
        topModel: 'Morphology v2',
        changeType: 'ai_adjustment',
        alternatives: toAlternatives([
          ['Superficial spreading melanoma', 0.64, 'Morphology v2'],
          ['Nodular melanoma', 0.39, 'Morphology v2'],
        ]),
      },
      {
        columnKey: 'tissue_origin',
        originalValue: 'Skin',
        harmonizedValue: 'Skin',
        confidence: 0.89,
        topModel: 'Tissue Origin v1',
        changeType: 'no_change',
        alternatives: toAlternatives([
          ['Skin', 0.89, 'Tissue Origin v1'],
          ['Epidermis', 0.51, 'Tissue Origin v1'],
        ]),
      },
      {
        columnKey: 'sample_site',
        originalValue: 'Right shoulder lesion',
        harmonizedValue: null,
        confidence: 0.0,
        topModel: 'Sample Site v2',
        changeType: 'missing',
        needsRerun: true,
        alternatives: [],
      },
    ],
  },
  {
    recordId: 'PT-004',
    cells: [
      {
        columnKey: 'therapeutic_agents',
        originalValue: 'Revlimid',
        harmonizedValue: 'Lenalidomide',
        confidence: 0.9,
        topModel: 'Therapeutic Agents v2',
        changeType: 'no_change',
        alternatives: toAlternatives([
          ['Lenalidomide', 0.9, 'Therapeutic Agents v2'],
          ['Pomalidomide', 0.46, 'Therapeutic Agents v2'],
        ]),
      },
      {
        columnKey: 'primary_diagnosis',
        originalValue: 'Multiple myeloma',
        harmonizedValue: 'Multiple myeloma',
        confidence: 0.93,
        topModel: 'Primary Diagnosis v3',
        changeType: 'no_change',
        alternatives: toAlternatives([
          ['Multiple myeloma', 0.93, 'Primary Diagnosis v3'],
          ['Smoldering myeloma', 0.51, 'Primary Diagnosis v3'],
        ]),
      },
      {
        columnKey: 'morphology',
        originalValue: 'Plasma cell',
        harmonizedValue: 'Plasma cell myeloma',
        confidence: 0.74,
        topModel: 'Morphology v2',
        changeType: 'ai_adjustment',
        alternatives: toAlternatives([
          ['Plasma cell myeloma', 0.74, 'Morphology v2'],
          ['Diffuse large B-cell lymphoma', 0.33, 'Morphology v2'],
        ]),
      },
      {
        columnKey: 'tissue_origin',
        originalValue: 'Bone marrow',
        harmonizedValue: 'Bone marrow',
        confidence: 0.84,
        topModel: 'Tissue Origin v1',
        changeType: 'no_change',
        alternatives: toAlternatives([
          ['Bone marrow', 0.84, 'Tissue Origin v1'],
          ['Iliac crest marrow', 0.39, 'Tissue Origin v1'],
        ]),
      },
      {
        columnKey: 'sample_site',
        originalValue: 'Left iliac crest',
        harmonizedValue: 'Left iliac crest marrow',
        confidence: 0.69,
        topModel: 'Sample Site v2',
        changeType: 'ai_adjustment',
        alternatives: toAlternatives([
          ['Left iliac crest marrow', 0.69, 'Sample Site v2'],
          ['Bone marrow aspirate', 0.44, 'Sample Site v2'],
        ]),
      },
    ],
  },
  {
    recordId: 'PT-005',
    cells: [
      {
        columnKey: 'therapeutic_agents',
        originalValue: 'Carbo/Taxol',
        harmonizedValue: 'Carboplatin + Paclitaxel',
        confidence: 0.48,
        topModel: 'Therapeutic Agents v2',
        changeType: 'ai_adjustment',
        alternatives: toAlternatives([
          ['Carboplatin + Paclitaxel', 0.48, 'Therapeutic Agents v2'],
          ['Cisplatin + Paclitaxel', 0.31, 'Therapeutic Agents v2'],
        ]),
      },
      {
        columnKey: 'primary_diagnosis',
        originalValue: 'Ovarian ca',
        harmonizedValue: 'High-grade serous ovarian carcinoma',
        confidence: 0.44,
        topModel: 'Primary Diagnosis v3',
        changeType: 'ai_adjustment',
        alternatives: toAlternatives([
          ['High-grade serous ovarian carcinoma', 0.44, 'Primary Diagnosis v3'],
          ['Epithelial ovarian cancer', 0.38, 'Primary Diagnosis v3'],
        ]),
      },
      {
        columnKey: 'morphology',
        originalValue: 'Serous',
        harmonizedValue: 'Serous carcinoma',
        confidence: 0.57,
        topModel: 'Morphology v2',
        changeType: 'ai_adjustment',
        alternatives: toAlternatives([
          ['Serous carcinoma', 0.57, 'Morphology v2'],
          ['Clear cell carcinoma', 0.25, 'Morphology v2'],
        ]),
      },
      {
        columnKey: 'tissue_origin',
        originalValue: 'Ovary',
        harmonizedValue: 'Ovary',
        confidence: 0.73,
        topModel: 'Tissue Origin v1',
        changeType: 'no_change',
        alternatives: toAlternatives([
          ['Ovary', 0.73, 'Tissue Origin v1'],
          ['Pelvis', 0.28, 'Tissue Origin v1'],
        ]),
      },
      {
        columnKey: 'sample_site',
        originalValue: 'Right adnexa',
        harmonizedValue: 'Right adnexa',
        confidence: 0.32,
        topModel: 'Sample Site v2',
        changeType: 'ai_adjustment',
        needsRerun: true,
        alternatives: toAlternatives([
          ['Right adnexa', 0.32, 'Sample Site v2'],
          ['Pelvic mass', 0.29, 'Sample Site v2'],
        ]),
      },
    ],
  },
  {
    recordId: 'PT-006',
    cells: [
      {
        columnKey: 'therapeutic_agents',
        originalValue: 'FOLFOX',
        harmonizedValue: 'FOLFOX',
        confidence: 0.61,
        topModel: 'Therapeutic Agents v2',
        changeType: 'no_change',
        alternatives: toAlternatives([
          ['FOLFOX', 0.61, 'Therapeutic Agents v2'],
          ['FOLFIRI', 0.42, 'Therapeutic Agents v2'],
        ]),
      },
      {
        columnKey: 'primary_diagnosis',
        originalValue: 'Colon cancer',
        harmonizedValue: 'Colon adenocarcinoma',
        confidence: 0.59,
        topModel: 'Primary Diagnosis v3',
        changeType: 'ai_adjustment',
        alternatives: toAlternatives([
          ['Colon adenocarcinoma', 0.59, 'Primary Diagnosis v3'],
          ['Colorectal carcinoma', 0.49, 'Primary Diagnosis v3'],
        ]),
      },
      {
        columnKey: 'morphology',
        originalValue: 'Moderately differentiated',
        harmonizedValue: 'Moderately differentiated adenocarcinoma',
        confidence: 0.63,
        topModel: 'Morphology v2',
        changeType: 'ai_adjustment',
        alternatives: toAlternatives([
          ['Moderately differentiated adenocarcinoma', 0.63, 'Morphology v2'],
          ['Well differentiated adenocarcinoma', 0.38, 'Morphology v2'],
        ]),
      },
      {
        columnKey: 'tissue_origin',
        originalValue: 'Colon',
        harmonizedValue: 'Ascending colon',
        confidence: 0.47,
        topModel: 'Tissue Origin v1',
        changeType: 'ai_adjustment',
        alternatives: toAlternatives([
          ['Ascending colon', 0.47, 'Tissue Origin v1'],
          ['Colon', 0.44, 'Tissue Origin v1'],
        ]),
      },
      {
        columnKey: 'sample_site',
        originalValue: 'Hepatic flexure',
        harmonizedValue: 'Hepatic flexure colon',
        confidence: 0.51,
        topModel: 'Sample Site v2',
        changeType: 'ai_adjustment',
        alternatives: toAlternatives([
          ['Hepatic flexure colon', 0.51, 'Sample Site v2'],
          ['Transverse colon', 0.33, 'Sample Site v2'],
        ]),
      },
    ],
  },
  {
    recordId: 'PT-007',
    cells: [
      {
        columnKey: 'therapeutic_agents',
        originalValue: 'Iressa',
        harmonizedValue: 'Gefitinib',
        confidence: 0.56,
        topModel: 'Therapeutic Agents v2',
        changeType: 'ai_adjustment',
        alternatives: toAlternatives([
          ['Gefitinib', 0.56, 'Therapeutic Agents v2'],
          ['Erlotinib', 0.37, 'Therapeutic Agents v2'],
        ]),
      },
      {
        columnKey: 'primary_diagnosis',
        originalValue: 'EGFR mutant NSCLC',
        harmonizedValue: 'Non-small cell lung cancer (EGFR+)',
        confidence: 0.62,
        topModel: 'Primary Diagnosis v3',
        changeType: 'ai_adjustment',
        alternatives: toAlternatives([
          ['Non-small cell lung cancer (EGFR+)', 0.62, 'Primary Diagnosis v3'],
          ['Lung adenocarcinoma', 0.45, 'Primary Diagnosis v3'],
        ]),
      },
      {
        columnKey: 'morphology',
        originalValue: 'Papillary',
        harmonizedValue: 'Papillary adenocarcinoma',
        confidence: 0.52,
        topModel: 'Morphology v2',
        changeType: 'ai_adjustment',
        alternatives: toAlternatives([
          ['Papillary adenocarcinoma', 0.52, 'Morphology v2'],
          ['Micropapillary adenocarcinoma', 0.36, 'Morphology v2'],
        ]),
      },
      {
        columnKey: 'tissue_origin',
        originalValue: 'Lung',
        harmonizedValue: 'Right upper lobe lung',
        confidence: 0.55,
        topModel: 'Tissue Origin v1',
        changeType: 'ai_adjustment',
        needsRerun: true,
        alternatives: toAlternatives([
          ['Right upper lobe lung', 0.55, 'Tissue Origin v1'],
          ['Lung', 0.52, 'Tissue Origin v1'],
        ]),
      },
      {
        columnKey: 'sample_site',
        originalValue: 'Bronchial brush',
        harmonizedValue: 'Bronchial brush',
        confidence: 0.76,
        topModel: 'Sample Site v2',
        changeType: 'no_change',
        alternatives: toAlternatives([
          ['Bronchial brush', 0.76, 'Sample Site v2'],
          ['Bronchoalveolar lavage', 0.41, 'Sample Site v2'],
        ]),
      },
    ],
  },
  {
    recordId: 'PT-008',
    cells: [
      {
        columnKey: 'therapeutic_agents',
        originalValue: 'Trametinib',
        harmonizedValue: 'Trametinib',
        confidence: 0.88,
        topModel: 'Therapeutic Agents v2',
        changeType: 'no_change',
        alternatives: toAlternatives([
          ['Trametinib', 0.88, 'Therapeutic Agents v2'],
          ['Cobimetinib', 0.42, 'Therapeutic Agents v2'],
        ]),
      },
      {
        columnKey: 'primary_diagnosis',
        originalValue: 'BRAF v600E melanoma',
        harmonizedValue: 'BRAF V600E-positive melanoma',
        confidence: 0.74,
        topModel: 'Primary Diagnosis v3',
        changeType: 'ai_adjustment',
        alternatives: toAlternatives([
          ['BRAF V600E-positive melanoma', 0.74, 'Primary Diagnosis v3'],
          ['Cutaneous melanoma', 0.52, 'Primary Diagnosis v3'],
        ]),
      },
      {
        columnKey: 'morphology',
        originalValue: 'Spindle cell melanoma',
        harmonizedValue: 'Spindle cell melanoma',
        confidence: 0.47,
        topModel: 'Morphology v2',
        changeType: 'ai_adjustment',
        alternatives: toAlternatives([
          ['Spindle cell melanoma', 0.47, 'Morphology v2'],
          ['Desmoplastic melanoma', 0.34, 'Morphology v2'],
        ]),
      },
      {
        columnKey: 'tissue_origin',
        originalValue: 'Skin',
        harmonizedValue: 'Skin of back',
        confidence: 0.46,
        topModel: 'Tissue Origin v1',
        changeType: 'ai_adjustment',
        alternatives: toAlternatives([
          ['Skin of back', 0.46, 'Tissue Origin v1'],
          ['Skin', 0.42, 'Tissue Origin v1'],
        ]),
      },
      {
        columnKey: 'sample_site',
        originalValue: 'Back lesion',
        harmonizedValue: 'Upper back skin',
        confidence: 0.58,
        topModel: 'Sample Site v2',
        changeType: 'ai_adjustment',
        alternatives: toAlternatives([
          ['Upper back skin', 0.58, 'Sample Site v2'],
          ['Trunk skin', 0.41, 'Sample Site v2'],
        ]),
      },
    ],
  },
  {
    recordId: 'PT-009',
    cells: [
      {
        columnKey: 'therapeutic_agents',
        originalValue: 'Temodar',
        harmonizedValue: 'Temozolomide',
        confidence: 0.82,
        topModel: 'Therapeutic Agents v2',
        changeType: 'no_change',
        alternatives: toAlternatives([
          ['Temozolomide', 0.82, 'Therapeutic Agents v2'],
          ['Procarbazine', 0.29, 'Therapeutic Agents v2'],
        ]),
      },
      {
        columnKey: 'primary_diagnosis',
        originalValue: 'GBM',
        harmonizedValue: null,
        confidence: 0.0,
        changeType: 'missing',
        needsRerun: true,
        topModel: 'Primary Diagnosis v3',
        alternatives: toAlternatives([
          ['Glioblastoma multiforme', 0.45, 'Primary Diagnosis v3'],
          ['WHO grade IV astrocytoma', 0.31, 'Primary Diagnosis v3'],
        ]),
      },
      {
        columnKey: 'morphology',
        originalValue: 'Glioblastoma',
        harmonizedValue: 'Glioblastoma',
        confidence: 0.71,
        topModel: 'Morphology v2',
        changeType: 'no_change',
        alternatives: toAlternatives([
          ['Glioblastoma', 0.71, 'Morphology v2'],
          ['Astrocytoma', 0.41, 'Morphology v2'],
        ]),
      },
      {
        columnKey: 'tissue_origin',
        originalValue: 'Brain',
        harmonizedValue: 'Frontal lobe',
        confidence: 0.52,
        topModel: 'Tissue Origin v1',
        changeType: 'ai_adjustment',
        alternatives: toAlternatives([
          ['Frontal lobe', 0.52, 'Tissue Origin v1'],
          ['Brain', 0.49, 'Tissue Origin v1'],
        ]),
      },
      {
        columnKey: 'sample_site',
        originalValue: 'Right frontal tumor',
        harmonizedValue: 'Right frontal lobe tumor',
        confidence: 0.46,
        topModel: 'Sample Site v2',
        changeType: 'ai_adjustment',
        alternatives: toAlternatives([
          ['Right frontal lobe tumor', 0.46, 'Sample Site v2'],
          ['Frontal resection cavity', 0.33, 'Sample Site v2'],
        ]),
      },
    ],
  },
];

const STAGE_ORDER = ['upload', 'mapping', 'harmonize', 'review', 'export'];
const CONFIDENCE_THRESHOLDS = {
  low: 0.4,
  medium: 0.8,
};
const SORT_LABEL_COPY = {
  'confidence-asc': 'Sorted by lowest confidence first.',
  'confidence-desc': 'Sorted by highest confidence first.',
  original: 'Sorted by the original upload order.',
};

const state = {
  rows: [],
  sortMode: 'confidence-asc',
  batchSize: 5,
  currentBatch: 1,
  completedBatches: new Set(),
  flaggedBatches: new Set(),
  context: {},
  job: null,
  alertTimer: null,
};

const setActiveStage = (stage) => {
  const targetIndex = STAGE_ORDER.indexOf(stage);
  progressSteps.forEach((step) => {
    const stepStage = step.dataset.stage;
    const stepIndex = STAGE_ORDER.indexOf(stepStage);
    const isActive = stepStage === stage;
    const isComplete = stepIndex >= 0 && stepIndex < targetIndex;
    step.classList.toggle('active', isActive);
    step.classList.toggle('complete', isComplete);
  });
};

const bucketFromConfidence = (value) => {
  const score = Number(value ?? 0);
  if (Number.isNaN(score) || score <= CONFIDENCE_THRESHOLDS.low) {
    return 'low';
  }
  if (score < CONFIDENCE_THRESHOLDS.medium) {
    return 'medium';
  }
  return 'high';
};

const safeJsonParse = (raw) => {
  try {
    return JSON.parse(raw);
  } catch (error) {
    console.warn('Unable to parse JSON payload', error);
    return null;
  }
};

const readFromSession = (key) => {
  try {
    const raw = sessionStorage.getItem(key);
    return raw ? safeJsonParse(raw) : null;
  } catch (error) {
    console.warn('Unable to read session storage', error);
    return null;
  }
};

const writeToSession = (key, value) => {
  try {
    sessionStorage.setItem(key, JSON.stringify(value));
  } catch (error) {
    console.warn('Unable to write to session storage', error);
  }
};

const augmentCell = (recordId, column, source) => {
  const base = source || {};
  const confidence = Number(base.confidence ?? 0);
  const harmonizedValue = base.manualOverride ?? base.harmonizedValue ?? null;
  const originalValue = base.originalValue ?? null;
  const isChanged = harmonizedValue !== originalValue || harmonizedValue === null;
  return {
    recordId,
    columnKey: column.key,
    columnLabel: column.label,
    originalValue,
    harmonizedValue,
    confidence,
    bucket: base.bucket ?? bucketFromConfidence(confidence),
    needsRerun: Boolean(base.needsRerun),
    isChanged,
  };
};

const buildRows = () =>
  SAMPLE_RECORDS.map((record, index) => {
    const cells = COLUMN_CONFIG.map((column) => {
      const match = record.cells.find((cell) => cell.columnKey === column.key);
      return augmentCell(record.recordId, column, match);
    });
    return {
      recordId: record.recordId,
      rowNumber: index + 1,
      originalIndex: index,
      cells,
    };
  });

const getRowAttentionCell = (row) => {
  const changed = row.cells.filter((cell) => cell.isChanged);
  const pool = changed.length ? changed : row.cells;
  return [...pool].sort((a, b) => a.confidence - b.confidence)[0];
};

const sortRows = (rows) => {
  const sorted = [...rows];
  sorted.sort((a, b) => {
    if (state.sortMode === 'original') {
      return a.originalIndex - b.originalIndex;
    }
    const aCell = getRowAttentionCell(a);
    const bCell = getRowAttentionCell(b);
    if (state.sortMode === 'confidence-desc') {
      return bCell.confidence - aCell.confidence || a.originalIndex - b.originalIndex;
    }
    return aCell.confidence - bCell.confidence || a.originalIndex - b.originalIndex;
  });
  return sorted;
};

const doesRowNeedAttention = (row) => row.cells.some((cell) => cell.needsRerun || cell.harmonizedValue === null);

const buildBatchSummaries = () => {
  const rows = sortRows(state.rows);
  const batchSize = Math.max(1, state.batchSize);
  if (!rows.length) {
    return {
      summaries: [
        {
          index: 1,
          rows: [],
          startRow: 0,
          endRow: 0,
          flagged: false,
        },
      ],
      totalRows: 0,
    };
  }
  const summaries = [];
  for (let start = 0; start < rows.length; start += batchSize) {
    const slice = rows.slice(start, start + batchSize);
    summaries.push({
      index: summaries.length + 1,
      rows: slice,
      startRow: start + 1,
      endRow: start + slice.length,
      flagged: slice.some(doesRowNeedAttention),
    });
  }
  return {
    summaries,
    totalRows: rows.length,
  };
};

const getCurrentBatchRows = () => {
  const { summaries, totalRows } = buildBatchSummaries();
  const totalBatches = summaries.length;
  state.currentBatch = Math.min(Math.max(state.currentBatch, 1), totalBatches);
  Array.from(state.completedBatches).forEach((index) => {
    if (index > totalBatches) {
      state.completedBatches.delete(index);
    }
  });
  Array.from(state.flaggedBatches).forEach((index) => {
    if (index > totalBatches) {
      state.flaggedBatches.delete(index);
    }
  });
  const current = summaries[state.currentBatch - 1];
  return {
    rows: current.rows,
    totalRows,
    totalBatches,
    summaries,
  };
};

const updateBatchMetadata = (batchMeta) => {
  const totalBatches = Math.max(1, batchMeta.totalBatches);
  const hasRows = batchMeta.rows.length > 0;
  previousBatchButton.disabled = state.currentBatch <= 1 || !hasRows;
  nextBatchButton.disabled = state.currentBatch >= totalBatches || !hasRows;
  completeBatchButton.disabled = !hasRows;
  const actionMode = hasRows && state.completedBatches.has(state.currentBatch) ? 'flag' : 'complete';
  completeBatchButton.dataset.mode = actionMode;
  completeBatchButton.textContent = actionMode === 'flag' ? 'Flag batch for review' : 'Mark batch complete';
};

const updateCurrentBatchIndicator = (batchMeta) => {
  if (!currentBatchIndicator) {
    return;
  }
  const total = Math.max(1, batchMeta.totalBatches);
  if (!batchMeta.rows.length) {
    currentBatchIndicator.textContent = 'No batches ready for review yet.';
    currentBatchIndicator.classList.add('muted');
  } else {
    currentBatchIndicator.textContent = `Reviewing batch ${state.currentBatch} of ${total}`;
    currentBatchIndicator.classList.remove('muted');
  }
};

const notify = (message, tone = 'info') => {
  if (!reviewAlerts) {
    return;
  }
  reviewAlerts.textContent = message;
  reviewAlerts.classList.remove('hidden', 'success', 'warning');
  if (tone === 'success') {
    reviewAlerts.classList.add('success');
  } else if (tone === 'warning') {
    reviewAlerts.classList.add('warning');
  }
  if (state.alertTimer) {
    window.clearTimeout(state.alertTimer);
  }
  state.alertTimer = window.setTimeout(() => {
    reviewAlerts.classList.add('hidden');
  }, 5000);
};

const renderConfidenceGuide = () => {
  if (!confidenceGuide) {
    return;
  }
  const lowMax = Math.round(CONFIDENCE_THRESHOLDS.low * 100);
  const mediumMin = lowMax + 1;
  const mediumMax = Math.max(Math.round(CONFIDENCE_THRESHOLDS.medium * 100) - 1, mediumMin);
  const highMin = Math.round(CONFIDENCE_THRESHOLDS.medium * 100);
  confidenceGuide.innerHTML = `
    <details class="confidence-details">
      <summary>Confidence guide</summary>
      <ul>
        <li><span class="confidence-badge low"></span><strong>Low (≤ ${lowMax}%):</strong> highlighted red and queued for review.</li>
        <li><span class="confidence-badge medium"></span><strong>Medium (${mediumMin}–${mediumMax}%):</strong> shown in yellow to encourage double-checking.</li>
        <li><span class="confidence-badge high"></span><strong>High (≥ ${highMin}%):</strong> displayed in green when the model is confident.</li>
        <li><span class="confidence-badge none"></span><strong>No change:</strong> gray cards indicate the model kept the original value.</li>
      </ul>
    </details>
  `;
};

const PROGRESS_STATUS_LABELS = {
  complete: 'Complete',
  flagged: 'Needs review',
  pending: 'Pending',
};

const renderBatchProgress = (batchMeta) => {
  if (!batchProgressList) {
    return;
  }
  batchProgressList.innerHTML = '';
  const meaningful = batchMeta.summaries.filter((summary) => summary.rows.length);
  const displayBatches = meaningful.length ? meaningful : batchMeta.summaries.slice(0, 1);
  const total = meaningful.length;
  const completedCount = meaningful.filter((summary) => state.completedBatches.has(summary.index)).length;
  const flaggedCount = meaningful.filter((summary) => {
    const index = summary.index;
    if (state.flaggedBatches.has(index)) {
      return true;
    }
    return !state.completedBatches.has(index) && summary.flagged;
  }).length;

  if (batchProgressHint) {
    if (!total) {
      batchProgressHint.textContent = 'Awaiting harmonized rows.';
    } else if (completedCount === total) {
      batchProgressHint.textContent = 'All batches reviewed.';
    } else {
      let copy = `${completedCount}/${total} batches complete`;
      if (flaggedCount > 0) {
        copy += ` · ${flaggedCount} flagged`;
      }
      batchProgressHint.textContent = copy;
    }
  }

  displayBatches.forEach((summary) => {
    const hasRows = summary.rows.length > 0;
    const manualFlagged = state.flaggedBatches.has(summary.index);
    const isComplete = state.completedBatches.has(summary.index);
    const status = manualFlagged
      ? 'flagged'
      : isComplete
        ? 'complete'
        : summary.flagged
          ? 'flagged'
          : 'pending';
    const item = document.createElement('button');
    item.type = 'button';
    item.className = `batch-progress-item ${status}${summary.index === state.currentBatch ? ' current' : ''}`;
    item.textContent = hasRows ? summary.index : '—';
    item.disabled = !hasRows;
    item.setAttribute(
      'aria-label',
      hasRows ? `Batch ${summary.index}: ${PROGRESS_STATUS_LABELS[status]}` : 'No harmonized batches yet',
    );
    if (hasRows) {
      item.addEventListener('click', () => {
        if (state.currentBatch === summary.index) {
          return;
        }
        state.currentBatch = summary.index;
        render();
      });
    }
    batchProgressList.append(item);
  });
};

const createCellCard = (cell) => {
  const card = document.createElement('div');
  const classes = ['row-cell'];
  if (cell.isChanged) {
    classes.push(`confidence-${cell.bucket}`);
    if (cell.harmonizedValue === null) {
      classes.push('needs-review');
    }
  } else {
    classes.push('no-change');
  }
  card.className = classes.join(' ');
  card.innerHTML = `
    <p class="cell-column">${cell.columnLabel}</p>
    <div class="value-pair" role="group" aria-label="${cell.columnLabel} comparison">
      <div class="value-group recommended">
        <p class="value-label">Recommended</p>
        <p class="value-text recommended-text${cell.harmonizedValue === null ? ' missing' : ''}">${cell.harmonizedValue ?? '—'}</p>
      </div>
      <div class="value-group original">
        <p class="value-label">Original input</p>
        <p class="value-text original-text">${cell.originalValue ?? '—'}</p>
      </div>
    </div>
  `;
  return card;
};

const renderRows = (batchMeta) => {
  reviewTable.innerHTML = '';
  if (!batchMeta.rows.length) {
    const empty = document.createElement('div');
    empty.className = 'review-empty';
    empty.innerHTML = `
      <p>No harmonized changes to review.</p>
      <p>Once Stage 3 produces updates, they will appear here automatically.</p>
    `;
    reviewTable.append(empty);
    return;
  }

  batchMeta.rows.forEach((row) => {
    const rowCard = document.createElement('article');
    rowCard.className = 'review-row-card';

    const rowHeader = document.createElement('header');
    rowHeader.className = 'row-card-header';
    const attentionCell = getRowAttentionCell(row);
    const hint = document.createElement('p');
    hint.className = 'row-card-hint';
    const changeCount = row.cells.filter((cell) => cell.isChanged).length;
    if (changeCount) {
      hint.innerHTML = `<strong>${changeCount}</strong> column${changeCount === 1 ? '' : 's'} updated · Min confidence ${Math.round(attentionCell.confidence * 100)}%`;
    } else {
      hint.textContent = 'No harmonization changes detected for this row.';
    }
    rowHeader.innerHTML = `<h3 class="row-card-title"><span class="row-id">${row.rowNumber}</span>Row ${row.rowNumber} <small>${row.recordId}</small></h3>`;
    rowHeader.append(hint);

    const grid = document.createElement('div');
    grid.className = 'row-cell-grid';
    grid.style.setProperty('--cell-count', Math.max(row.cells.length, 1));
    row.cells.forEach((cell) => {
      grid.append(createCellCard(cell));
    });

    rowCard.append(rowHeader, grid);
    reviewTable.append(rowCard);
  });
};

const render = () => {
  const batchMeta = getCurrentBatchRows();
  updateBatchMetadata(batchMeta);
  updateCurrentBatchIndicator(batchMeta);
  renderBatchProgress(batchMeta);
  renderRows(batchMeta);
};

const markBatchComplete = () => {
  const meta = getCurrentBatchRows();
  if (!meta.rows.length) {
    notify('No rows in this batch to complete.', 'warning');
    return;
  }
  state.completedBatches.add(state.currentBatch);
  state.flaggedBatches.delete(state.currentBatch);
  const reviewableBatches = meta.summaries.filter((summary) => summary.rows.length);
  const completedCount = reviewableBatches.filter((summary) => state.completedBatches.has(summary.index)).length;
  const allComplete = reviewableBatches.length > 0 && completedCount === reviewableBatches.length;
  const remaining = reviewableBatches.find((summary) => !state.completedBatches.has(summary.index));

  if (allComplete) {
    notify('All batches have been reviewed. You can still revisit previous batches.', 'success');
  } else {
    const remainingCount = Math.max(reviewableBatches.length - completedCount, 0);
    const copy =
      remainingCount > 0
        ? `Batch ${state.currentBatch} marked complete. ${remainingCount} batch${remainingCount === 1 ? '' : 'es'} remaining.`
        : `Batch ${state.currentBatch} marked complete.`;
    notify(copy, 'success');
  }

  if (remaining) {
    state.currentBatch = remaining.index;
  }
  render();
};

const flagCurrentBatch = () => {
  const meta = getCurrentBatchRows();
  if (!meta.rows.length) {
    notify('No rows to flag yet.', 'warning');
    return;
  }
  if (!state.completedBatches.has(state.currentBatch)) {
    notify('Only completed batches can be flagged for review.', 'warning');
    return;
  }
  state.completedBatches.delete(state.currentBatch);
  state.flaggedBatches.add(state.currentBatch);
  notify(`Batch ${state.currentBatch} flagged for review.`, 'warning');
  render();
};

const changeBatch = (delta) => {
  const meta = getCurrentBatchRows();
  const next = Math.min(Math.max(state.currentBatch + delta, 1), meta.totalBatches);
  if (next === state.currentBatch) {
    return;
  }
  state.currentBatch = next;
  render();
};

const hydrateContext = () => {
  const stored = readFromSession(stageThreePayloadKey);
  if (stored?.context) {
    state.context = stored.context;
  }
};

const hydrateJob = () => {
  const params = new URLSearchParams(window.location.search);
  const job = {
    job_id: params.get('job_id'),
    status: params.get('status') || 'completed',
    detail: params.get('detail') || 'Ready for review.',
  };
  if (!job.job_id && !job.status) {
    const stored = readFromSession(stageThreeJobKey);
    if (stored) {
      state.job = stored;
      return;
    }
  }
  state.job = job;
  writeToSession(stageThreeJobKey, job);
};

const attachEventListeners = () => {
  sortModeSelect.addEventListener('change', () => {
    state.sortMode = sortModeSelect.value;
    state.currentBatch = 1;
    state.completedBatches.clear();
    state.flaggedBatches.clear();
    render();
  });
  batchSizeSelect.addEventListener('change', () => {
    state.batchSize = Number(batchSizeSelect.value) || 5;
    state.currentBatch = 1;
    state.completedBatches.clear();
    state.flaggedBatches.clear();
    render();
  });
  previousBatchButton.addEventListener('click', () => changeBatch(-1));
  nextBatchButton.addEventListener('click', () => changeBatch(1));
  completeBatchButton.addEventListener('click', () => {
    const mode = completeBatchButton.dataset.mode || 'complete';
    if (mode === 'flag') {
      flagCurrentBatch();
    } else {
      markBatchComplete();
    }
  });
};

const init = () => {
  setActiveStage('review');
  state.rows = buildRows();
  hydrateContext();
  hydrateJob();
  sortModeSelect.value = state.sortMode;
  batchSizeSelect.value = state.batchSize.toString();
  attachEventListeners();
  renderConfidenceGuide();
  render();
};

init();
