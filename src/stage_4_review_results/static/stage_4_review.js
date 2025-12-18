/**
 * Stage 4 Review - Main orchestrator.
 * Delegates to review mode modules (column or row) based on user selection.
 * Manages state persistence, navigation, and user interactions.
 */
import { initStepInstruction } from '/assets/shared/step-instruction-ui.js';
import * as columnMode from './review_mode_column.js?v=20251216';
import * as rowMode from './review_mode_row.js?v=20251216';

/** @type {Object} */
const config = window.stageFourConfig ?? {};

/** @type {string} */
const stageFiveUrl = config.stageFiveUrl ?? '/stage-5';

/** @type {string} */
const resultsEndpoint = config.resultsEndpoint ?? '/stage-4/rows';

/* DOM element references */
const sortModeSelect = document.getElementById('sortModeSelect');
const batchSizeSelect = document.getElementById('batchSizeSelect');
const batchSizeLabel = batchSizeSelect?.previousElementSibling;
const reviewModeSelect = document.getElementById('reviewModeSelect');
/* reviewAlerts element removed - notifications disabled */
const previousBatchButton = document.getElementById('previousBatchButton');
const nextBatchButton = document.getElementById('nextBatchButton');
const completeBatchButton = document.getElementById('completeBatchButton');
const reviewTable = document.getElementById('reviewTable');
const helpMenuToggle = document.getElementById('helpMenuToggle');
const stageHelp = document.getElementById('stageFourHelp');
const stageFiveButton = document.getElementById('stageFiveButton');
const batchProgressList = document.getElementById('batchProgressList');
const batchProgressHint = document.getElementById('batchProgressHint');
const currentBatchIndicator = document.getElementById('currentBatchIndicator');

/** @type {string[]} */
const STAGE_ORDER = ['upload', 'mapping', 'harmonize', 'review', 'export'];

/**
 * Debounce delay for auto-save in milliseconds.
 * 500ms balances responsive feel (user sees saves happen quickly) with
 * avoiding excessive server requests during rapid typing.
 * @type {number}
 */
const SAVE_DEBOUNCE_MS = 500;

/**
 * Batch size options for column mode.
 * Values represent grid dimension (e.g., 3 = 3x3 = 9 entries per batch).
 * @type {Array<{value: number, label: string}>}
 */
const COLUMN_MODE_BATCH_OPTIONS = [
  { value: 3, label: '3×3 grid' },
  { value: 4, label: '4×4 grid' },
  { value: 5, label: '5×5 grid' },
];

/**
 * Batch size options for row mode.
 * Values represent number of rows per batch.
 * @type {Array<{value: number, label: string}>}
 */
const ROW_MODE_BATCH_OPTIONS = [
  { value: 5, label: '5 rows' },
  { value: 10, label: '10 rows' },
  { value: 15, label: '15 rows' },
];

/** @type {number} Default grid dimension for column mode */
const DEFAULT_COLUMN_BATCH_SIZE = 5;

/** @type {number} Default rows per batch for row mode */
const DEFAULT_ROW_BATCH_SIZE = 5;

/**
 * Application state for Stage 4 review.
 * @type {Object}
 */
const state = {
  rows: [],
  sortMode: 'original',
  reviewMode: 'column',
  alertTimer: null,
  hasLoadedRows: false,
  pendingOverrides: {},
  saveDebounceTimer: null,

  columnMode: {
    currentUnit: 1,
    completedUnits: new Set(),
    flaggedUnits: new Set(),
    batchSize: DEFAULT_COLUMN_BATCH_SIZE,
  },

  rowMode: {
    currentUnit: 1,
    completedUnits: new Set(),
    flaggedUnits: new Set(),
    batchSize: DEFAULT_ROW_BATCH_SIZE,
  },
};

/**
 * Get the state object for the current review mode.
 * @returns {Object}
 */
const getModeState = () => {
  return state.reviewMode === 'column' ? state.columnMode : state.rowMode;
};

/**
 * Get the current batch size based on mode.
 * For column mode, returns entries per batch (gridSize^2).
 * For row mode, returns rows per batch directly.
 * @returns {number}
 */
const getCurrentBatchSize = () => {
  const modeState = getModeState();
  if (state.reviewMode === 'column') {
    return modeState.batchSize * modeState.batchSize;
  }
  return modeState.batchSize;
};

