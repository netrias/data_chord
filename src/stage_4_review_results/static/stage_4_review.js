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
  getAllEntries as getColumnAllEntries,
} from './review_mode_column.js';
import { escapeHtml, getFileIdFromUrl, createValueCard, toExcelRowNumber, cleanupCards } from './shared_review_utils.js';
import { showRowContextPopup } from './row_context_popup.js';
import {
  getTotalUnits as getRowTotalUnits,
  getCurrentEntries as getRowCurrentEntries,
  renderEntries as renderRowEntries,
  renderBatchProgress as renderRowBatchProgress,
  getAllEntries as getRowAllEntries,
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
const hideCaseOnlyChangesCheckbox = document.getElementById('hideCaseOnlyChanges');
const showUnchangedValuesCheckbox = document.getElementById('showUnchangedValues');
const scrollModeCheckbox = document.getElementById('scrollModeCheckbox');

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
  columnPVs: {},  // column_key -> sorted PV list (from backend)
  totalOriginalRows: 0,  // Original spreadsheet row count (before grouping)
  sortMode: 'original',
  reviewMode: 'column',
  scrollMode: false,  // Continuous scrolling vs batch navigation
  pendingOverrides: {},
  hideCaseOnlyChanges: true,  // Filter: hide entries where only casing differs
  showUnchangedValues: false,  // Filter: show entries where original equals harmonized

  columnMode: {
    currentUnit: 1,
    batchSize: DEFAULT_COLUMN_BATCH_SIZE,
  },

  rowMode: {
    currentUnit: 1,
    batchSize: DEFAULT_ROW_BATCH_SIZE,
  },
};

const OVERRIDE_SAVE_DELAY_MS = 400;
let overrideSaveTimeout = null;

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
    console.time('fetchRows:networkRequest');
    const response = await fetch(resultsEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        file_id: fileId,
        manual_columns: [],
      }),
    });
    console.timeEnd('fetchRows:networkRequest');
    if (!response.ok) {
      throw new Error('Unable to load harmonized results.');
    }
    console.time('fetchRows:parseJSON');
    const body = await response.json();
    console.timeEnd('fetchRows:parseJSON');
    console.log('fetchRows: received', body.rows?.length, 'rows');
    state.rows = (body.rows || []).map((row) => ({
      ...row,
      sourceRowNumbers: row.sourceRowNumbers ??
        (row.sourceRowNumber ? [row.sourceRowNumber] : [row.rowNumber]),
    }));
    state.columnPVs = body.columnPVs || {};
    state.totalOriginalRows = body.totalOriginalRows || 0;
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
    scroll_mode: state.scrollMode,
    hide_case_only_changes: state.hideCaseOnlyChanges,
    show_unchanged_values: state.showUnchangedValues,
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

/**
 * Debounce override saves to avoid spamming the server on each keystroke.
 */
const scheduleOverrideSave = () => {
  if (overrideSaveTimeout) {
    clearTimeout(overrideSaveTimeout);
  }
  overrideSaveTimeout = window.setTimeout(() => {
    overrideSaveTimeout = null;
    saveOverrides();
  }, OVERRIDE_SAVE_DELAY_MS);
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
    if (humanValue !== '') {
      state.pendingOverrides[rowKey][columnKey] = {
        ai_value: aiValue,
        human_value: humanValue,
        original_value: originalValue,
      };
    } else {
      delete state.pendingOverrides[rowKey][columnKey];
      if (Object.keys(state.pendingOverrides[rowKey]).length === 0) {
        delete state.pendingOverrides[rowKey];
      }
    }
  }
  scheduleOverrideSave();
};

/**
 * Build filter options object from current state.
 * @returns {Object}
 */
const _buildFilterOptions = () => ({
  hideCaseOnlyChanges: state.hideCaseOnlyChanges,
  showUnchangedValues: state.showUnchangedValues,
});

/**
 * Get batch metadata from the active mode module.
 * @returns {Object}
 */
const getCurrentBatchMeta = () => {
  const modeState = getModeState();
  const batchSize = getCurrentBatchSize();
  const filterOptions = _buildFilterOptions();
  if (state.reviewMode === 'column') {
    return getColumnCurrentEntries(state.rows, modeState.currentUnit, batchSize, state.sortMode, filterOptions);
  }
  return getRowCurrentEntries(state.rows, modeState.currentUnit, batchSize, state.sortMode, filterOptions);
};


/**
 * Get total unit count from the active mode module.
 * @returns {number}
 */
const getTotalUnits = () => {
  const batchSize = getCurrentBatchSize();
  const filterOptions = _buildFilterOptions();
  if (state.reviewMode === 'column') {
    return getColumnTotalUnits(state.rows, batchSize, filterOptions);
  }
  return getRowTotalUnits(state.rows, batchSize, filterOptions);
};

