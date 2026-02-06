/**
 * Column-based review mode module.
 * Shows one column at a time with all unique entries displayed in a grid.
 * Batches entries within each column based on configurable grid size.
 *
 * Now receives column-centric data directly from backend (no client-side transformation).
 */

import {
  cellNeedsReview,
  createEmptyState,
  createValueCard,
  renderProgressPills,
  toExcelRowNumber,
  cleanupCards,
  sortEntriesByConfidence,
  getFileIdFromUrl,
  SORT_MODE,
} from './shared_review_utils.js';
import { showRowContextPopup } from './row_context_popup.js';

/** Default number of entries per batch when not specified. */
const DEFAULT_ENTRIES_PER_BATCH = 25;

/**
 * Filter transformations based on review options.
 * @param {Array} transformations - Array of Transformation objects
 * @param {Object} [filterOptions] - Filter options passed to cellNeedsReview
 * @returns {Array} Filtered transformations
 */
const _filterTransformations = (transformations, filterOptions = {}) => {
  return transformations.filter((t) => cellNeedsReview(t, filterOptions));
};

/**
 * Get all entries across all columns for scroll mode.
 * Returns a flat array of all entries, sorted by column then by confidence within column.
 * @param {Array} columns - Array of ColumnReviewData objects from backend
 * @param {string} [sortMode] - Sort mode for entries within each column
 * @param {Object} [filterOptions] - Filter options
 * @returns {Array} Flat array of all entries with columnLabel/columnKey attached
 */
export const getAllEntries = (columns, sortMode = SORT_MODE.ORIGINAL, filterOptions = {}) => {
  const allEntries = [];

  for (const col of columns) {
    const filtered = _filterTransformations(col.transformations, filterOptions);
    const sortedEntries = sortEntriesByConfidence(filtered, sortMode);
    for (const entry of sortedEntries) {
      allEntries.push({
        ...entry,
        columnKey: col.columnKey,
        columnLabel: col.columnLabel,
      });
    }
  }

  return allEntries;
};

/**
 * Get total number of navigable units (columns with batches).
 * Returns 0 when no data exists to signal empty state to UI.
 * Sorting doesn't affect total count, only order within batches.
 * @param {Array} columns - Array of ColumnReviewData objects from backend
 * @param {number} entriesPerBatch - Number of entries per batch
 * @param {Object} [filterOptions] - Filter options
 * @returns {number}
 */
export const getTotalUnits = (columns, entriesPerBatch = DEFAULT_ENTRIES_PER_BATCH, filterOptions = {}) => {
  const safeBatchSize = Math.max(1, entriesPerBatch);
  let total = 0;
  for (const col of columns) {
    const filtered = _filterTransformations(col.transformations, filterOptions);
    total += Math.ceil(filtered.length / safeBatchSize);
  }
  return total;
};

/**
 * Get all columns with their batch counts for progress display.
 * Sorting is applied before calculating batch boundaries so slicing is consistent.
 * @param {Array} columns - Array of ColumnReviewData objects from backend
 * @param {number} entriesPerBatch - Number of entries per batch
 * @param {string} [sortMode] - Sort mode: 'original', 'confidence-asc', 'confidence-desc'
 * @param {Object} [filterOptions] - Filter options
 * @returns {Array} Array of summary objects for each unit (empty array if no data)
 */
const getColumnSummaries = (columns, entriesPerBatch = DEFAULT_ENTRIES_PER_BATCH, sortMode = SORT_MODE.ORIGINAL, filterOptions = {}) => {
  const safeBatchSize = Math.max(1, entriesPerBatch);
  const summaries = [];
  let unitIndex = 0;

  for (const col of columns) {
    const filtered = _filterTransformations(col.transformations, filterOptions);
    if (filtered.length === 0) continue;

    // Sort entries before calculating batch boundaries
    const sortedEntries = sortEntriesByConfidence(filtered, sortMode);
    const batchCount = Math.ceil(sortedEntries.length / safeBatchSize);

    for (let batch = 0; batch < batchCount; batch++) {
      summaries.push({
        unitIndex: unitIndex + 1,
        columnLabel: col.columnLabel,
        columnKey: col.columnKey,
        columnIndex: col.sourceColumnIndex,
        batchWithinColumn: batch + 1,
        totalBatchesInColumn: batchCount,
        entryCount: sortedEntries.length,
        startEntry: batch * safeBatchSize,
        endEntry: Math.min((batch + 1) * safeBatchSize, sortedEntries.length),
        sortedEntries, // Include sorted entries for direct access
      });
      unitIndex++;
    }
  }

  return summaries;
};

/**
 * Get entries for the current unit (column + batch within column).
 * @param {Array} columns - Array of ColumnReviewData objects from backend
 * @param {number} currentUnit - Current unit index (1-based)
 * @param {number} entriesPerBatch - Number of entries per batch
 * @param {string} [sortMode] - Sort mode: 'original', 'confidence-asc', 'confidence-desc'
 * @param {Object} [filterOptions] - Filter options
 * @returns {Object} Batch metadata with entries array
 */