/**
 * Extract file_id from URL query parameters.
 * @returns {string|null}
 */
const getFileIdFromUrl = () => {
  const params = new URLSearchParams(window.location.search);
  return params.get('file_id');
};

/**
 * Fetch harmonized rows from the server.
 * @returns {Promise<void>}
 */
const fetchRows = async () => {
  if (state.hasLoadedRows) return;

  const fileId = getFileIdFromUrl();
  if (!fileId) {
    console.warn('Unable to locate harmonized data. Please rerun Stage 3.');
    return;
  }

  try {
    const response = await fetch(resultsEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        file_id: fileId,
        manual_columns: [],
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
  } catch (error) {
    console.error('Unable to load harmonized results:', error);
  }
};

/**
 * Update progress tracker to show the active stage.
 * @param {string} stage - Stage identifier
 */
const setActiveStage = (stage) => {
  const targetIndex = STAGE_ORDER.indexOf(stage);
  const progressSteps = document.querySelectorAll('.progress-tracker [data-stage]');
  progressSteps.forEach((step) => {
    const stepStage = step.dataset.stage;
    const stepIndex = STAGE_ORDER.indexOf(stepStage);
    const isActive = stepStage === stage;
    const isComplete = stepIndex >= 0 && stepIndex < targetIndex;
    step.classList.toggle('active', isActive);
    step.classList.toggle('complete', isComplete);
  });
};

/**
 * Fetch saved overrides from server.
 * @param {string} fileId
 * @returns {Promise<Object|null>}
 */
const fetchOverrides = async (fileId) => {
  try {
    const response = await fetch(`/stage-4/overrides/${fileId}`);
    if (response.ok) {
      return await response.json();
    }
    if (response.status === 404) {
      return null;
    }
    console.warn('Failed to fetch overrides', response.status);
    return null;
  } catch (error) {
    console.warn('Error fetching overrides', error);
    return null;
  }
};

/**
 * Serialize a mode state object for persistence.
 * @param {Object} modeState - Mode state with currentUnit, completedUnits, flaggedUnits, batchSize
 * @returns {Object}
 */
const _serializeModeState = (modeState) => ({
  current_unit: modeState.currentUnit,
  completed_units: Array.from(modeState.completedUnits),
  flagged_units: Array.from(modeState.flaggedUnits),
  batch_size: modeState.batchSize,
});

/**
 * Build the save payload for overrides and review state.
 * @returns {Object}
 */
const _buildSavePayload = () => ({
  file_id: getFileIdFromUrl(),
  overrides: state.pendingOverrides,
  review_state: {
    review_mode: state.reviewMode,
    sort_mode: state.sortMode,
    column_mode: _serializeModeState(state.columnMode),
    row_mode: _serializeModeState(state.rowMode),
  },
});

/**
 * Save overrides to server.
 * @returns {Promise<void>}
 */
const saveOverrides = async () => {
  const fileId = getFileIdFromUrl();
  if (!fileId) return;

  try {
    const response = await fetch('/stage-4/overrides', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(_buildSavePayload()),
    });
    if (!response.ok) {
      throw new Error('Server returned an error');
    }
  } catch (error) {
    console.warn('Failed to save overrides:', error);
  }
};

/**
 * Schedule a debounced save operation.
 */
const debouncedSave = () => {
  if (state.saveDebounceTimer) {
    clearTimeout(state.saveDebounceTimer);
  }
  state.saveDebounceTimer = setTimeout(saveOverrides, SAVE_DEBOUNCE_MS);
};

/**
 * Save overrides immediately, cancelling any pending debounced save.
 */
const saveOverridesImmediate = () => {
  if (state.saveDebounceTimer) {
    clearTimeout(state.saveDebounceTimer);
    state.saveDebounceTimer = null;
  }
  saveOverrides();
};

/**
 * Record an override for multiple row indices sharing the same original value.
 * @param {number[]} rowIndices - Array of row indices
 * @param {string} columnKey - Column identifier
 * @param {string|null} aiValue - AI-recommended value
 * @param {string} humanValue - User-entered override value
 * @param {string} originalValue - Original input value
 */
