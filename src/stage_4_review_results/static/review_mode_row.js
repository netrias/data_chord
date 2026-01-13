/**
 * Row-based review mode module.
 * Shows batches of rows, with each row displaying all its changed columns side by side.
 * Uses linear batch numbering (1, 2, 3, ...).
 */

import {
  rowHasChanges,
  getChangedCells,
  createEmptyState,
  createValueCard,
  renderProgressPills,
  toExcelRowNumber,
  cleanupCards,
  getMinConfidence,
  SORT_MODE,
} from './shared_review_utils.js';

/**
 * Build row summaries with only rows that have changes.
 * @param {Array} rows - Array of row objects
 * @param {string} [sortMode] - Sort mode: 'original', 'confidence-asc', 'confidence-desc'
 * @returns {Array} Array of row objects with changedCells and rowIndex added
 */
const _buildRowsWithChanges = (rows, sortMode = SORT_MODE.ORIGINAL) => {
  const changedRows = rows
    .filter(rowHasChanges)
    .map((row) => ({
      ...row,
      changedCells: getChangedCells(row),
      rowIndex: row.sourceRowNumber ?? row.rowNumber,
    }));

  if (sortMode === SORT_MODE.ORIGINAL || !sortMode) {
    return changedRows;
  }

  const ascending = sortMode === SORT_MODE.CONFIDENCE_ASC;
  return changedRows.sort((a, b) => {
    const aMin = getMinConfidence(a.changedCells);
    const bMin = getMinConfidence(b.changedCells);
    return ascending ? aMin - bMin : bMin - aMin;
  });
};

/**
 * Get total number of batches based on batch size.
 * Returns 0 when no changed rows exist to signal empty state to UI.
 * Sorting doesn't affect total count, only order within batches.
 * @param {Array} rows - Array of row objects
 * @param {number} batchSize - Number of rows per batch
 * @returns {number}
 */
export const getTotalUnits = (rows, batchSize) => {
  const changedRows = _buildRowsWithChanges(rows);
  if (!changedRows.length) return 0;
  return Math.ceil(changedRows.length / batchSize);
};

/**
 * Get batch summaries for progress display.
 * @param {Array} rows - Array of row objects
 * @param {number} batchSize - Number of rows per batch
 * @param {string} [sortMode] - Sort mode: 'original', 'confidence-asc', 'confidence-desc'
 * @returns {Array} Array of summary objects for each batch
 */
const getBatchSummaries = (rows, batchSize, sortMode = SORT_MODE.ORIGINAL) => {
  const changedRows = _buildRowsWithChanges(rows, sortMode);
  const summaries = [];

  if (!changedRows.length) {
    return [{
      unitIndex: 1,
      startRow: 0,
      endRow: 0,
      rowCount: 0,
      batchRows: [],
    }];
  }

  for (let start = 0; start < changedRows.length; start += batchSize) {
    const slice = changedRows.slice(start, start + batchSize);
    summaries.push({
      unitIndex: summaries.length + 1,
      startRow: start + 1,
      endRow: start + slice.length,
      rowCount: slice.length,
      batchRows: slice,
    });
  }

  return summaries;
};

/**
 * Get entries for the current batch.
 * Returns a consistent interface with `entries` containing the batch's rows.
 * @param {Array} rows - Array of row objects
 * @param {number} currentUnit - Current batch index (1-based)
 * @param {number} batchSize - Number of rows per batch
 * @param {string} [sortMode] - Sort mode: 'original', 'confidence-asc', 'confidence-desc'
 * @returns {Object} Batch metadata with entries array
 */
export const getCurrentEntries = (rows, currentUnit, batchSize, sortMode = SORT_MODE.ORIGINAL) => {
  const summaries = getBatchSummaries(rows, batchSize, sortMode);
  const totalUnits = summaries.length;
  const safeUnit = Math.min(Math.max(currentUnit, 1), totalUnits);
  const summary = summaries[safeUnit - 1];

  if (!summary || summary.rowCount === 0) {
    return {
      entries: [],
      unitIndex: safeUnit,
      totalUnits,
      summaries,
    };
  }

  return {
    entries: summary.batchRows,
    unitIndex: safeUnit,
    totalUnits,
    summaries,
  };
};

/**
 * Render entries for the current batch into the container.
 * Each row is displayed as a horizontal group of its changed cells.
 * @param {HTMLElement} container - Container element
 * @param {Object} batchMeta - Batch metadata with entries (rows)
 * @param {Object} pendingOverrides - Map of pending overrides
 * @param {Function} onOverrideChange - Callback for override changes
 * @param {Object} [columnPVs] - Map of column_key -> PV list
 */
export const renderEntries = (container, batchMeta, pendingOverrides, onOverrideChange, columnPVs = {}) => {
  cleanupCards(container);
  container.innerHTML = '';

  if (!batchMeta.entries.length) {
    container.append(createEmptyState());
    return;
  }

  const wrapper = document.createElement('div');
  wrapper.className = 'row-mode-wrapper';

  for (const row of batchMeta.entries) {
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
        pendingOverrides,
        onOverrideChange,
        columnPVs,
      });
      cellsEl.append(card);
    }

    rowEl.append(cellsEl);
    wrapper.append(rowEl);
  }

  container.append(wrapper);
};

/**
 * Render progress pills for row mode.
 * Shows batch numbers (1, 2, 3, ...).
 * @param {HTMLElement} container - Container element
 * @param {Object} batchMeta - Batch metadata with summaries
 * @param {number} currentUnit - Current batch index
 * @param {Function} onUnitClick - Callback when batch is clicked
 */
export const renderBatchProgress = (container, batchMeta, currentUnit, onUnitClick) => {
  renderProgressPills({
    container,
    summaries: batchMeta.summaries,
    currentUnit,
    onUnitClick,
    isColumnMode: false,
    getLabelForSummary: (summary) => String(summary.unitIndex),
    getAriaLabelForSummary: (summary, status) => `Batch ${summary.unitIndex}: ${status}`,
  });
};
