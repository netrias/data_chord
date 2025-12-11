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
const helpMenuToggle = document.getElementById('helpMenuToggle');
const stageHelp = document.getElementById('stageFourHelp');
const stageFiveButton = document.getElementById('stageFiveButton');
const stageFiveUrl = config.stageFiveUrl ?? '/stage-5';
const resultsEndpoint = config.resultsEndpoint ?? '/stage-4/rows';
const batchProgressList = document.getElementById('batchProgressList');
const batchProgressHint = document.getElementById('batchProgressHint');
const currentBatchIndicator = document.getElementById('currentBatchIndicator');

const COLUMN_CONFIG = [
  { key: 'therapeutic_agents', label: 'Therapeutic Agents' },
  { key: 'primary_diagnosis', label: 'Primary Diagnosis' },
  { key: 'morphology', label: 'Morphology' },
  { key: 'tissue_or_organ_of_origin', label: 'Tissue / Organ Origin' },
  { key: 'sample_anatomic_site', label: 'Sample Anatomic Site' },
];


const STAGE_ORDER = ['upload', 'mapping', 'harmonize', 'review', 'export'];
const HIGH_CONFIDENCE_MIN = 0.8;
const SORT_LABEL_COPY = {
  'confidence-asc': 'Sorted by lowest confidence first.',
  'confidence-desc': 'Sorted by highest confidence first.',
  original: 'Sorted by the original upload order.',
};

const state = {
  rows: [],
  sortMode: 'original',
  batchSize: 5,
  currentBatch: 1,
  completedBatches: new Set(),
  flaggedBatches: new Set(),
  context: null,
  job: null,
  alertTimer: null,
  hasLoadedRows: false,
  sourceContext: null,
};

const loadSourceContext = () => {
  const stored = readFromSession(stageThreePayloadKey);
  const fileId = stored?.request?.file_id;
  if (!fileId) {
    return null;
  }
  const manualColumns = Object.keys(stored?.request?.manual_overrides ?? {});
  return { fileId, manualColumns };
};

const fetchRows = async () => {
  if (state.hasLoadedRows) {
    render();
    return;
  }
  if (!state.sourceContext) {
    state.sourceContext = loadSourceContext();
  }
  if (!state.sourceContext?.fileId) {
    notify('Unable to locate harmonized data. Please rerun Stage 3.', 'warning');
    return;
  }
  try {
    const response = await fetch(resultsEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        file_id: state.sourceContext.fileId,
        manual_columns: state.sourceContext.manualColumns ?? [],
      }),
    });
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(detail || 'Unable to load harmonized results.');
    }
    const body = await response.json();
    state.rows = (body.rows || []).map((row) => ({
      ...row,
      originalIndex: Math.max(0, (row.sourceRowNumber ?? row.rowNumber) - 1),
    }));
    state.hasLoadedRows = true;
    state.currentBatch = 1;
    state.completedBatches.clear();
    state.flaggedBatches.clear();
    render();
  } catch (error) {
    console.error(error);
    notify(error.message || 'Unable to load harmonized results.', 'warning');
  }
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
  if (!Number.isNaN(score) && score >= HIGH_CONFIDENCE_MIN) {
    return 'high';
  }
  return 'low';
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
    <div class="value-pair" role="group" aria-label="${cell.columnLabel} comparison">
      <div class="value-group recommended">
        <p class="value-label">Recommended</p>
        <p class="value-text recommended-text${cell.harmonizedValue === null ? ' missing' : ''}">${cell.harmonizedValue ?? '—'}</p>
      </div>
      <div class="value-group original">
        <p class="value-label">Original input</p>
        <p class="value-text original-text">${cell.originalValue ?? '—'}</p>
      </div>
      <label class="value-group value-override">
        <span class="value-label sr-only">Override ${cell.columnLabel}</span>
        <span class="value-input-wrapper">
          <input
            class="value-input"
            type="text"
            value=""
            aria-label="Manual override for ${cell.columnLabel}"
          />
          <svg class="value-input-icon" viewBox="0 0 20 20" aria-hidden="true">
            <path d="M2 14.5V18h3.5l8.4-8.4-3.5-3.5L2 14.5zm11.8-9.1a1 1 0 0 1 1.4 0l1.4 1.4a1 1 0 0 1 0 1.4l-1.2 1.2-3.5-3.5 1.2-1.2z"/>
          </svg>
        </span>
      </label>
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

  const columnsTemplate = ['100px', ...COLUMN_CONFIG.map(() => 'minmax(280px, 1fr)')].join(' ');
  const wrapper = document.createElement('div');
  wrapper.className = 'row-table-wrapper';
  wrapper.style.setProperty('--table-columns', columnsTemplate);

  const viewport = document.createElement('div');
  viewport.className = 'row-table-viewport';

  const header = document.createElement('div');
  header.className = 'row-table-header';
  header.innerHTML = [`<div class="row-index-header">Row</div>`, ...COLUMN_CONFIG.map((column) => `<div class="column-header">${column.label}</div>`)].join('');

  const body = document.createElement('div');
  body.className = 'row-table-body';

  batchMeta.rows.forEach((row) => {
    const rowEl = document.createElement('div');
    rowEl.className = 'row-table-row';
    const indexCell = document.createElement('div');
    indexCell.className = 'row-index-cell';
    indexCell.textContent = `Row ${row.rowNumber}`;
    indexCell.title = row.recordId;
    rowEl.append(indexCell);
    row.cells.forEach((cell) => {
      const cellWrapper = document.createElement('div');
      cellWrapper.className = 'table-cell';
      cellWrapper.append(createCellCard(cell));
      rowEl.append(cellWrapper);
    });
    body.append(rowEl);
  });

  viewport.append(header, body);
  wrapper.append(viewport);
  reviewTable.append(wrapper);
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
  const fileId = stored?.request?.file_id;
  if (fileId) {
    state.sourceContext = {
      fileId,
      manualColumns: Object.keys(stored?.request?.manual_overrides ?? {}),
    };
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

  if (helpMenuToggle && stageHelp) {
    helpMenuToggle.addEventListener('click', () => {
      const expanded = helpMenuToggle.getAttribute('aria-expanded') === 'true';
      helpMenuToggle.setAttribute('aria-expanded', String(!expanded));
      stageHelp.classList.toggle('hidden', expanded);
    });
  }
  if (stageFiveButton) {
    stageFiveButton.addEventListener('click', () => {
      window.location.assign(stageFiveUrl);
    });
  }
};

const init = () => {
  setActiveStage('review');
  hydrateContext();
  hydrateJob();
  sortModeSelect.value = state.sortMode;
  batchSizeSelect.value = state.batchSize.toString();
  attachEventListeners();
  render();
  fetchRows();
};

init();
document.querySelectorAll('.step[data-url]').forEach((step) => {
  step.addEventListener('click', () => {
    const target = step.dataset.url;
    if (target) {
      window.location.assign(target);
    }
  });
});

document.querySelectorAll('[data-nav-target]').forEach((button) => {
  button.addEventListener('click', () => {
    const target = button.dataset.navTarget;
    if (target) {
      window.location.assign(target);
    }
  });
});