const recordOverrideForRows = (rowIndices, columnKey, aiValue, humanValue, originalValue) => {
  for (const rowIndex of rowIndices) {
    const rowKey = String(rowIndex);
    if (!state.pendingOverrides[rowKey]) {
      state.pendingOverrides[rowKey] = {};
    }
    if (humanValue && humanValue.trim()) {
      state.pendingOverrides[rowKey][columnKey] = {
        ai_value: aiValue,
        human_value: humanValue.trim(),
        original_value: originalValue,
      };
    } else {
      delete state.pendingOverrides[rowKey][columnKey];
      if (Object.keys(state.pendingOverrides[rowKey]).length === 0) {
        delete state.pendingOverrides[rowKey];
      }
    }
  }
  debouncedSave();
};

/* Notification toasts removed - visual clutter reduction */

/**
 * Get batch metadata from the active mode module.
 * @returns {Object}
 */
const getCurrentBatchMeta = () => {
  const modeState = getModeState();
  const batchSize = getCurrentBatchSize();
  if (state.reviewMode === 'column') {
    return columnMode.getCurrentEntries(state.rows, modeState.currentUnit, batchSize);
  }
  return rowMode.getCurrentEntries(state.rows, modeState.currentUnit, batchSize);
};

/**
 * Get progress summary from the active mode module.
 * @returns {Object}
 */
const getProgressSummary = () => {
  const modeState = getModeState();
  const batchSize = getCurrentBatchSize();
  if (state.reviewMode === 'column') {
    return columnMode.getProgressSummary(state.rows, modeState.completedUnits, modeState.flaggedUnits, batchSize);
  }
  return rowMode.getProgressSummary(state.rows, batchSize, modeState.completedUnits, modeState.flaggedUnits);
};

/**
 * Get total unit count from the active mode module.
 * @returns {number}
 */
const getTotalUnits = () => {
  const batchSize = getCurrentBatchSize();
  if (state.reviewMode === 'column') {
    return columnMode.getTotalUnits(state.rows, batchSize);
  }
  return rowMode.getTotalUnits(state.rows, batchSize);
};

/**
 * Update navigation button states based on current batch.
 * @param {Object} batchMeta
 */
const updateNavigationButtons = (batchMeta) => {
  const modeState = getModeState();
  const totalUnits = batchMeta.totalUnits;
  const hasEntries = batchMeta.entries.length > 0;

  previousBatchButton.disabled = modeState.currentUnit <= 1 || !hasEntries;
  nextBatchButton.disabled = modeState.currentUnit >= totalUnits || !hasEntries;
  completeBatchButton.disabled = !hasEntries;

  const actionMode = hasEntries && modeState.completedUnits.has(modeState.currentUnit) ? 'flag' : 'complete';
  completeBatchButton.dataset.mode = actionMode;
  completeBatchButton.textContent = actionMode === 'flag' ? 'Flag for review' : 'Mark complete';
  completeBatchButton.classList.toggle('netrias-btn', actionMode === 'complete');
  completeBatchButton.classList.toggle('warning-btn', actionMode === 'flag');
};

/**
 * Update the current batch indicator text.
 * @param {Object} batchMeta
 */
const updateCurrentBatchIndicator = (batchMeta) => {
  if (!currentBatchIndicator) return;

  const modeState = getModeState();
  const batchSize = getCurrentBatchSize();

  if (!batchMeta.entries.length) {
    currentBatchIndicator.textContent = 'Batch progress';
    return;
  }

  if (state.reviewMode === 'column') {
    currentBatchIndicator.textContent = columnMode.getCurrentUnitLabel(state.rows, modeState.currentUnit, batchSize);
  } else {
    currentBatchIndicator.textContent = rowMode.getCurrentUnitLabel(state.rows, modeState.currentUnit, batchSize);
  }
};

/**
 * Update the progress hint text.
 * @param {Object} progressSummary
 */
const updateProgressHint = (progressSummary) => {
  if (!batchProgressHint) return;

  const total = progressSummary.totalCount;
  const completed = progressSummary.completedCount;
  const flagged = progressSummary.flaggedCount;

  if (!total) {
    batchProgressHint.textContent = 'Awaiting harmonized entries.';
    return;
  }

  if (completed === total) {
    batchProgressHint.textContent = state.reviewMode === 'column'
      ? 'All columns reviewed.'
      : 'All batches reviewed.';
    return;
  }

  const unitLabel = state.reviewMode === 'column' ? 'columns' : 'batches';
  let copy = `${completed}/${total} ${unitLabel} complete`;
  if (flagged > 0) {
    copy += ` · ${flagged} flagged`;
  }
  batchProgressHint.textContent = copy;
};

