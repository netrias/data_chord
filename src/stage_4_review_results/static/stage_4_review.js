/**
 * Stage 4 Review - Main orchestrator.
 * Delegates to review mode modules (column or row) based on user selection.
 * Manages state persistence, navigation, and user interactions.
 */
import { initStepInstruction, setActiveStage, initNavigationEvents, advanceMaxReachedStage } from '/assets/shared/step-instruction-ui.js';
import { isValidFileId } from '/assets/shared/storage-keys.js';
import {
  getTotalUnits as getColumnTotalUnits,
  getCurrentEntries as getColumnCurrentEntries,
  renderEntries as renderColumnEntries,
  renderBatchProgress as renderColumnBatchProgress,
} from './review_mode_column.js';
import {
  getTotalUnits as getRowTotalUnits,
  getCurrentEntries as getRowCurrentEntries,
  renderEntries as renderRowEntries,
  renderBatchProgress as renderRowBatchProgress,
} from './review_mode_row.js';

/** @type {Object} */
const config = window.stageFourConfig ?? {};

/** @type {string} */
const stageFiveUrl = config.stageFiveUrl ?? '/stage-5';

/** @type {string} */
const resultsEndpoint = config.resultsEndpoint ?? '/stage-4/rows';

/**
 * Get a required DOM element by ID, throwing if not found.
 * @param {string} id - Element ID
 * @returns {HTMLElement}
 */
const _requireElement = (id) => {
  const el = document.getElementById(id);
  if (!el) {
    throw new Error(`Required element #${id} not found in DOM`);
  }
  return el;
};

/* DOM element references - required elements use _requireElement, optional use getElementById */
const sortModeSelect = document.getElementById('sortModeSelect');
const batchSizeSelect = document.getElementById('batchSizeSelect');
const batchSizeLabel = batchSizeSelect?.previousElementSibling;
const reviewModeSelect = document.getElementById('reviewModeSelect');
const previousBatchButton = _requireElement('previousBatchButton');
const nextBatchButton = _requireElement('nextBatchButton');
const reviewTable = document.getElementById('reviewTable');
const stageFiveButton = document.getElementById('stageFiveButton');
const batchProgressList = document.getElementById('batchProgressList');
const settingsButton = document.getElementById('settingsButton');
const settingsModal = document.getElementById('settingsModal');
const settingsCloseButton = document.getElementById('settingsCloseButton');

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
const DEFAULT_COLUMN_BATCH_SIZE = 4;

/** @type {number} Default rows per batch for row mode */
const DEFAULT_ROW_BATCH_SIZE = 5;

/**
 * Application state for Stage 4 review.
 * @type {Object}
 */
