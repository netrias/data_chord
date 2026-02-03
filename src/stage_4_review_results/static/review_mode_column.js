/**
 * Column-based review mode module.
 * Shows one column at a time with all unique entries displayed in a grid.
 * Batches entries within each column based on configurable grid size.
 */

import {
  rowHasChanges,
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
 * Determine which column indices have at least one non-empty value across all rows.
 * @param {Array} rows - Array of row objects
 * @returns {Set<number>} Set of populated column indices
 */
const _getPopulatedColumnIndices = (rows) => {
  if (!rows.length) return new Set();

  const firstRow = rows[0];
  if (!firstRow?.cells?.length) return new Set();

  const columnCount = firstRow.cells.length;
  const populated = new Set();

  for (let colIdx = 0; colIdx < columnCount; colIdx++) {
    for (const row of rows) {
      const cell = row.cells[colIdx];
      const hasOriginal = cell?.originalValue != null && cell.originalValue !== '';
      const hasHarmonized = cell?.harmonizedValue != null && cell.harmonizedValue !== '';
      if (hasOriginal || hasHarmonized) {
        populated.add(colIdx);
        break;
      }
    }
  }

  return populated;
};

/**
 * Process a single row's cell for a given column, updating the entries map.
 * @param {Object} row - Row object
 * @param {number} colIdx - Column index
 * @param {string} columnKey - Column key identifier
 * @param {Map} entriesByOriginal - Map to accumulate entries by original value
 * @param {Object} [filterOptions] - Filter options passed to cellNeedsReview
 */
const _processRowCellForColumn = (row, colIdx, columnKey, entriesByOriginal, filterOptions = {}) => {
  const cell = row.cells[colIdx];
  // Preserve whitespace - domain rule: whitespace is semantically significant
  const originalValue = cell?.originalValue ?? '';

  // Skip cells without original value (nothing to review)
  if (originalValue === '') return;
  // Skip cells that don't need review
  if (!cellNeedsReview(cell, filterOptions)) return;

  // Skip if we already have this term — manifest provides complete row list
  if (entriesByOriginal.has(originalValue)) {
    return;
  }

  // Use manifest row indices when available (complete list from harmonization);
  // fall back to group sourceRowNumber for backwards compatibility
  const rowIndex = row.sourceRowNumber ?? row.rowNumber;
  const rowIndices =
    cell.manifestRowIndices?.length > 0 ? cell.manifestRowIndices : [rowIndex];
  // manifestRowCount is the true count; rowIndices may be truncated for large arrays
  const rowCount = cell.manifestRowCount ?? rowIndices.length;

  entriesByOriginal.set(originalValue, {
    originalValue: cell.originalValue,
    harmonizedValue: cell.harmonizedValue,
    confidence: cell.confidence,
    bucket: cell.bucket,
    isChanged: cell.isChanged,
    recommendationType: cell.recommendationType,
    manualOverride: cell.manualOverride,
    isPVConformant: cell.isPVConformant,
    pvSetAvailable: cell.pvSetAvailable,
    topSuggestions: cell.topSuggestions ?? [],
    rowIndices,
    rowCount,
    columnKey,
  });
};

/**
 * Build entries for a single column from all changed rows.
 * @param {Array} changedRows - Rows that have changes
 * @param {number} colIdx - Column index
 * @param {Object} columnCell - Cell object containing column metadata
 * @param {Object} [filterOptions] - Filter options passed to _processRowCellForColumn
 * @returns {Object|null} Column object with entries, or null if no entries
 */
const _buildColumnEntries = (changedRows, colIdx, columnCell, filterOptions = {}) => {
  if (!columnCell) return null;

  const columnLabel = columnCell.columnLabel;
  const columnKey = columnCell.columnKey;
  const entriesByOriginal = new Map();

  for (const row of changedRows) {
    _processRowCellForColumn(row, colIdx, columnKey, entriesByOriginal, filterOptions);
  }

  const entries = Array.from(entriesByOriginal.values());
  if (entries.length === 0) return null;

  return {
    columnLabel,
    columnKey,
    columnIndex: colIdx,
    entries,
  };
};

/**
 * Build column-centric data structure that groups cells by unique original value.
 * Returns columns with only entries that have changes (original !== harmonized).
 * @param {Array} rows - Array of row objects
 * @param {Object} [filterOptions] - Filter options passed to rowHasChanges/cellNeedsReview
 * @returns {Array} Array of column objects with entries
 */
const _buildCompactedColumns = (rows, filterOptions = {}) => {
  console.time('_buildCompactedColumns:filter');
  const changedRows = rows.filter((row) => rowHasChanges(row, filterOptions));
  console.timeEnd('_buildCompactedColumns:filter');
  if (!changedRows.length) return [];

  const firstRow = changedRows[0];
  if (!firstRow?.cells) return [];

  console.time('_buildCompactedColumns:buildEntries');
  const populatedIndices = _getPopulatedColumnIndices(changedRows);
  const allColumns = firstRow.cells;
  const columns = [];

  for (let colIdx = 0; colIdx < allColumns.length; colIdx++) {
    if (!populatedIndices.has(colIdx)) continue;

    const column = _buildColumnEntries(changedRows, colIdx, allColumns[colIdx], filterOptions);
    if (column) {
      columns.push(column);
    }
  }
  console.timeEnd('_buildCompactedColumns:buildEntries');

  return columns;
};

/**
 * Get all entries across all columns for scroll mode.
 * Returns a flat array of all entries, sorted by column then by confidence within column.
 * @param {Array} rows - Array of row objects
 * @param {string} [sortMode] - Sort mode for entries within each column
 * @param {Object} [filterOptions] - Filter options
 * @returns {Array} Flat array of all entries with columnLabel attached
 */
export const getAllEntries = (rows, sortMode = SORT_MODE.ORIGINAL, filterOptions = {}) => {
  const columns = _buildCompactedColumns(rows, filterOptions);
  const allEntries = [];

  for (const col of columns) {
    const sortedEntries = sortEntriesByConfidence(col.entries, sortMode);
    for (const entry of sortedEntries) {
      allEntries.push({
        ...entry,
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
 * @param {Array} rows - Array of row objects
 * @param {number} entriesPerBatch - Number of entries per batch
 * @param {Object} [filterOptions] - Filter options passed to _buildCompactedColumns
 * @returns {number}
 */
export const getTotalUnits = (rows, entriesPerBatch = DEFAULT_ENTRIES_PER_BATCH, filterOptions = {}) => {
  const safeBatchSize = Math.max(1, entriesPerBatch);
  const columns = _buildCompactedColumns(rows, filterOptions);
  let total = 0;
  for (const col of columns) {
    total += Math.ceil(col.entries.length / safeBatchSize);
  }
  return total;
};

/**
 * Get all columns with their batch counts for progress display.
 * Sorting is applied before calculating batch boundaries so slicing is consistent.
 * @param {Array} rows - Array of row objects
 * @param {number} entriesPerBatch - Number of entries per batch
 * @param {string} [sortMode] - Sort mode: 'original', 'confidence-asc', 'confidence-desc'
 * @param {Object} [filterOptions] - Filter options passed to _buildCompactedColumns
 * @returns {Array} Array of summary objects for each unit (empty array if no data)
 */
const getColumnSummaries = (rows, entriesPerBatch = DEFAULT_ENTRIES_PER_BATCH, sortMode = SORT_MODE.ORIGINAL, filterOptions = {}) => {
  const safeBatchSize = Math.max(1, entriesPerBatch);
  const columns = _buildCompactedColumns(rows, filterOptions);
  const summaries = [];
  let unitIndex = 0;

  for (const col of columns) {
    // Sort entries before calculating batch boundaries
    const sortedEntries = sortEntriesByConfidence(col.entries, sortMode);
    const batchCount = Math.ceil(sortedEntries.length / safeBatchSize);
    for (let batch = 0; batch < batchCount; batch++) {
      summaries.push({
        unitIndex: unitIndex + 1,
        columnLabel: col.columnLabel,
        columnKey: col.columnKey,
        columnIndex: col.columnIndex,
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
 * @param {Array} rows - Array of row objects
 * @param {number} currentUnit - Current unit index (1-based)
 * @param {number} entriesPerBatch - Number of entries per batch
 * @param {string} [sortMode] - Sort mode: 'original', 'confidence-asc', 'confidence-desc'
 * @param {Object} [filterOptions] - Filter options passed to getColumnSummaries
 * @returns {Object} Batch metadata with entries array
 */
export const getCurrentEntries = (rows, currentUnit, entriesPerBatch = DEFAULT_ENTRIES_PER_BATCH, sortMode = SORT_MODE.ORIGINAL, filterOptions = {}) => {
  const summaries = getColumnSummaries(rows, entriesPerBatch, sortMode, filterOptions);
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

  console.time('renderEntries:cardLoop');
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
  console.timeEnd('renderEntries:cardLoop');

  console.time('renderEntries:domAppend');
  container.append(wrapper);
  console.timeEnd('renderEntries:domAppend');
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