/**
 * Render progress pills using the active mode module.
 * @param {Object} batchMeta
 */
const renderProgressPillsUI = (batchMeta) => {
  if (!batchProgressList) return;

  const modeState = getModeState();
  const onUnitClick = (unitIndex) => {
    modeState.currentUnit = unitIndex;
    render();
  };

  if (state.reviewMode === 'column') {
    columnMode.renderBatchProgress(
      batchProgressList,
      batchMeta,
      modeState.currentUnit,
      modeState.completedUnits,
      modeState.flaggedUnits,
      onUnitClick,
    );
  } else {
    rowMode.renderBatchProgress(
      batchProgressList,
      batchMeta,
      modeState.currentUnit,
      modeState.completedUnits,
      modeState.flaggedUnits,
      onUnitClick,
    );
  }
};

/**
 * Render entry cards using the active mode module.
 * @param {Object} batchMeta
 */
const renderEntries = (batchMeta) => {
  if (!reviewTable) return;

  if (state.reviewMode === 'column') {
    const gridSize = state.columnMode.batchSize;
    columnMode.renderEntries(reviewTable, batchMeta, state.pendingOverrides, recordOverrideForRows, gridSize);
  } else {
    rowMode.renderEntries(reviewTable, batchMeta, state.pendingOverrides, recordOverrideForRows);
  }
};

/**
 * Main render function - updates all UI components.
 */
const render = () => {
  const batchMeta = getCurrentBatchMeta();
  const progressSummary = getProgressSummary();

  updateNavigationButtons(batchMeta);
  updateCurrentBatchIndicator(batchMeta);
  updateProgressHint(progressSummary);
  renderProgressPillsUI(batchMeta);
  renderEntries(batchMeta);
};

/**
 * Mark current unit as complete and advance to next.
 */
const markComplete = () => {
  const modeState = getModeState();
  const batchMeta = getCurrentBatchMeta();

  if (!batchMeta.entries.length) {
    return;
  }

  modeState.completedUnits.add(modeState.currentUnit);
  modeState.flaggedUnits.delete(modeState.currentUnit);

  if (modeState.currentUnit < batchMeta.totalUnits) {
    modeState.currentUnit = modeState.currentUnit + 1;
  }

  saveOverridesImmediate();
  render();
};

/**
 * Flag current unit for review.
 */
const flagCurrent = () => {
  const modeState = getModeState();
  const batchMeta = getCurrentBatchMeta();

  if (!batchMeta.entries.length) {
    return;
  }

  if (!modeState.completedUnits.has(modeState.currentUnit)) {
    return;
  }

  modeState.completedUnits.delete(modeState.currentUnit);
  modeState.flaggedUnits.add(modeState.currentUnit);

  saveOverridesImmediate();
  render();
};

/**
 * Navigate to a different unit.
 * @param {number} delta - Direction to move (-1 or +1)
 */
const changeUnit = (delta) => {
  const modeState = getModeState();
  const totalUnits = getTotalUnits();
  const next = Math.min(Math.max(modeState.currentUnit + delta, 1), totalUnits);

  if (next === modeState.currentUnit) return;

  modeState.currentUnit = next;
  render();
};

/**
 * Populate batch size dropdown with options for the current mode.
 */
const populateBatchSizeOptions = () => {
  if (!batchSizeSelect) return;

  const options = state.reviewMode === 'column' ? COLUMN_MODE_BATCH_OPTIONS : ROW_MODE_BATCH_OPTIONS;
  const modeState = getModeState();

  batchSizeSelect.innerHTML = '';
  for (const opt of options) {
    const option = document.createElement('option');
    option.value = String(opt.value);
    option.textContent = opt.label;
    batchSizeSelect.append(option);
  }

  batchSizeSelect.value = String(modeState.batchSize);

  if (batchSizeLabel) {
    batchSizeLabel.textContent = state.reviewMode === 'column' ? 'Grid size' : 'Batch size';
  }
};

/**
 * Handle review mode toggle (column vs row).
 */