const state = {
  rows: [],
  /* why: sortMode UI exists and value persists, but sorting logic not yet implemented. */
  sortMode: 'original',
  reviewMode: 'column',
  pendingOverrides: {},
  saveDebounceTimer: null,

  columnMode: {
    currentUnit: 1,
    batchSize: DEFAULT_COLUMN_BATCH_SIZE,
  },

  rowMode: {
    currentUnit: 1,
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
 * Always fetches fresh data to support back-navigation without stale caching.
 * @returns {Promise<void>}
 */
const fetchRows = async () => {
  const fileId = getFileIdFromUrl();
  if (!fileId) {
    console.warn('Unable to locate harmonized data. Please rerun Stage 3.');
    return;
  }

  if (!isValidFileId(fileId)) {
    console.warn('Invalid file ID format.');
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
      throw new Error('Unable to load harmonized results.');
    }
    const body = await response.json();
    state.rows = body.rows || [];
  } catch (error) {
    console.error('Unable to load harmonized results:', error);
  }
};

/**
 * Fetch saved overrides from server.
 * @param {string} fileId
 * @returns {Promise<Object|null>}
 */
const fetchOverrides = async (fileId) => {
  if (!isValidFileId(fileId)) {
    console.warn('Invalid file ID format for fetching overrides.');
    return null;
  }

  try {
    const response = await fetch(`/stage-4/overrides/${encodeURIComponent(fileId)}`);
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
 * @param {Object} modeState - Mode state with currentUnit, batchSize
 * @returns {Object}
 */
const _serializeModeState = (modeState) => ({
  current_unit: modeState.currentUnit,
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
  if (!fileId || !isValidFileId(fileId)) return;

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

/* why: debounce saves to avoid excessive server requests during rapid edits. */
const _debouncedSave = () => {
  if (state.saveDebounceTimer) {
    clearTimeout(state.saveDebounceTimer);
  }
  state.saveDebounceTimer = setTimeout(saveOverrides, SAVE_DEBOUNCE_MS);
};

/* why: flush pending saves immediately on navigation or explicit save triggers. */
const _saveOverridesImmediate = () => {
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
  _debouncedSave();
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
    return getColumnCurrentEntries(state.rows, modeState.currentUnit, batchSize);
  }
  return getRowCurrentEntries(state.rows, modeState.currentUnit, batchSize);
};


/**
 * Get total unit count from the active mode module.
 * @returns {number}
 */
const getTotalUnits = () => {
  const batchSize = getCurrentBatchSize();
  if (state.reviewMode === 'column') {
    return getColumnTotalUnits(state.rows, batchSize);
  }
  return getRowTotalUnits(state.rows, batchSize);
};

/**
 * Update navigation button states based on current batch.
 * @param {Object} batchMeta
 */
const updateNavigationButtons = (batchMeta) => {
  const modeState = getModeState();
  const hasEntries = batchMeta.entries.length > 0;

  previousBatchButton.disabled = modeState.currentUnit <= 1 || !hasEntries;
  nextBatchButton.disabled = !hasEntries;
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
    renderColumnBatchProgress(batchProgressList, batchMeta, modeState.currentUnit, onUnitClick);
  } else {
    renderRowBatchProgress(batchProgressList, batchMeta, modeState.currentUnit, onUnitClick);
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
    renderColumnEntries(reviewTable, batchMeta, state.pendingOverrides, recordOverrideForRows, gridSize);
  } else {
    renderRowEntries(reviewTable, batchMeta, state.pendingOverrides, recordOverrideForRows);
  }
};

/**
 * Main render function - updates all UI components.
 */
const render = () => {
  const batchMeta = getCurrentBatchMeta();

  updateNavigationButtons(batchMeta);
  renderProgressPillsUI(batchMeta);
  renderEntries(batchMeta);
};

/**
 * Flash the Stage 5 button to draw attention when user tries to advance past last batch.
 */
const flashStageFiveButton = () => {
  if (!stageFiveButton) return;
  /* Guard: skip if animation already in progress to prevent restart on rapid clicks. */
  if (stageFiveButton.classList.contains('attention-pulse')) return;

  stageFiveButton.classList.add('attention-pulse');
  stageFiveButton.addEventListener(
    'animationend',
    () => stageFiveButton.classList.remove('attention-pulse'),
    { once: true },
  );
};

/**
 * Navigate to a different unit.
 * @param {number} delta - Direction to move (-1 or +1)
 */
const changeUnit = (delta) => {
  const modeState = getModeState();
  const totalUnits = getTotalUnits();

  /* Flash Stage 5 button when trying to go past last batch. */
  if (delta > 0 && modeState.currentUnit >= totalUnits) {
    flashStageFiveButton();
    return;
  }

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
 * Clamps currentUnit to valid range after mode switch to prevent out-of-bounds navigation.
 */
const handleReviewModeChange = () => {
  const newMode = reviewModeSelect.value;
  if (newMode === state.reviewMode) return;

  state.reviewMode = newMode;
  populateBatchSizeOptions();

  /* Clamp currentUnit to valid range - different modes may have different unit counts. */
  const modeState = getModeState();
  const totalUnits = getTotalUnits();
  if (totalUnits > 0 && modeState.currentUnit > totalUnits) {
    modeState.currentUnit = totalUnits;
  } else if (totalUnits === 0) {
    modeState.currentUnit = 1;
  }

  _saveOverridesImmediate();
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
  _saveOverridesImmediate();
  render();
};

/**
 * Attach all event listeners.
 */
const attachEventListeners = () => {
  if (sortModeSelect) {
    sortModeSelect.addEventListener('change', () => {
      state.sortMode = sortModeSelect.value;
      /* Note: Sorting is persisted but not yet implemented in data processing.
         Progress is preserved since changing sort order doesn't invalidate reviews. */
      _saveOverridesImmediate();
      render();
    });
  }

  if (batchSizeSelect) {
    batchSizeSelect.addEventListener('change', handleBatchSizeChange);
  }

  if (reviewModeSelect) {
    reviewModeSelect.addEventListener('change', handleReviewModeChange);
  }

  previousBatchButton.addEventListener('click', () => changeUnit(-1));
  nextBatchButton.addEventListener('click', () => changeUnit(1));

  if (stageFiveButton) {
    stageFiveButton.addEventListener('click', () => {
      advanceMaxReachedStage('review');
      const fileId = getFileIdFromUrl();
      const url = fileId ? `${stageFiveUrl}?file_id=${encodeURIComponent(fileId)}` : stageFiveUrl;
      window.location.assign(url);
    });
  }

  if (settingsButton && settingsModal) {
    settingsButton.addEventListener('click', () => {
      settingsModal.showModal();
    });

    settingsCloseButton?.addEventListener('click', () => {
      settingsModal.close();
    });

    settingsModal.addEventListener('click', (event) => {
      if (event.target === settingsModal) {
        settingsModal.close();
      }
    });
  }

  initNavigationEvents();
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
  state.columnMode.batchSize = columnModeState.batch_size || DEFAULT_COLUMN_BATCH_SIZE;

  const rowModeState = reviewState.row_mode || {};
  state.rowMode.currentUnit = rowModeState.current_unit || 1;
  state.rowMode.batchSize = rowModeState.batch_size || DEFAULT_ROW_BATCH_SIZE;
};

/**
 * Initialize Stage 4 review page.
 * @returns {Promise<void>}
 */
const init = async () => {
  setActiveStage('verify');
  initStepInstruction('verify');

  await loadStateFromDisk();

  if (sortModeSelect) {
    sortModeSelect.value = state.sortMode;
  }
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
  _clampCurrentUnitsToValidRange();
  render();
};

/**
 * Clamp currentUnit values to valid range based on loaded data.
 * Prevents out-of-bounds navigation when data changed between sessions.
 */
const _clampCurrentUnitsToValidRange = () => {
  for (const mode of ['column', 'row']) {
    const modeState = mode === 'column' ? state.columnMode : state.rowMode;
    const batchSize = mode === 'column'
      ? modeState.batchSize * modeState.batchSize
      : modeState.batchSize;
    const totalUnits = mode === 'column'
      ? getColumnTotalUnits(state.rows, batchSize)
      : getRowTotalUnits(state.rows, batchSize);

    if (totalUnits > 0 && modeState.currentUnit > totalUnits) {
      modeState.currentUnit = totalUnits;
    } else if (totalUnits === 0) {
      modeState.currentUnit = 1;
    }
  }
};

/* why: re-fetch data when page is restored from browser back-forward cache. */
window.addEventListener('pageshow', async (event) => {
  if (event.persisted) {
    await fetchRows();
    render();
  }
});

init();