/**
 * Update navigation button states based on current batch.
 * @param {Object} batchMeta
 */
const updateNavigationButtons = (batchMeta) => {
  const modeState = getModeState();
  const hasEntries = batchMeta.entries.length > 0;
  const hasSingleBatch = batchMeta.totalUnits <= 1;

  // Hide navigation when only one batch exists
  previousBatchButton.hidden = hasSingleBatch;
  nextBatchButton.hidden = hasSingleBatch;

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
    renderColumnEntries(reviewTable, batchMeta, state.pendingOverrides, recordOverrideForRows, gridSize, state.columnPVs, state.totalOriginalRows);
  } else {
    renderRowEntries(reviewTable, batchMeta, state.pendingOverrides, recordOverrideForRows, state.columnPVs);
  }
};

/**
 * Main render function - updates all UI components.
 */
const render = () => {
  if (state.scrollMode) {
    renderScrollMode();
    return;
  }

  const batchMeta = getCurrentBatchMeta();

  updateNavigationButtons(batchMeta);
  renderProgressPillsUI(batchMeta);
  renderEntries(batchMeta);
};

/**
 * Render all entries in scroll mode.
 * Gets all entries and renders them in a scrollable container.
 */
const renderScrollMode = () => {
  if (!reviewTable) return;

  const filterOptions = _buildFilterOptions();
  const allEntries = state.reviewMode === 'column'
    ? getColumnAllEntries(state.rows, state.sortMode, filterOptions)
    : getRowAllEntries(state.rows, state.sortMode, filterOptions);

  if (!allEntries.length) {
    reviewTable.innerHTML = '';
    const emptyEl = document.createElement('div');
    emptyEl.className = 'review-empty';
    emptyEl.textContent = 'No changes to review';
    reviewTable.append(emptyEl);
    return;
  }

  if (state.reviewMode === 'column') {
    renderColumnScrollMode(allEntries);
  } else {
    renderRowScrollMode(allEntries);
  }
};

/**
 * Render column mode entries in scroll mode.
 * Renders all entries directly, letting the page scroll naturally.
 * Adds column title to each card since entries from all columns are mixed.
 * @param {Array} entries - All column entries
 */
const renderColumnScrollMode = (entries) => {
  cleanupCards(reviewTable);
  reviewTable.innerHTML = '';

  const wrapper = document.createElement('div');
  wrapper.className = 'column-mode-grid';
  wrapper.style.setProperty('--grid-columns', state.columnMode.batchSize);

  const fileId = getFileIdFromUrl();

  for (const entry of entries) {
    const rowCount = entry.rowIndices.length;
    const excelRows = entry.rowIndices.map(toExcelRowNumber);
    const rowLabel = rowCount === 1 ? `Row ${excelRows[0]}` : `${rowCount} rows`;
    const tooltipText = rowCount > 1 ? `Rows: ${excelRows.join(', ')}` : null;

    const card = createValueCard({
      entry,
      labelText: rowLabel,
      tooltipText,
      pendingOverrides: state.pendingOverrides,
      onOverrideChange: recordOverrideForRows,
      columnPVs: state.columnPVs,
    });

    // Add column title to header row for scroll mode
    const headerRow = card.querySelector('.card-header-row');
    if (headerRow) {
      const titleEl = document.createElement('span');
      titleEl.className = 'scroll-mode-column-title';
      titleEl.textContent = entry.columnLabel;
      // Insert after row label so layout is: [confidence] [row] [column title] [pv icons]
      const rowLabelEl = headerRow.querySelector('.entry-row-label');
      if (rowLabelEl) {
        rowLabelEl.after(titleEl);
      } else {
        headerRow.append(titleEl);
      }
    }

    // Make row label clickable to show context popup
    if (fileId && entry.rowIndices?.length) {
      const rowLabelEl = card.querySelector('.entry-row-label');
      if (rowLabelEl) {
        rowLabelEl.classList.add('row-context-link');
        rowLabelEl.addEventListener('click', () => {
          const zeroBasedIndices = entry.rowIndices.map((idx) => idx - 1);
          showRowContextPopup({
            term: entry.originalValue,
            columnKey: entry.columnKey,
            rowIndices: zeroBasedIndices,
            fileId,
            totalOriginalRows: state.totalOriginalRows,
          });
        });
      }
    }

    wrapper.append(card);
  }

  reviewTable.append(wrapper);
};

/**
 * Render row mode entries in scroll mode.
 * Renders all rows directly, letting the page scroll naturally.
 * @param {Array} rows - All rows with changes
 */