const handleReviewModeChange = () => {
  const newMode = reviewModeSelect.value;
  if (newMode === state.reviewMode) return;

  state.reviewMode = newMode;
  populateBatchSizeOptions();
  saveOverridesImmediate();
  render();
};

/**
 * Handle batch size change.
 */
const handleBatchSizeChange = () => {
  const modeState = getModeState();
  const newSize = Number(batchSizeSelect.value);

  if (newSize === modeState.batchSize) return;

  modeState.batchSize = newSize;
  modeState.currentUnit = 1;
  modeState.completedUnits.clear();
  modeState.flaggedUnits.clear();
  saveOverridesImmediate();
  render();
};

/**
 * Attach all event listeners.
 */
const attachEventListeners = () => {
  sortModeSelect.addEventListener('change', () => {
    state.sortMode = sortModeSelect.value;
    /* Note: Sorting is persisted but not yet implemented in data processing.
       Progress is preserved since changing sort order doesn't invalidate reviews. */
    saveOverridesImmediate();
    render();
  });

  if (batchSizeSelect) {
    batchSizeSelect.addEventListener('change', handleBatchSizeChange);
  }

  if (reviewModeSelect) {
    reviewModeSelect.addEventListener('change', handleReviewModeChange);
  }

  previousBatchButton.addEventListener('click', () => changeUnit(-1));
  nextBatchButton.addEventListener('click', () => changeUnit(1));

  completeBatchButton.addEventListener('click', () => {
    const mode = completeBatchButton.dataset.mode || 'complete';
    if (mode === 'flag') {
      flagCurrent();
    } else {
      markComplete();
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

  /* Navigation via progress tracker steps */
  document.querySelectorAll('.step[data-url]').forEach((step) => {
    step.addEventListener('click', () => {
      const target = step.dataset.url;
      if (target) {
        window.location.assign(target);
      }
    });
  });

  /* Navigation via data-nav-target buttons */
  document.querySelectorAll('[data-nav-target]').forEach((button) => {
    button.addEventListener('click', () => {
      const target = button.dataset.navTarget;
      if (target) {
        window.location.assign(target);
      }
    });
  });
};

/**
 * Load persisted state from server.
 * @returns {Promise<void>}
 */
const loadStateFromDisk = async () => {
  const fileId = getFileIdFromUrl();
  if (!fileId) return;

  const stored = await fetchOverrides(fileId);
  if (!stored) return;

  state.pendingOverrides = stored.overrides || {};
  const reviewState = stored.review_state || {};

  state.reviewMode = reviewState.review_mode || 'column';
  state.sortMode = reviewState.sort_mode || 'original';

  const columnModeState = reviewState.column_mode || {};
  state.columnMode.currentUnit = columnModeState.current_unit || 1;
  state.columnMode.completedUnits = new Set(columnModeState.completed_units || []);
  state.columnMode.flaggedUnits = new Set(columnModeState.flagged_units || []);
  state.columnMode.batchSize = columnModeState.batch_size || DEFAULT_COLUMN_BATCH_SIZE;

  const rowModeState = reviewState.row_mode || {};
  state.rowMode.currentUnit = rowModeState.current_unit || 1;
  state.rowMode.completedUnits = new Set(rowModeState.completed_units || []);
  state.rowMode.flaggedUnits = new Set(rowModeState.flagged_units || []);
  state.rowMode.batchSize = rowModeState.batch_size || DEFAULT_ROW_BATCH_SIZE;
};

/**
 * Initialize Stage 4 review page.
 * @returns {Promise<void>}
 */
const init = async () => {
  setActiveStage('review');
  initStepInstruction('review');

  await loadStateFromDisk();

  sortModeSelect.value = state.sortMode;
  if (reviewModeSelect) {
    reviewModeSelect.value = state.reviewMode;
  }
  populateBatchSizeOptions();

  attachEventListeners();

  /* Flush pending saves on page unload */
  window.addEventListener('beforeunload', () => {
    if (state.saveDebounceTimer) {
      clearTimeout(state.saveDebounceTimer);
      state.saveDebounceTimer = null;
      navigator.sendBeacon(
        '/stage-4/overrides',
        new Blob([JSON.stringify(_buildSavePayload())], { type: 'application/json' }),
      );
    }
  });

  await fetchRows();
  render();
};

init();