export const getCurrentEntries = (columns, currentUnit, entriesPerBatch = DEFAULT_ENTRIES_PER_BATCH, sortMode = SORT_MODE.ORIGINAL, filterOptions = {}) => {
  const summaries = getColumnSummaries(columns, entriesPerBatch, sortMode, filterOptions);
  const totalUnits = Math.max(1, summaries.length);
  const safeUnit = summaries.length > 0
    ? Math.min(Math.max(currentUnit, 1), summaries.length)
    : 1;
  const summary = summaries[safeUnit - 1];

  const emptyResult = {
    entries: [],
    unitIndex: safeUnit,
    totalUnits,
    summaries,
    columnLabel: summary?.columnLabel ?? '',
    batchWithinColumn: summary?.batchWithinColumn ?? 1,
    totalBatchesInColumn: summary?.totalBatchesInColumn ?? 1,
  };

  if (!summary || summary.entryCount === 0) {
    return emptyResult;
  }

  // Use pre-sorted entries from summary (sorted in getColumnSummaries)
  const entries = summary.sortedEntries.slice(summary.startEntry, summary.endEntry).map((entry) => ({
    ...entry,
    columnKey: summary.columnKey,
    columnLabel: summary.columnLabel,
  }));

  return {
    entries,
    unitIndex: safeUnit,
    totalUnits,
    summaries,
    columnLabel: summary.columnLabel,
    batchWithinColumn: summary.batchWithinColumn,
    totalBatchesInColumn: summary.totalBatchesInColumn,
  };
};

/**
 * Render entries for the current unit into the container.
 * Uses a grid layout for column mode.
 * @param {HTMLElement} container - Container element
 * @param {Object} batchMeta - Batch metadata with entries
 * @param {Object} pendingOverrides - Map of pending overrides
 * @param {Function} onOverrideChange - Callback for override changes
 * @param {number} gridSize - Grid dimension (3, 4, or 5 for 3x3, 4x4, 5x5)
 * @param {Object} [columnPVs] - Map of column_key -> PV list
 * @param {number} [totalOriginalRows] - Total rows in original spreadsheet (for row context popup)
 */
export const renderEntries = (container, batchMeta, pendingOverrides, onOverrideChange, gridSize = 5, columnPVs = {}, totalOriginalRows = 0) => {
  cleanupCards(container);
  container.innerHTML = '';

  if (!batchMeta.entries.length) {
    container.append(createEmptyState());
    return;
  }

  const wrapper = document.createElement('div');
  wrapper.className = 'column-mode-grid';
  wrapper.style.setProperty('--grid-columns', gridSize);

  const fileId = getFileIdFromUrl();

  for (const entry of batchMeta.entries) {
    // rowCount is the true count; rowIndices may be truncated for large arrays
    const rowCount = entry.rowCount ?? entry.rowIndices.length;
    const excelRows = entry.rowIndices.map(toExcelRowNumber);
    const rowLabel = rowCount === 1 ? `Row ${excelRows[0]}` : `${rowCount} rows`;
    // Build tooltip: show up to 5 row numbers, then ellipsis if more
    let tooltipText = null;
    if (rowCount > 1) {
      const maxTooltipRows = 5;
      const displayRows = excelRows.slice(0, maxTooltipRows);
      if (rowCount > maxTooltipRows) {
        tooltipText = `Rows: ${displayRows.join(', ')}... (${rowCount} total)`;
      } else {
        tooltipText = `Rows: ${displayRows.join(', ')}`;
      }
    }

    const card = createValueCard({
      entry,
      labelText: rowLabel,
      tooltipText,
      pendingOverrides,
      onOverrideChange,
      columnPVs,
    });

    // Make row label clickable to show context popup
    if (fileId && rowCount > 0) {
      const rowLabelEl = card.querySelector('.entry-row-label');
      if (rowLabelEl) {
        rowLabelEl.classList.add('row-context-link');
        rowLabelEl.addEventListener('click', () => {
          // Pass entry info; popup will fetch full indices if needed
          showRowContextPopup({
            term: entry.originalValue,
            columnKey: entry.columnKey,
            rowIndices: entry.rowIndices.map((idx) => idx - 1),
            rowCount,
            fileId,
            totalOriginalRows,
          });
        });
      }
    }

    wrapper.append(card);
  }

  container.append(wrapper);
};

/**
 * Render progress pills for column mode.
 * @param {HTMLElement} container - Container element
 * @param {Object} batchMeta - Batch metadata with summaries
 * @param {number} currentUnit - Current unit index
 * @param {Function} onUnitClick - Callback when unit is clicked
 */
export const renderBatchProgress = (container, batchMeta, currentUnit, onUnitClick) => {
  renderProgressPills({
    container,
    summaries: batchMeta.summaries,
    currentUnit,
    onUnitClick,
    isColumnMode: true,
    getLabelForSummary: (summary) => {
      const batchSuffix = summary.totalBatchesInColumn > 1
        ? ` (${summary.batchWithinColumn}/${summary.totalBatchesInColumn})`
        : '';
      return summary.columnLabel + batchSuffix;
    },
    getAriaLabelForSummary: (summary, status) => {
      const batchSuffix = summary.totalBatchesInColumn > 1
        ? ` (${summary.batchWithinColumn}/${summary.totalBatchesInColumn})`
        : '';
      return `${summary.columnLabel}${batchSuffix}: ${status}`;
    },
  });
};