const renderRowScrollMode = (rows) => {
  cleanupCards(reviewTable);
  reviewTable.innerHTML = '';

  const wrapper = document.createElement('div');
  wrapper.className = 'row-mode-wrapper';

  for (const row of rows) {
    const rowEl = document.createElement('div');
    rowEl.className = 'row-mode-row';

    const headerEl = document.createElement('div');
    headerEl.className = 'row-mode-header';
    headerEl.textContent = `Row ${toExcelRowNumber(row.rowIndex)}`;
    rowEl.append(headerEl);

    const cellsEl = document.createElement('div');
    cellsEl.className = 'row-mode-cells';

    for (const cell of row.changedCells) {
      const entry = {
        ...cell,
        topSuggestions: cell.topSuggestions ?? [],
        rowIndices: [row.rowIndex],
      };
      const card = createValueCard({
        entry,
        labelText: cell.columnLabel,
        tooltipText: null,
        pendingOverrides: state.pendingOverrides,
        onOverrideChange: recordOverrideForRows,
        columnPVs: state.columnPVs,
      });
      cellsEl.append(card);
    }

    rowEl.append(cellsEl);
    wrapper.append(rowEl);
  }

  reviewTable.append(wrapper);
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

  saveOverrides();
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
  saveOverrides();
  render();
};

/**
 * Handle scroll mode toggle.
 * When enabled, renders all entries in a scrollable container; when disabled, returns to batch mode.
 */
const handleScrollModeChange = () => {
  state.scrollMode = scrollModeCheckbox.checked;
  updateUIForScrollMode();
  saveOverrides();
  render();
};

/**
 * Update UI elements based on scroll mode state.
 * Disables batch size selector and hides navigation controls when scroll mode is active.
 */
const updateUIForScrollMode = () => {
  if (batchSizeSelect) {
    batchSizeSelect.disabled = state.scrollMode;
    batchSizeSelect.title = state.scrollMode
      ? 'Batch size is disabled in scroll mode'
      : '';
  }

  // Toggle visibility of batch navigation elements
  const batchActions = document.querySelector('.batch-actions');
  if (batchActions) {
    batchActions.style.display = state.scrollMode ? 'none' : '';
  }
  if (batchProgressList) {
    batchProgressList.style.display = state.scrollMode ? 'none' : '';
  }
};

/**
 * Attach all event listeners.
 */
const attachEventListeners = () => {
  if (sortModeSelect) {
    sortModeSelect.addEventListener('change', () => {
      state.sortMode = sortModeSelect.value;
      // Reset both modes to first unit since sorting affects entry order in all modes
      state.columnMode.currentUnit = 1;
      state.rowMode.currentUnit = 1;
      saveOverrides();
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
    stageFiveButton.addEventListener('click', async (e) => {
      e.preventDefault();
      await handleAdvanceToStage5();
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

  if (hideCaseOnlyChangesCheckbox) {
    hideCaseOnlyChangesCheckbox.addEventListener('change', () => {
      state.hideCaseOnlyChanges = hideCaseOnlyChangesCheckbox.checked;
      // Reset to first unit since filtered entry count may change
      state.columnMode.currentUnit = 1;
      state.rowMode.currentUnit = 1;
      saveOverrides();
      render();
    });
  }

  if (showUnchangedValuesCheckbox) {
    showUnchangedValuesCheckbox.addEventListener('change', () => {
      state.showUnchangedValues = showUnchangedValuesCheckbox.checked;
      // Reset to first unit since filtered entry count may change
      state.columnMode.currentUnit = 1;
      state.rowMode.currentUnit = 1;
      saveOverrides();
      render();
    });
  }

  if (scrollModeCheckbox) {
    scrollModeCheckbox.addEventListener('change', handleScrollModeChange);
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
  state.scrollMode = reviewState.scroll_mode ?? false;
  // Default to true if not specified (checked by default)
  state.hideCaseOnlyChanges = reviewState.hide_case_only_changes ?? true;
  // Default to false if not specified (unchecked by default)
  state.showUnchangedValues = reviewState.show_unchanged_values ?? false;

  const columnModeState = reviewState.column_mode || {};
  state.columnMode.currentUnit = columnModeState.current_unit || 1;
  state.columnMode.batchSize = columnModeState.batch_size || DEFAULT_COLUMN_BATCH_SIZE;

  const rowModeState = reviewState.row_mode || {};
  state.rowMode.currentUnit = rowModeState.current_unit || 1;
  state.rowMode.batchSize = rowModeState.batch_size || DEFAULT_ROW_BATCH_SIZE;
};

/**
 * Navigate to Stage 5.
 */
const navigateToStage5 = () => {
  advanceMaxReachedStage('review');
  const fileId = getFileIdFromUrl();
  const url = fileId ? `${stageFiveUrl}?file_id=${encodeURIComponent(fileId)}` : stageFiveUrl;
  window.location.assign(url);
};

/**
 * Group items by column for cleaner display.
 */
const _groupByColumn = (items) => {
  const grouped = new Map();
  for (const item of items) {
    if (!grouped.has(item.column)) {
      grouped.set(item.column, []);
    }
    grouped.get(item.column).push(item.value);
  }
  return grouped;
};

/**
 * Show the PV warning dialog with non-conformant values.
 * Groups values by column for cleaner presentation.
 * @param {Object} data - { count: number, items: Array<{column, value, original}> }
 */
const showPVWarningDialog = (data) => {
  const { count, items } = data;

  const dialog = document.createElement('dialog');
  dialog.className = 'pv-warning-dialog';

  const grouped = _groupByColumn(items);
  let groupsHtml = '';

  for (const [column, values] of grouped) {
    const valuesHtml = values
      .map((v) => `<div class="non-conformant-value">"${escapeHtml(v)}"</div>`)
      .join('');

    groupsHtml += `
      <div class="non-conformant-group">
        <h4 class="non-conformant-column">${escapeHtml(column)} <span class="value-count">(${values.length})</span></h4>
        <div class="non-conformant-values">${valuesHtml}</div>
      </div>
    `;
  }

  dialog.innerHTML = `
    <div class="pv-warning-dialog-content">
      <div class="pv-warning-dialog-header">
        <h3 class="pv-warning-dialog-title">Non-Conforming Values Detected</h3>
      </div>
      <div class="pv-warning-dialog-body">
        <p>
          <strong>${count}</strong> value${count === 1 ? '' : 's'} do not match the permissible value set
          for ${count === 1 ? 'its' : 'their'} mapped ontology.
        </p>
        <div class="non-conformant-groups">${groupsHtml}</div>
      </div>
      <div class="pv-warning-dialog-footer">
        <button class="btn-secondary" data-action="return">
          Return to Review
        </button>
        <button class="btn-warning" data-action="proceed">
          Proceed Anyway
        </button>
      </div>
    </div>
  `;

  dialog.querySelector('[data-action="return"]').addEventListener('click', () => {
    dialog.close();
    dialog.remove();
  });

  dialog.querySelector('[data-action="proceed"]').addEventListener('click', () => {
    dialog.close();
    dialog.remove();
    navigateToStage5();
  });

  document.body.appendChild(dialog);
  dialog.showModal();
};

/**
 * Handle advancement to Stage 5 with PV conformance check.
 * @returns {Promise<void>}
 */
const handleAdvanceToStage5 = async () => {
  const fileId = getFileIdFromUrl();
  if (!fileId || !isValidFileId(fileId)) {
    navigateToStage5();
    return;
  }

  try {
    const response = await fetch(`/stage-4/non-conformant/${encodeURIComponent(fileId)}`);
    if (!response.ok) {
      console.warn('Failed to check PV conformance, proceeding anyway');
      navigateToStage5();
      return;
    }

    const data = await response.json();
    if (data.count > 0) {
      showPVWarningDialog(data);
      return;
    }
  } catch (err) {
    console.warn('Error checking PV conformance:', err);
  }

  navigateToStage5();
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
  if (hideCaseOnlyChangesCheckbox) {
    hideCaseOnlyChangesCheckbox.checked = state.hideCaseOnlyChanges;
  }
  if (showUnchangedValuesCheckbox) {
    showUnchangedValuesCheckbox.checked = state.showUnchangedValues;
  }
  if (scrollModeCheckbox) {
    scrollModeCheckbox.checked = state.scrollMode;
  }
  populateBatchSizeOptions();
  updateUIForScrollMode();

  attachEventListeners();

  /* Save any pending overrides on page unload */
  window.addEventListener('beforeunload', () => {
    if (Object.keys(state.pendingOverrides).length > 0) {
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
  const filterOptions = _buildFilterOptions();
  for (const mode of ['column', 'row']) {
    const modeState = mode === 'column' ? state.columnMode : state.rowMode;
    const batchSize = mode === 'column'
      ? modeState.batchSize * modeState.batchSize
      : modeState.batchSize;
    const totalUnits = mode === 'column'
      ? getColumnTotalUnits(state.rows, batchSize, filterOptions)
      : getRowTotalUnits(state.rows, batchSize, filterOptions);

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
    _clampCurrentUnitsToValidRange();
    render();
  }
});

init();
